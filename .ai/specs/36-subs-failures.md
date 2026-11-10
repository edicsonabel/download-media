# 36 — Stop a subtitle failure costing the video, skip empty tracks, and clean up after failed items

Status: done
Item: `fix/subs-failures`

> **Decided under the overnight delegation, for the human's morning review:** D1 to D5, each on its recommended option. Every one changes shipped (0.3.0) or 0.4.0-bound behaviour; the list is under "SemVer and the 0.4.0 review list". Nothing is breaking by `AGENTS.md`'s list: no flag, default, path or state format moves.

## Problem

With `-s`, a subtitle yt-dlp can't fetch costs the whole video in single mode, a subtitle with no text embeds as an empty track without a word, and a video that fails leaves its subtitle files behind.

Reproduced on `main` at `bb65124` (`src/` and `tests/` are the same as the container-choice merge `09075a2`), with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14. The real console script (`.venv/bin/download-media`) ran under `env -i` against a scratch `http.server` on `127.0.0.1`, with `HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` and `XDG_DATA_HOME` in a fresh folder per run, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, `LANG=en_US.UTF-8`, stdin from `/dev/null`, an absolute `-o` and no cookies. Each page is `<video src=…><track kind="subtitles" srclang=… src=…></video>`, which yt-dlp's generic extractor reads as subtitles; `tiny.mp4` is a 0.5 s h264/aac clip. A second loopback server sent `cut.vtt` with `Content-Length: 500` and a 44-byte body. Streams were read back with `ffprobe`.

```bash
download-media -y -s -o "$OUT" http://127.0.0.1:18936/subs-404.html       # en → missing.vtt
# [info] Writing video subtitles to: …/subs-404 (1).en.vtt
# ERROR: Unable to download video subtitles for 'en': HTTP Error 404: File not found
# Download failed: http://127.0.0.1:18936/subs-404.html
# exit 1. On disk: nothing. The video was never requested. No history row.

download-media -y -s -o "$OUT" http://127.0.0.1:18936/subs-empty.html     # en → a 0-byte file
# ERROR: Did not get any data blocks
# ERROR: Unable to download video subtitles for 'en': ERROR: Did not get any data blocks
# Download failed …   exit 1, nothing on disk

download-media -y -s -o "$OUT" http://127.0.0.1:18936/subs-404-es.html    # en ok, es → missing.vtt
# Download failed …   exit 1, nothing on disk: the good en track doesn't save the video either

download-media -y -s -o "$OUT" http://127.0.0.1:18937/subs-cut.html       # en cut off at 44 of 500 bytes
# ERROR: [download] Got error: Downloaded 44 bytes, expected 500 bytes
# Download failed …   exit 1. On disk: "subs-cut (1).en.vtt.part"

download-media -y -s -o "$OUT" http://127.0.0.1:18936/subs-garbage.html   # en → 184 bytes of binary junk
# [EmbedSubtitle] Embedding subtitles in "…/subs-garbage (1).mp4"
# Done.   exit 0. ffprobe: h264, aac, mov_text "eng" holding one 2-byte empty sample. No warning.

download-media -y -s -o "$OUT" http://127.0.0.1:18936/subs-novideo.html   # en.vtt ok, video → 404
# ERROR: unable to download video data: HTTP Error 404: File not found
# Download failed …   exit 1. On disk: "subs-novideo (1).en.vtt", alone

download-media -y -s -o "$OUT" http://127.0.0.1:18936/subs-junk.html      # en.vtt ok, video → a text body
# ERROR: Postprocessing: Error opening input files: Invalid data found when processing input
# Download failed …   exit 1. On disk: "subs-junk (1).en.vtt" and the junk "subs-junk (1).mp4"
```

- **What happened:** a subtitle that couldn't be fetched failed the run before the video was requested; an empty subtitle became an empty track; a failed video left its subtitle files.
- **What should happen:** the video is saved and the run names the subtitle it couldn't get, a subtitle with no text isn't embedded and is named, and a failed item leaves none of the subtitle files this run wrote for it.

## Goal

A subtitle problem never costs the video. Each subtitle that couldn't be fetched, or that holds no text, is named after the download. A failed item leaves none of the subtitle files this run wrote for it. The run still ends in `Done.` and exits 0 when the media was saved.

## Findings

"Prototype" means the design below patched in at runtime from a scratch runner that imported `download_media` and called `cli.main`. `src/` wasn't touched. It covered D1, D2's per-language reasons, D3's embed exclusion (video mode) and D4's cleanup. The new messages were printed as plain stderr lines.

### What yt-dlp does (2026.10.26, read in the installed source)

| Fact | Where |
| --- | --- |
| `process_info` writes subtitles **before** it downloads the media, and returns if `_write_subtitles` returns `None` | `YoutubeDL.py:3387-3390` |
| A subtitle that fails to download raises `DownloadError` unless `ignoreerrors is True`. With `True`, it's a `report_warning` (hidden by our `no_warnings`) and the loop goes on to the next language | `YoutubeDL.py:4492-4498` |
| Only the playlist path sets `ignoreerrors` (`downloader.py:373`), so single mode (`-y`, the preview, the wizard, `-P`) aborts the item, and in a carousel the items after it are never fetched | `downloader.py:361-377` |
| Under `ignoreerrors`, a downloader error still goes through `report_error`. The HTTP downloader reports `Did not get any data blocks` for an empty body (`downloader/http.py:327-330`), and `[download] Got error: …` for a cut-off one (`downloader/common.py:416`). The downloader binds `ydl.report_error` when it's created (`downloader/common.py:95-110`), so `_Ydl.report_error` sees both, records them in `ydl.errors`, and the item counts as failed | measured, below |
| After the empty-body report, the downloader returns `False` instead of raising (`http.py:330`), and `_write_subtitles` still sets `filepath` to a file that doesn't exist | `YoutubeDL.py:4489-4491` |
| An inline subtitle (`data`) that can't be written: `report_error`, then `return None`, which ends `process_info` with no download | `YoutubeDL.py:4473-4481` |
| With `overwrites` unset, `existing_file` deletes a subtitle already on disk and the run writes it again (`Deleting existing file …`). The file present after `_write_subtitles` is then this run's copy, but it replaces one an earlier run saved, so the cleanup must not treat it as new (corrected in testing, `4d4f725`; Design 2) | `YoutubeDL.py:3321-3329`; measured on a rerun |
| `FFmpegEmbedSubtitlePP.run` embeds every entry of `requested_subtitles` whose `filepath` exists, in one ffmpeg call, after `-map -0:s`. With none left it prints `There aren't any subtitles to embed` and returns | `postprocessor/ffmpeg.py:588-659` |

The same `_write_subtitles` shape (the `ignoreerrors is not True` branch, `existing_file`, `filepath`), the same `existing_file` and the same `report_error` binding in `downloader/common.py` are in the `pyproject.toml` floor, 2024.10.07 (fetched from GitHub and read, not run).

### Every case, `main` and prototype

| Page, command | `main` | Prototype |
| --- | --- | --- |
| `subs-404`, `-y -s` | `Download failed`, exit 1, nothing saved | `subs-404 (1).mp4`, reason `HTTP Error 404: File not found`, `subs_none`, `Done.`, exit 0, one history row |
| `subs-empty` (0-byte body), `-y -s` | same failure, two `ERROR:` lines | mp4 saved, reason `Did not get any data blocks`, `subs_none`, exit 0, no `ERROR:` line |
| `subs-404-es` (en ok, es 404), `-y -s` | `Download failed`, nothing saved | mp4 with `mov_text` `eng`, `.en.vtt`, reason for `es` only, no `subs_none`, exit 0 |
| `subs-cut`, `-y -s` | `Download failed`, `.en.vtt.part` left | mp4 saved, reason `[download] Got error: Downloaded 44 bytes, expected 500 bytes`, no `.part` left, exit 0 |
| `subs-cut`, `-y -p -s` | **`Download failed`, exit 1, with the mp4 on disk** and the `.part`: the downloader's `report_error` counted the only item as failed (a false failure of spec 29's kind) | mp4 saved, exit 0, no `.part` |
| `subs-404-carousel` (item 1 sub 404, item 2 with en.vtt), `-y -s` | `Download failed` at item 1, item 2 never fetched, nothing saved | both items saved, item 2 with its subtitle, the reason names item 1's file, exit 0 |
| same, `-y -p -s` | both saved, `Done.`, **no word** about item 1's subtitle (item 2 got one, so no `subs_none`) | both saved, item 1's subtitle named |
| `subs-garbage`, `-y -s` | empty `mov_text` track embedded, `Done.`, no warning | no subtitle stream, `.en.vtt` kept, the file named, no `[EmbedSubtitle] Embedding` line |
| `subs-garbage-es` (en ok, es garbage), `-y -s` | both embedded, `es` empty | one `mov_text` `eng`, both `.vtt` kept, `.es.vtt` named |
| `subs-novideo`, `-y -s` | `Download failed`, `.en.vtt` left | `Download failed`, exit 1, nothing left |
| `subs-junk`, `-y -s` | `Download failed`, `.en.vtt` and the junk body left | `Download failed`, exit 1, only the junk body left (row 46's) |
| `subs-novideo-carousel` (item 1 video 404 with en.vtt, item 2 plain), `-y -p -s` | `1 of 2 items downloaded (… 1 failed)`, item 1's `.en.vtt` left | same line, `.en.vtt` gone |
| `subs-404`, `-y -a -s` | `Download failed`, exit 1, nothing saved | follows from the same path; not run on the prototype |
| `subs-ok` twice in one folder | run 2: `Deleting existing file …en.vtt`, rewritten, video `has already been downloaded` | unchanged |

### Telling an empty subtitle from a real one

`ffprobe -v error -count_packets -select_streams s -show_entries stream=nb_read_packets -of csv=p=0 <file>`, on files made by hand:

| File | Output | Exit |
| --- | --- | --- |
| `en.vtt` (one cue), `ok.srt`, a cue with no text | `1` | 0 |
| 184 bytes of binary junk named `.vtt`, a 0-byte `.vtt`, `WEBVTT` with no cue, `WEBVTT` with only a `NOTE` | `N/A` | 0 |
| garbage named `.srt`, TTML (the existing `subs-ttml` case) | `Invalid data found when processing input` | 1 |

The embed step reads each file with the same ffmpeg demuxers, so a file that ffprobe finds no packets in becomes an empty track. In mp4 the muxer writes one 2-byte empty sample for it (measured), so reading the finished video can't tell an empty track from a short one. Reading the subtitle file before the embed can.

## Scope

### In

- In every mode, a subtitle that can't be fetched or written no longer fails the item. The other languages and the media go on (D1).
- After the download, one warning per saved file and failed language, with yt-dlp's reason (D2).
- A subtitle file with no packets is left out of the embed, kept on disk, and named, in video and audio mode (D3).
- When an item counts as failed, the subtitle files this run wrote for it are deleted, and so is a failed subtitle's `.part` file if it wasn't there before (D4).
- Two new messages, `subs_failed` and `subs_empty`, in both blocks.
- One README sentence.

### Out

- **A rerun whose subtitle now fails** loses the earlier subtitle file: yt-dlp deletes it before fetching it again (`existing_file`, Findings). The video and its embedded track stay. This is yt-dlp's overwrite rule, and the tool leaves `overwrites` unset on purpose (spec 01, `_IMAGE_PARAMS`).
- **Subtitles fetched in fragments** (HLS or DASH subtitle tracks). yt-dlp's fragment downloader skips missing fragments with a warning by default, so a partial file counts as written. Not measured, and no fixture has that shape.
- **The junk body a failed video leaves** stays on disk: row 46.
- **A wizard subtitle step:** row 40.
- **`--sub-langs` defaults, auto-captions, format conversion:** unchanged, as spec 21 left them.
- **yt-dlp's `[EmbedSubtitle] There aren't any subtitles to embed` line** on a video with no subtitles at all. It's printed today for such items (measured on `subs-novideo-carousel`'s item 2) and isn't this row's. The new exclusion doesn't add it (Design 4).

## Files

| Action | File | What, and where on `main` (`bb65124`) |
| --- | --- | --- |
| modify | `src/download_media/downloader.py` | `DownloadResult` (`:68-91`): two fields. `_Ydl` (`:155-299`): `_write_subtitles` override, per-item subtitle state, `report_error` (`:288-291`) and a new `report_warning` routing, `process_info` (`:217-235`) cleanup. New `_subtitle_packets` next to `_subtitle_streams` (`:514-521`). `_SubtitlesSeen` (`:524-534`) and `_EmbedSubtitles.run` (`:561-575`). `download()`: `keep` (`:775-780`), the result (`:814-825`) |
| modify | `src/download_media/cookies.py` | rename `_reason` (`:115-119`) to `short_reason`, with its two callers (`:131`, `:142`), so `downloader.py` reuses it |
| modify | `src/download_media/cli.py` | the warning block in `_download_one` after `partial_result` (`:388-399`): two loops |
| modify | `src/download_media/i18n.py` | `subs_failed`, `subs_empty`, next to `subs_none` in both blocks (`:83-87`, `:184-188`) |
| modify | `README.md` | line 15, one sentence |
| modify | `tests/conftest.py` | `_SUBTITLES` (`:330-349`): `header.vtt`. `_subtitle_attack_shape` (`:966-1010`): new pages |
| modify | `tests/test_e2e.py` | the criteria below; `test_a_subtitle_404_still_aborts_a_single_video` (`:6113`) is replaced, `test_a_garbage_subtitle_file_never_fails_the_run` (`:6039`) is extended |
| modify | `tests/test_downloader.py` | `_subtitle_packets` and the routing |
| modify | `tests/test_i18n.py` | only if the parity test needs nothing more; the wording test lives in `test_e2e.py` |

No new dependency, flag, default or on-disk state. `wizard.py`, `probe.py`, `equivalent.py`, `history.py` and `config.py` don't change.

**Items in flight, which merge before this one's development.** Start from `main` after all three, and these are the lines that meet:

- **`fix/m4a-cover`** moves `_TagSource` above `_EmbedCover` in `download()` (`:787-794`) and rewords the comment at `:399`. This item edits `keep` just above and the subtitle block just below (`:799-805`), not those lines.
- **`fix/cookies-undecrypted`** edits `load_browser_cookies` (`:320-351`), adds `jar_failure` to `cookies.py` after `logged_failure` (`:141-143`) and a field to `CookieFailure`. This item doesn't touch `load_browser_cookies`; in `cookies.py` it renames `_reason` on `:115-119`, `:131` and `:142`. If `jar_failure` comes to call `_reason`, rename that call too.
- **`fix/cancel-exits`** reshapes the `except` chain in `_download_one` (`cli.py:369-381`), adds `EXIT_INTERRUPTED` and `_Interrupted` above it (about 13 lines), and wraps `main`. This item only adds two loops to the warning block after `partial_result`, which those lines push down. Its `KeyboardInterrupt` branch is reached after `process_info`'s cleanup (D4) has run.

## Design

Written for the recommended option of each decision. The prototype ran parts 1 to 4.

### 1. A subtitle failure stays the subtitle's (`_Ydl`, D1)

Override `_write_subtitles(self, info_dict, filename)`:

- If `info_dict.get("requested_subtitles")` is empty, or neither `writesubtitles` nor `writeautomaticsub` is set, return `super()._write_subtitles(...)` unchanged (it prints yt-dlp's own `[info] There are no subtitles …`).
- Otherwise, for each `lang, sub` in the original dict, in order:
  - set `info_dict["requested_subtitles"] = {lang: sub}`;
  - note the downloader's scratch file, `Path(subtitles_filename(filename, lang, sub["ext"], info_dict.get("ext")) + ".part")` (`subtitles_filename` from `yt_dlp.utils`; `.part` is `FileDownloader.temp_name`), and whether it exists;
  - with `self.params["ignoreerrors"] = True` and `self.writing_subs = True`, call `super()._write_subtitles(info_dict, filename)`, then restore both in a `finally` (put back the old `ignoreerrors` value, or remove the key if it wasn't there);
  - if `sub.get("filepath")` is an existing file: add the call's return value to the list this override returns, and append the path to `self.item_subs` only if no file was at that path before the call (`4d4f725`, Design 2);
  - otherwise: `sub.pop("filepath", None)`, delete the `.part` file if it wasn't there before, and append `(lang, reason)` to `self.item_subs_failed`.
- Restore `info_dict["requested_subtitles"]` to the original dict in a `finally`, and return the collected list. Never return `None`: a failed language must not end `process_info`.

While `writing_subs` is set:

- `_Ydl.report_error` doesn't append to `self.errors`, and doesn't call `super().report_error`. It records the message in `self.sub_reasons` and calls `self.report_warning(message)`, which `no_warnings` hides. So the downloader's `report_error` neither counts the item as failed nor prints an `ERROR:` line above a `Done.`.
- A new `_Ydl.report_warning` override also records the message in `self.sub_reasons`, then calls `super()`. That's how the `Unable to download video subtitles for 'en': …` warning from `ignoreerrors` reaches us.

`reason` is the first message of `sub_reasons` for that language, passed through `cookies.short_reason` after removing yt-dlp's `f"Unable to download video subtitles for {lang!r}: "` prefix (strip the `ERROR:` labels first, since `short_reason` does that too, then remove the prefix, then `short_reason` again). With no message, use `"?"`. If yt-dlp ever rewords the prefix, the reason keeps it, which is harmless.

Why one call per language: each call's messages belong to one language, so the reason can't be paired with the wrong one. yt-dlp's loop does the same work in the same order. `ignoreerrors=True` is yt-dlp's own branch for this (`YoutubeDL.py:4492-4498`), the one `-p` already takes, scoped to the call the way `_IMAGE_PARAMS` scopes image params to one entry.

Class attributes: `writing_subs = False`. Instance state set in `__init__`: `sub_reasons: list[str]`. Per-item state, reset at the top of `process_info` next to `item_thumbs`: `item_subs: list[str]`, `item_subs_failed: list[tuple[str, str]]`, `item_subs_empty: list[str]`.

### 2. Cleanup of a failed item (`_Ydl.process_info`, D4)

Add `drop_subs()`, like `drop_thumbs()`: delete each path in `item_subs` (`missing_ok=True`), then clear the list. Call it wherever `drop_thumbs()` is called: in the `except BaseException` branch and in the `if self.item_failed()` branch.

A subtitle already on disk before the write is never deleted. With `overwrites` unset, yt-dlp deletes an earlier run's copy and writes it again (Findings), so the file after the call is this run's bytes, but it stands for one the user already had. `_write_subtitles` notes whether the path existed before the call and leaves such a file out of `item_subs` (`4d4f725`, found by the test-engineer: run 1 `-a -s` kept `Clip.en.vtt` next to `Clip.mp3`, run 2 `-s` failed on the video and took it). Only a subtitle file that didn't exist before this run's call is deleted with a failed item. This is spec 30's rule: never delete what this run didn't create.

A subtitle that this run finished from a `.part` an earlier run left is counted as this run's: no file was at its final path before the call. If the item then fails, it's deleted with the item, and the earlier `.part` is gone with it (it became that file). Accepted by the test-engineer and QA: the finished file only exists because of this run, and the alternative, telling a resumed file from a fresh one, would need state yt-dlp doesn't expose. A `.part` that was there before a failed subtitle is kept (`test_a_subtitle_part_file_left_by_an_earlier_run_is_kept`).

### 3. Empty subtitle files (`_subtitle_packets`, `_SubtitlesSeen`, D3)

```python
def _subtitle_packets(path: str) -> int | None:
    # ffprobe -v error -count_packets -select_streams s -show_entries stream=nb_read_packets
    #         -of csv=p=0 <_probe_path(path)>, timeout 15, like _subtitle_streams.
    # None on OSError, timeout, a non-zero exit, or no row (a file with no subtitle stream).
    # Otherwise the sum of the rows, counting N/A as 0.
```

A file is empty when `_subtitle_packets` returns `0`. `None` means ffprobe couldn't read it, or found no subtitle stream (spec 21's TTML and wrong-media cases). Those keep today's path: the embed fails or skips them, and `subs_not_embedded` names the video.

`_SubtitlesSeen.run`, which runs in both modes, also appends each empty file in `_subtitle_files(info)` to `self._downloader.item_subs_empty`. It keeps counting any file as seen, so a URL whose only subtitle is empty gets `subs_empty`, not `subs_none`.

### 4. The embed leaves empty files out (`_EmbedSubtitles.run`)

Before `super().run(info)`: `empty = set(self._downloader.item_subs_empty)`. Put a copy of `requested_subtitles` without the entries whose `filepath` is in `empty` into `info`, and restore the original in a `finally`. `files` becomes the non-empty files, so the existing `streams < len(files)` check stays right. If no file is left, skip `super().run` altogether, which also avoids yt-dlp's `There aren't any subtitles to embed` line. Nothing else in the step changes. The order of post-processors stays as `download()` sets it, with `_SubtitlesSeen` before `_EmbedSubtitles`.

### 5. Reporting (`download()`, `DownloadResult`, `cli.py`)

```python
# DownloadResult
# Per saved file, a subtitle language that couldn't be fetched, and yt-dlp's reason.
subs_failed: tuple[tuple[str, str, str], ...] = ()
# Subtitle files that hold no subtitle text; kept, never embedded.
subs_empty: tuple[str, ...] = ()
```

In `keep(path)`, inside `if not ydl.item_failed():`, extend `subs_failed` with `(Path(path).name, lang, reason)` for each entry of `ydl.item_subs_failed`, and `subs_empty` with `Path(p).name` for each path in `ydl.item_subs_empty`. A failed item reports neither: its failure says enough, and D4 has removed its files.

`cli._download_one`, right after the `partial_result` line and before the `subs_not_embedded` loop:

```python
for name, lang, reason in result.subs_failed:
    warn(t("subs_failed", name, lang, reason))
for name in result.subs_empty:
    warn(t("subs_empty", name))
```

`subs_none` keeps spec 21's rule (D2): it still fires when no saved item got a subtitle file.

### 6. Messages (new keys, both blocks)

| Key | en | es |
| --- | --- | --- |
| `subs_failed` | `{0}: couldn't download the "{1}" subtitles ({2}).` | `{0}: no se pudieron descargar los subtítulos "{1}" ({2}).` |
| `subs_empty` | `{0}: the subtitle file holds no text; it's kept, but not embedded.` | `{0}: el archivo de subtítulos no contiene texto; se conserva, pero no se incrusta.` |

`{1}` is yt-dlp's language code. The quotes keep `"en"` from reading as a word, which matters most in Spanish (`los subtítulos en en`). `subs_empty` names the subtitle file (`Clip.en.vtt`), and `subs_failed` names the saved media, since no subtitle file exists.

### 7. README

Line 15, after "With `-a` they're saved as files only.": `A subtitle that can't be downloaded, or that holds no text, is named in a warning and never stops the video.`

### What the user sees

```
$ download-media -y -s -o ~/tmp/x 'http://127.0.0.1:18936/subs-404-es.html'
…
[info] Writing video subtitles to: …/subs-404-es (1).es.vtt
[info] Writing video subtitles to: …/subs-404-es (1).en.vtt
[download] Destination: …/subs-404-es (1).mp4
[EmbedSubtitle] Embedding subtitles in "…/subs-404-es (1).mp4"
subs-404-es (1).mp4: couldn't download the "es" subtitles (HTTP Error 404: File not found).
Done.
```

## Cases

| Case | Expected |
| --- | --- |
| `-y`, direct preview, `-P`: one subtitle 404, empty body or cut off | Media saved, `subs_failed` once with the reason, `subs_none` if no other subtitle was saved, `Done.`, exit 0, one history row, no `.part`, no `ERROR:` line for the subtitle |
| Same, one language fails and another doesn't | The good one embedded and kept, `subs_failed` for the bad one only, no `subs_none` |
| `-p`, `--playlist-items`: an item's subtitle fails | The item saved and not counted failed, `subs_failed` naming its file. Today: silent, or a false failure for a cut-off body |
| Single mode on a list: item 1's subtitle fails | Every item fetched and saved. Today: stops at item 1, nothing saved |
| Subtitle with no text, video mode | Not embedded, file kept, `subs_empty` naming the file, no `subs_none`, no `subs_not_embedded` when it was the only one |
| Same, next to a good language | The good one embedded, `subs_empty` for the empty one |
| `-a -s`: a subtitle fails, or is empty | `subs_failed` naming the audio file, or `subs_empty` naming the subtitle file; nothing is embedded in audio, as spec 21 D4 keeps it |
| TTML, wrong media as `.vtt`, srt in webm | Unchanged: `subs_not_embedded` (spec 21). `_subtitle_packets` returns `None` for them |
| The video 404s, or is a junk body, after its subtitles were written | `Download failed`, exit 1, no subtitle file left; a junk body stays (row 46) |
| `-p`: a failed item with subtitles | Counted failed as today, its subtitle files deleted, the other items untouched |
| A failed post-processing step under `-p` (spec 29 D6, such as `-p -a` when extraction fails) | The item counts as failed, and its subtitles go with its thumbnails, though the raw file stays |
| Ctrl-C during an item | `process_info`'s `except BaseException` deletes that item's subtitles and thumbnails, then spec 23's interrupted path runs |
| `--cookies` on a page whose subtitle 404s | No cookie-less retry: no `DownloadError` reaches `_download_with_cookie_fallback`. Today: a retry, then `Download failed` |
| Wizard, `-i -s` (no subtitle step, row 40) | As `-y` after the walk: `_download_one` is shared |
| Rerun, same folder | Unchanged (yt-dlp rewrites the subtitle). If the subtitle now fails, see Out |
| Several URLs | Each URL's warnings after its own download, as spec 21 |
| `-l` | Unchanged: no subtitle options |
| Images in a carousel | Unchanged: no subtitles are requested for them |
| `es` and `en` | Both new messages in each language |

## Acceptance criteria

**[fix]** tests fail on a tree with only this item's `src/` changes reverted (`constraints.md`, Verification 5). The developer shows it once, and it isn't repeated after the merge. **[pin]** tests pass before and after. Every e2e test runs `cli.main` on `fake_site` with the real yt-dlp, ffmpeg and ffprobe. "No leftovers" means no name containing `.temp.` or ending in `.part`.

Fixtures (`tests/conftest.py`): `_SUBTITLES` gains `"header.vtt": "WEBVTT\n\n"`. `_subtitle_attack_shape` gains:

- `subs-404-two`: `en.vtt`, `es` → `missing.vtt`
- `subs-short`: `en` → `short/en.vtt` (the existing cut-off route)
- `subs-header`: `en` → `header.vtt`
- `subs-garbage-two`: `en.vtt`, `es` → `garbage.vtt`
- `subs-novideo`: one format at a missing URL, `en.vtt`
- `subs-junk`: one format at `JUNK`, `en.vtt`
- `subs-novideo-carousel`: item `s1` as `subs-novideo`, item `s2` a plain video

The existing `subs-404`, `subs-empty`, `subs-garbage` and `subs-404-carousel` are reused.

- [x] `scripts/pre-release.sh --no-live` passes
- [x] **[fix]** `test_a_failed_subtitle_no_longer_costs_the_video[subs-404|subs-empty|subs-short]` replaces `test_a_subtitle_404_still_aborts_a_single_video`. `-y -s`: exit 0, `names(out) == ["Clip.mp4"]`, exactly one `subs_failed` line, naming `Clip.mp4` and `en`, whose reason holds `404`, `data blocks` or `bytes` respectively. `t("subs_none", "es,en")` once, `t("done")` once, no leftovers, no line starting with `ERROR:` that mentions subtitles, one history row
- [x] **[fix]** `test_one_failed_language_keeps_the_others`. `-y -s` on `subs-404-two`: `Clip.mp4` and `Clip.en.vtt`, `subtitle_streams(Clip.mp4) == [("mov_text", "eng")]`, `subs_failed` for `es` only, no `subs_none`, no `subs_not_embedded`, exit 0
- [x] **[fix]** `test_single_mode_goes_on_past_a_failed_subtitle`. `-y -s` (no `-p`) on `subs-404-carousel`: both items saved, `subs_failed` naming item 1's file, exit 0
- [x] **[fix]** `test_a_cut_off_subtitle_is_not_a_failed_item_under_p`. `-y -p -s` on `subs-short`: exit 0, `Clip.mp4` saved, no `partial_result`, no `t("failed", url)`, no leftovers
- [x] **[fix]** `test_a_subtitle_404_in_playlist_mode_saves_the_video_and_says_so` gains one assertion: `subs_failed` names `01 - Clip [s1].mp4` and `en`. Its `subs_none` count of 1 stays (D2)
- [x] **[fix]** `test_an_empty_subtitle_is_not_embedded[subs-garbage|subs-header|subs-garbage-mkv]` extends `test_a_garbage_subtitle_file_never_fails_the_run` (the mkv row is `subs-garbage` with `--video-format mkv`). The video holds no subtitle stream, `Clip.en.vtt` is kept, `t("subs_empty", "Clip.en.vtt")` once, no `subs_not_embedded`, no `subs_none`, no `[EmbedSubtitle] Embedding` line, exit 0
- [x] **[fix]** `test_an_empty_language_leaves_the_good_one_embedded`. `-y -s` on `subs-garbage-two`: one `mov_text` `eng` stream, both files kept, `t("subs_empty", "Clip.es.vtt")` once, no `subs_not_embedded`
- [x] **[fix]** `test_audio_names_a_failed_or_empty_subtitle[subs-404|subs-garbage]`. `-y -a -s`: `Clip.mp3` saved, exit 0. On `subs-404`, `subs_failed` names `Clip.mp3`. On `subs-garbage`, `t("subs_empty", "Clip.en.vtt")` and the file is kept
- [x] **[fix]** `test_a_failed_video_leaves_no_subtitle_file[subs-novideo|subs-junk]`. `-y -s`: exit 1, `t("failed", url)`, no `.vtt` in `out`. `subs-junk` keeps only its junk body (row 46), `subs-novideo` leaves nothing
- [x] **[fix]** `test_a_failed_item_under_p_leaves_no_subtitle_file`. `-y -p -s` on `subs-novideo-carousel`: `names(out) == ["02 - Clip [s2].mp4"]`, `t("partial_result", 1, 2, 0, 1)`, exit 0
- [x] **[fix]** `test_a_subtitle_404_with_cookies_is_not_retried`. `-y -s --cookies brave` on `subs-404`: exit 0, `Clip.mp4`, `t("cookies_retry", "brave")` not in the output, `fake_site == [("brave",)]`
- [x] **[fix]** `test_wizard_with_subs_survives_a_failed_subtitle`. `-i -s` on `subs-404` with `SINGLE_WALK`: exit 0, `Clip.mp4`, `subs_failed` once
- [x] **[pin]** These pass unchanged: `test_one_broken_language_blocks_the_embed_but_keeps_every_file`, `test_an_embed_that_fails_after_starting_leaves_no_temp_file`, `test_a_container_that_cannot_hold_every_file_warns`, `test_subs_rerun_keeps_one_stream_per_language`, `test_subtitles_embed_in_the_converted_file`, `test_audio_keeps_subtitles_as_files_only`
- [x] **[fix]** `test_subtitle_failure_warnings_in_both_languages[es|en]`: `subs-404` and `subs-garbage` under each locale print the formatted `subs_failed` and `subs_empty`
- [x] Unit `test_downloader.py::test_subtitle_packets_by_what_ffprobe_says`: real ffprobe on `en.vtt` → 1, `garbage.vtt`, `empty.vtt`, `header.vtt` → 0, `en.ttml` and `tiny.mp4` → `None`. A missing ffprobe and a hung one (as `test_no_ffprobe_means_no_media` and `test_a_hung_ffprobe_is_given_up_on` do) → `None`
- [x] Unit `test_downloader.py::test_report_error_outside_subtitles_still_counts`: with `writing_subs` false, `report_error` appends to `errors` as today; with it true, it doesn't, and the message lands in `sub_reasons`
- [x] `subs_failed` and `subs_empty` exist in both blocks (`test_es_and_en_have_same_keys`)
- [x] `grep -n "never stops the video" README.md` matches line 15

Expected flips among existing tests: only `test_a_subtitle_404_still_aborts_a_single_video`, which the first criterion replaces. Any other flip goes in the developer's report with the reason.

Commits, one logical change each, with their tests: `fix(downloader): keep the video when a subtitle can't be fetched` (D1, D2, `cli.py`, `i18n.py`, `cookies.py`, README), `fix(downloader): leave subtitles with no text out of the embed` (D3), `fix(downloader): remove a failed item's subtitle files` (D4).

## Live check

Not needed. Selecting, fetching and embedding subtitles is yt-dlp core code (`_write_subtitles`, the HTTP downloader, `FFmpegEmbedSubtitlePP`), not extractor code, and `fake_site` feeds it real 404s, empty and cut-off bodies, and real files that ffprobe reads. No cookies are involved. An optional smoke test at validation, into `tmp/`, not a gate: `.venv/bin/download-media -y -s -o tmp/subs-failures '<a public video with captions>'` saves the video with `mov_text` streams next to the `.vtt` files and prints no `subs_failed`. That checks the per-language loop on a real extractor's success path.

## SemVer and the 0.4.0 review list

`fix`. Two new messages, which `AGENTS.md` counts as minor; 0.4.0 is already a minor. Not breaking: no flag, default, path or state format changes. Exit codes move from 1 to 0 only where the media was saved, the correction spec 29 made for tags.

If this ships in 0.4.0, add to the review list in `PLAN.md`:

1. D1: a subtitle that can't be fetched (404, empty body, cut off, a rate limit) no longer fails the video in single mode, the preview, the wizard or `-P`. The video is saved with a warning, exit 0, where it was `Download failed`, exit 1, nothing saved. Single mode on a page with several videos no longer stops at that item. With `--cookies`, it no longer triggers the cookie-less retry.
2. D1: under `-p`, a cut-off or empty-bodied subtitle no longer turns a saved video into a failed item. When it was the only item, that was `Download failed`, exit 1, with the file on disk.
3. D2: yt-dlp's `ERROR:` line for a subtitle failure is no longer printed; the new `subs_failed` warning replaces it, with yt-dlp's reason. Under `-p`, a failed subtitle is now named, where it was silent unless no item got a subtitle.
4. D3: a subtitle file with no text (binary junk, a header with no cue, an empty inline subtitle) is no longer embedded as an empty track. It stays on disk and is named by `subs_empty`, in `-a` too.
5. D4: subtitle files written for an item that counts as failed (the video 404s, a junk body, a failed post-processing step under `-p`, Ctrl-C mid-item) are now deleted, as the item's cover thumbnails already were. So is the `.part` file of a subtitle that failed. A subtitle file an earlier run saved at the same path is kept (`4d4f725`), and so is a `.part` an earlier run left next to a subtitle that fails again; a subtitle this run finished from such a `.part` goes with the failed item (Design 2).

Release note: "With `-s`, a subtitle that can't be downloaded no longer costs the video: it's saved, and the run names the subtitle it couldn't get and why. A subtitle file with no text is kept but not embedded as an empty track. A download that fails no longer leaves its subtitle files behind."

## Open decisions

All five were decided by the roles under the overnight delegation, each on the recommended option: D1 A, D2 A, D3 A, D4 A, D5 A. Each one is listed above for the human's review. The rule was the same as specs 21, 29 and 30: lose no data, keep a saved file from reading as a failure, and keep it easy to undo.

### D1: keeping a subtitle failure from failing the video

| Option | Effect |
| --- | --- |
| **A. Scope `ignoreerrors=True` to `_write_subtitles`, one language per call, and route its error reports (recommended)** | Uses yt-dlp's own branch, the one `-p` already takes, for that call only. The downloader's `report_error` no longer counts the item as failed. About 35 lines in `_Ydl`. Prototype: every row of Findings |
| B. Set `ignoreerrors` in single mode too | Changes every error in single mode: a broken video would no longer stop the run, and `DownloadError` would stop reaching the cookie fallback. That's row 08's question, and much wider than this row |
| C. Catch the `DownloadError` around yt-dlp's loop | yt-dlp has already called `report_error`, which prints `ERROR:` and records the error, so the item still counts as failed unless it's rolled back. The loop has stopped too, so the remaining languages are never fetched |
| D. Turn yt-dlp's subtitles off and fetch them separately | Re-implements `_write_subtitles` and its naming: against the wrapper rule |

### D2: what the user is told

| Option | Effect |
| --- | --- |
| **A. One `subs_failed` per saved file and language, with yt-dlp's reason. `subs_none` unchanged, exit 0 (recommended)** | Names what's missing and why (a 429 tells the user to try later). `subs_none` keeps spec 21's rule, so its existing tests stay. Cost: a lone video whose only subtitle fails gets both lines |
| B. As A, but `subs_none` only when no failure was named | One line fewer in that case. Changes a 0.3.0 message's rule, and `test_a_subtitle_404_in_playlist_mode_saves_the_video_and_says_so` |
| C. One line per URL listing the languages | Hides which item lost which subtitle in a carousel |
| D. Keep yt-dlp's `ERROR:` line as well | An `ERROR:` right above `Done.` reads as a failure, which is what spec 29 removed for tags |

### D3: subtitle files with no text

| Option | Effect |
| --- | --- |
| **A. ffprobe packet count; leave it out of the embed, keep the file, name it, in both modes (recommended)** | No empty track in the player's menu. Nothing is deleted. The verdict comes from the same demuxers the embed uses, so leaving the file out never drops a track that would have shown text |
| B. Embed as today and warn | An empty track remains, which some players offer and show nothing for |
| C. Leave it out and delete the file | Deletes on an ffprobe verdict, which spec 29 Out avoids. A format ffprobe misreads would be lost |
| D. As A, video mode only | In `-a` the file is all the user gets, and it would stay unnamed |

### D4: what a failed item leaves

| Option | Effect |
| --- | --- |
| **A. Delete the subtitle files this run wrote for an item that counts as failed, and a failed subtitle's own new `.part` file (recommended)** | Same rule and same places as `drop_thumbs`. Every file deleted is one that wasn't on disk before this run's write (Design 2, after `4d4f725`). The disk matches the report |
| B. Only when the media download itself failed | Keeps subtitles next to a junk body or a `-p -a` item whose extraction failed. Two rules for "failed" |
| C. Leave them, as today | A `.vtt` with no video, after `Download failed` |

### D5: the README

| Option | Effect |
| --- | --- |
| **A. One sentence on line 15 (recommended)** | Tells `-s` users the video is never lost to a subtitle |
| B. No README change | The behaviour change is only in the release notes |

## Validation

On `main` at `e54e5c2` (merge `e447bea`, the first merge with `kind-edges` in it, then `.ai/` only: `git diff --stat e447bea e54e5c2 -- src tests README.md` is empty), yt-dlp 2026.10.26, ffmpeg n9.0.2, Python 3.14.9. GitHub CI on `e54e5c2` was still running when this was written.

- **Gate:** not re-run. The tech-lead's `scripts/pre-release.sh --no-live` on the merge `e447bea`: `All checks passed.`, exit 0, 2526 passed, 9 deselected, 11 xfailed. No file outside `.ai/` has changed since.
- **Criteria tests:** a `pytest -k` selection over `test_e2e.py`, `test_downloader.py` and `test_i18n.py` covering every test the criteria name, `[fix]` and `[pin]`, plus the test-engineer's subtitle tests (`test_a_failed_video_never_takes_a_subtitle_an_earlier_run_saved`, `test_a_subtitle_part_file_left_by_an_earlier_run_is_kept`): 121 passed on `main`. `test_a_subtitle_404_still_aborts_a_single_video` and `test_a_garbage_subtitle_file_never_fails_the_run` are gone, replaced as the criteria ask. `grep -n "never stops the video" README.md` matches line 15. The revert proof of the `[fix]` tests is the developer's and the test-engineer's, not repeated (criteria preamble).
- **Scope:** the merge touches exactly the Files table: `README.md`, `cli.py`, `cookies.py`, `downloader.py`, `i18n.py`, `conftest.py`, `test_downloader.py`, `test_e2e.py` (`test_i18n.py` needed nothing). No flag, default, path, dependency or version change.
- **Console check:** `main`'s real console script, `.venv/bin/download-media`, under `env -i`, `LANG=en_US.UTF-8` (and `es_ES.UTF-8` where noted), `PATH=/usr/bin:/bin`, a fresh `HOME` and all four `XDG_*` folders per run in the scratchpad, `XDG_VIDEOS_DIR`/`XDG_MUSIC_DIR` unset, stdin from `/dev/null`, an absolute `-o`, no cookies. A scratch `http.server` on `127.0.0.1` served an ffmpeg-made `tiny.mp4`, a one-cue `en.vtt`, 184 random bytes as `garbage.vtt`, `cut.vtt` (`Content-Length: 500`, 41-byte body) and one `<video><track kind="subtitles" srclang="en">` page per case. Streams read with ffprobe. Everything was deleted afterwards.

| Run | Output | Exit | Disk (streams), history |
| --- | ------ | ---- | ----------------------- |
| `-y -s`, subtitle 404 | `S404 (1).mp4: couldn't download the "en" subtitles (HTTP Error 404: File not found).`, `subs_none`, `Done.`; no `ERROR:` line | 0 | `S404 (1).mp4` (h264, aac), 1 row; on `bb65124` this was `Download failed`, nothing saved |
| same, `es_ES.UTF-8` | `… no se pudieron descargar los subtítulos "en" (HTTP Error 404: File not found).` | 0 | the mp4 |
| `-y -p -s`, cut-off subtitle | `subs_failed` with `[download] Got error: Downloaded 41 bytes, expected 500 bytes`, `subs_none`, `Done.`; no `partial_result`, no `Download failed` | 0 | `Scut (1).mp4` only, no `.part`, 1 row |
| `-y -s`, junk `.vtt` | `Sgarb (1).en.vtt: the subtitle file holds no text; it's kept, but not embedded.`, `Done.`; no `[EmbedSubtitle] Embedding` | 0 | `Sgarb (1).mp4` (h264, aac, no subtitle stream), `Sgarb (1).en.vtt`, 1 row |
| same, `--video-format mkv` | same warning | 0 | `Sgarb (1).mkv` (h264, aac), the `.vtt` |
| same, `-a` | same warning | 0 | `Sgarb (1).mp3`, the `.vtt` |
| same, `es_ES.UTF-8` | `… el archivo de subtítulos no contiene texto; se conserva, pero no se incrusta.` | 0 | the mp4, the `.vtt` |
| `-y -s`, video 404 after its `en.vtt` was written | `ERROR: unable to download video data: HTTP Error 404 …`, `Download failed` | 1 | nothing, 0 rows; on `bb65124` the `.vtt` stayed |
| same, `-y -p -s` | same | 1 | nothing, 0 rows |
| Two runs, one folder: `-y -a -s`, then the video removed from the server, `-y -s` | run 1 `Done.`; run 2 `Deleting existing file …en.vtt`, `Download failed` | 0, 1 | after run 2: `Stwo (1).mp3`, `Stwo (1).en.vtt` (its cue intact). `4d4f725`'s case holds |

- **Cases, tests only:** single mode on a carousel, one bad language next to a good one, `--cookies` with no retry, the wizard walk, `-p` on a carousel with a failed item, the empty-body and header-only subtitles, and the resumed `.part` are criterion or test-engineer tests in the selection above; they weren't run by hand here. The optional live smoke test (Live check) wasn't run: it isn't a gate, and the human is away.

### Notes from review, no change made

1. **Design 2 was out of date** after `4d4f725` ("every file in `item_subs` is this run's"). Corrected above, with the Findings row, Design 1's bullet and D4 A's effect.
2. **The resumed `.part` judgement** (test-engineer, QA): a subtitle finished in this run from an earlier run's `.part` is deleted with a failed video. Recorded in Design 2 as accepted.
3. **`subs_empty`'s wording under `-a`** (QA): `it's kept, but not embedded` reads as if embedding were an option, though `-a` never embeds (spec 21 D4). Seen in the console check. Wording only, both blocks; for the planner, next to `i18n-leftovers`.
4. **A `getattr` inconsistency** (QA): `_EmbedSubtitles.run` reads `getattr(self._downloader, "item_subs_empty", ())` (`downloader.py:699`) while `_SubtitlesSeen.run` uses `self._downloader.item_subs_empty` directly (`:669`). Harmless, since `_Ydl.__init__` always sets it; a cleanup for whoever next edits the step.
