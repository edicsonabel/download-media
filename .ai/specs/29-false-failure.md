# 29 — Stop reporting a saved file as a failed download

Status: approved
Item: `fix/false-failure`

## Problem

yt-dlp's metadata step can't write tags to some files. When that happens the tool says `Download failed` and exits 1, though the file is complete on disk.

Reproduced on `main` at `8f830bb`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. The real console script ran against a scratch loopback server under `env -i`, with `HOME` and `XDG_*` in a temp folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, an absolute `-o`, no clipboard and no cookies. The server sends `/v/<f>` as `video/<ext>`, `/a/<f>` as `audio/<ext>` and `/o/<f>` as `application/octet-stream`. `/p/<name>.html` pages hold `<video>` or `<audio>` tags. Streams were checked with ffprobe.

```bash
# vid.ogx is 0.2 s of Theora and Vorbis.
download-media -y -o "$OUT" http://127.0.0.1:18977/v/vid.ogx
# [Metadata] Adding metadata to ".../vid.ogx"
# ERROR: Postprocessing: Error opening output files: Invalid argument
# Download failed: http://127.0.0.1:18977/v/vid.ogx
# exit 1. On disk: vid.ogx, audio and video. No history row.
```

- **What happened:** the file was saved, and the run reported a failure.
- **What should happen:** the run reports a success, and says the file has no metadata and what that means for a rerun. A body that isn't media must still be reported as a failure.

## Goal

When the media is on disk and only its tags couldn't be written, the user gets `Done.`, exit 0, and one warning that names the file. `-p` counts every item once. A file in an `<audio>` tag with no extension gets the type step again, which spec 27 put off until this item.

## Findings

Every result below comes from a run. "Prototype" means the design below applied to a scratch copy of `src/download_media` (about 55 changed diff lines). `src/` itself wasn't touched.

### Which files fail, and how (`main`, `-y`)

| Source                                                                            | ffmpeg error (from yt-dlp)              | On disk after the run                         | Exit |
| --------------------------------------------------------------------------------- | --------------------------------------- | --------------------------------------------- | ---- |
| `.ogx` video (`video/ogx`)                                                        | `Error opening output files: Invalid argument` | the `.ogx`, audio and video            | 1    |
| `.weba`: a renamed `.mp4` (`video/weba`), and real Opus-in-WebM audio (`audio/weba`) | same                                 | the file, streams intact                      | 1    |
| **Real audio** `.m4r`, `.f4a`, `.f4b` (AAC in mp4, `audio/*`)                     | same                                    | the file, audio                               | 1    |
| `.mp4` renamed `.alac` or `.ape` (`audio/*`)                                      | same                                    | the file, audio and video                     | 1    |
| Extensionless octet-stream link to an `.mp4` (`/o/noext`)                         | same                                    | `noext.unknown_video`, audio and video        | 1    |
| `.mp4` renamed `.oga`, `.spx`, `.aiff`, `.wav` (`audio/*`)                        | `Conversion failed!`                    | the file, plus an **empty `vid.temp.<ext>`**  | 1    |
| A 17-byte text file served as `video/mp4` (`junk.mp4`)                            | `Error opening input files: Invalid data found when processing input` | `junk.mp4`, no streams | 1 |

Two classes of ffmpeg failure: no muxer for the extension, or a muxer that can't take the streams. Both are output-side, and both leave the downloaded file untouched. Only the junk row is a real failure: ffmpeg can't read the input.

Real audio is affected too, not only renamed files: `.weba`, `.m4r`, `.f4a` and `.f4b` are real formats.

### `-p`, single mode on a list, and `--cookies` (`main`)

| Command                                                                       | Output                                                              | On disk                    | Exit |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------- | -------------------------- | ---- |
| `-y -p` on a page with `<video>` on `vid.ogx` and on `tiny.mp4`              | `2 of 3 items downloaded (0 without downloadable media, 1 failed).`, `Done.` | both files, intact | 0    |
| `-y` (single mode) on the same page                                          | `Download failed`                                                   | only the `.ogx`: **the mp4 was never downloaded** | 1 |
| `-y -p` on a page with `junk.mp4` and `tiny.mp4`                             | `2 of 3 items downloaded (… 1 failed).`, `Done.`                    | both                       | 0    |
| `-y -p` on a page with two junk bodies                                        | `2 of 4 items downloaded (… 2 failed).`, **`Done.`**                | both junk files            | **0** |
| `-y --cookies brave` on a `fake_site` item at `theora.ogx` (cookies stripped by the fixture) | `Failed with brave cookies. Retrying without cookies...`, `Download failed` | `Clip.ogx` **and `Clip [x].ogx`** | 1 |

- **The double count** comes from `run_pp` (`YoutubeDL.py:3803-3815`). With `ignoreerrors=True`, which `-p` sets (`downloader.py:89`), a post-processor error is reported and swallowed, so `process_info` goes on to the post hooks (`:3666-3671`). The item lands in both `saved` and `ydl.errors`.
- **The last `-p` row is a total failure that reads as success.** `AGENTS.md` says `-p` must not do that.
- **The cookie row:** the metadata error raises `DownloadError`, so `_download_with_cookie_fallback` (`cli.py:323-334`) downloads again without cookies. `_AvoidNameClash` finds an untagged file of the same name and adds `[x]`. The run then fails again and leaves two copies. This was measured with a scratch pytest using the repo's `fake_site` fixture, not with real cookies.
- **Single mode without `ignoreerrors`:** the error propagates from `process_info` (`:3662-3665`) and aborts the rest of the list.

### Reruns (`main`, `-y` three times on real `.weba` audio in the same home)

| Run | Files                            | Exit |
| --- | -------------------------------- | ---- |
| 1   | `aud.weba`                       | 1    |
| 2   | `aud.weba`, `aud [aud].weba`     | 1    |
| 3   | the same two (`has already been downloaded`) | 1 |

`_AvoidNameClash` (`downloader.py:181`) compares the existing file's `purl`/`comment` tag with the URL. No tag was ever written, so the second run takes the file for another video's and saves a copy. The third run finds `aud [aud].weba` and stops there. The copies stop at two.

### The wizard (`main`)

- Direct `.ogx`: `Detected: generic / Video`, six Enters, `Download failed`, exit 1, `Videos/vid.ogx` with both streams. `test_wizard_keeps_an_ogx_and_reports_the_metadata_failure` pins this today.
- `<audio src="/o/noext">` with an **`.mp4` behind it**: `Detected: generic / Audio`, no type step, `Deleting original file ….unknown_video`, `Music/audio-noext.html (1).mp3`, **audio only**, `Done.`, exit 0. The video is lost. This is the case spec 27 left out of its rule at `probe.py:199` until this item lands.

### `-a`

`-y -a` on `vid.ogx`: `vid.mp3`, `Done.`, exit 0. The metadata step runs on the `.mp3`, so `-a` is never affected with the four `-f` formats (`mp3`, `m4a`, `opus`, `wav`), all of which ffmpeg can tag.

### The prototype

| Command                                         | Result                                                                                             |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `-y` on `vid.ogx`, `aud.weba`, `vid.wav`, `/o/noext` | exit 0, the warning naming the file, `Done.`, one file, streams intact, one history row. **No `vid.temp.wav`** |
| `-y` on `junk.mp4`                              | Unchanged: `Download failed`, exit 1, no warning                                                   |
| `-y -p` on the ogx + mp4 page                  | exit 0, both files, no `N of M` line, the warning once for the `.ogx`                              |
| `-y` (single) on the same page                 | exit 0, **both files**                                                                             |
| `-y -p` on the junk + mp4 page                 | `1 of 2 items downloaded (0 without downloadable media, 1 failed).`, `Done.`, exit 0               |
| `-y -p` on the all-junk page                   | `Download failed`, exit 1                                                                          |
| `--cookies brave` on the `fake_site` `.ogx`    | No retry, `Clip.ogx` only, exit 0                                                                  |
| `-y` three times on `aud.weba`                 | 1, 2, 2 files, as on `main`, now exit 0 each time, 3 history rows                                  |
| Wizard, direct `.ogx`, six Enters              | `Video`, the warning, `Done.`, exit 0, `Videos/vid.ogx`                                            |
| Wizard, `<audio src="/o/noext">` on an `.mp4`  | `Audio`, spec 27's note and type step, Enter: `Videos/audio-noext.html (1).unknown_video` with **audio and video**, the warning, `Done.`, exit 0 |

A narrow pytest selection was run against the prototype: `tests/test_downloader.py`, `tests/test_probe.py`, and `tests/test_e2e.py -k "ogx or no_extension or partial or carousel or rerun or twice or subtitle"`. It gave 619 passed, 2 xfailed and 4 failed. The 4 are expected and listed under Acceptance criteria. The subtitle and rerun tests passed, so the source-URL tag is still written, and embedding still keeps it.

## Scope

### In

- Catch the metadata step's error when the downloaded file still holds audio or video. The run then counts the item as saved and warns once per file. The step's leftover `.temp.<ext>` and `.meta` files are removed.
- Under `-p`, an item whose post-processing failed counts as failed only, never as saved as well.
- One new message, `untagged`, in both blocks.
- `probe.py:199-204`: `unknown_video` rejoins spec 27's label rule.
- Tests: loopback pages and `fake_site` shapes, the real yt-dlp and ffmpeg.

### Out

- **Writing the source URL somewhere else** so a rerun recognises an untagged file (xattrs, a sidecar file). See D5 C. That would be new on-disk state, and it's a roadmap row if wanted.
- **Renaming `.unknown_video`** to an extension that plays everywhere. That's a container decision (row 30's area). Spec 27's D2 already accepted the raw file as served.
- **Removing a non-media body left on disk after a real failure** (`junk.mp4`, in single mode and under `-p`). It's left today and stays after this item. Deleting on an ffprobe verdict risks deleting media if ffprobe misbehaves. Noted for the planner.
- **The history row on a rerun that saved nothing new** (run 3 above). That's existing behaviour for every already-downloaded file.
- **Row 08** (single mode stopping at a broken item). This item makes single mode carry on only past a file that *was* saved. A real failure still stops it.
- **Row 25** (`.ogg`/`.opus` reruns): a different tag-reading bug in `_source_url`, untouched.
- **`-a`:** unaffected (see Findings). Only pinned.

## Files

| Action | File                              |
| ------ | --------------------------------- |
| modify | `src/download_media/downloader.py` |
| modify | `src/download_media/cli.py`       |
| modify | `src/download_media/i18n.py`      |
| modify | `src/download_media/probe.py`     |
| modify | `tests/conftest.py`               |
| modify | `tests/test_e2e.py`               |
| modify | `tests/test_downloader.py`        |
| modify | `tests/test_probe.py`             |

No new dependency, flag, default or on-disk state. `equivalent.py`, `wizard.py` and `history.py` don't change. Possible collisions: row 25 (`_source_url`), row 28 (`download()` and `_download_one`), row 30 (`build_ydl_opts` post-processors), and row 36 (`downloader.py`, `ignoreerrors`).

## Design

Written for the recommended option of each decision. The prototype produced every "after" result above.

### `downloader.py`

**1. A metadata step that tells "untagged" from "broken"** (D1 A, D2 A). It follows the pattern of `_EmbedSubtitles`:

```python
def _has_media_streams(path: str) -> bool:
    # ffprobe -v error -show_entries stream=codec_type -of csv=p=0 <path>, timeout 15:
    # True only on returncode 0 with an "audio" or "video" line. False on OSError/timeout.


class _TagSource(FFmpegMetadataPP):
    """yt-dlp's metadata step, except that a file ffmpeg can't tag still counts as saved."""

    def __init__(self, downloader: Any, untagged: list[str]) -> None: ...

    @classmethod
    def pp_key(cls) -> str:
        return "Metadata"  # keep the [Metadata] tag and the ffmpegmetadata args key

    def run(self, info):
        try:
            return super().run(info)
        except PostProcessingError:
            path = info["filepath"]
            Path(prepend_extension(path, "temp")).unlink(missing_ok=True)
            Path(replace_extension(path, "meta")).unlink(missing_ok=True)  # chapters file
            if not _has_media_streams(path):
                raise
            self._untagged.append(Path(path).name)
            return [], info
```

- **Why the file is intact:** `FFmpegMetadataPP.run` writes to `<name>.temp.<ext>` and calls `os.replace` only after ffmpeg succeeds (`postprocessor/ffmpeg.py:702-708`). On any failure the downloaded file is untouched. Its `.meta` chapters file (`:683-685`) is deleted only on success, so a failure leaves it behind.
- **Why ffprobe and not the error text:** the text differs between the two output-side classes (`Error opening output files` and `Conversion failed!`), and between ffmpeg versions. ffprobe measures the one thing that matters: whether the file holds media. A missing or failing ffprobe re-raises, which is today's behaviour, so the fallback is never a false success.
- `run` keeps `_restrict_to(images=False)`, because it calls `super().run`.
- **Wiring:** `build_ydl_opts` stops listing `{"key": "FFmpegMetadata"}`. Video mode keeps `"postprocessors": []`, so `test_subs_leave_the_postprocessors_alone` still indexes it. `download()` adds `_TagSource(ydl, untagged)` with `when="post_process"` right after building `_Ydl`, before `_SubtitlesSeen` and `_EmbedSubtitles`. `add_post_processor` appends after the params' post-processors (`YoutubeDL.py:946`, spec 21), so the order stays the same in both modes: audio is `ExtractAudio`, `EmbedThumbnail`, `Metadata`; video is `Metadata`, then the subtitle steps. Update the comment at `downloader.py:307`.

**2. Count each item once under `-p`** (D6 A). In `_Ydl`:

```python
item_errors = 0

def process_info(self, info_dict):
    self.item_errors = len(self.errors)
    return super().process_info(info_dict)
```

In `download()`, the post hook replaces `saved.append` and is added with `ydl.add_post_hook` once `ydl` exists. It appends to `saved` only if `len(ydl.errors) == ydl.item_errors`, and otherwise to a local list that isn't counted. In `process_info`, the only reported error that still reaches the post hook is a post-processor error swallowed by `run_pp` under `ignoreerrors`. Every other `report_error` there is followed by `return` (`YoutubeDL.py:3332-3671`), and subtitle errors under `ignoreerrors=True` are warnings (`:4493-4497`). So the change only reclassifies items that single mode already reports as `Download failed`. The existing `returncode != 0 and not saved` check then turns an all-failed `-p` run into a `DownloadError`.

**3. `DownloadResult` gains `untagged: tuple[str, ...] = ()`.**

### `cli.py`

In `_download_one`, next to the `subs_not_embedded` loop: `for name in result.untagged: warn(t("untagged", name))`. Then `Done.`, history and the printed command run as for any success. Nothing else changes. `_download_with_cookie_fallback` no longer sees an exception for these files, so it no longer retries.

### `i18n.py`

| Key        | en                                                                                                             | es                                                                                                                         |
| ---------- | -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `untagged` | `{}: saved, but ffmpeg couldn't write its metadata, so a rerun won't recognize it and may save another copy.` | `{}: guardado, pero ffmpeg no pudo escribir sus metadatos, así que al repetir la descarga no se reconocerá y puede guardarse otra copia.` |

"May" is deliberate: the second run saves a copy, but later runs don't (Findings, Reruns). The prototype used a shorter wording that the third run made untrue.

### `probe.py`

Delete `and ext != "unknown_video"` from `_audio_by_label_only` (`:204`), and delete the comment above it (`:199-200`) that ties it to this row.

**What changes:** a labelled audio format with no usable extension now raises `may_hold_video`. In practice that means an `<audio src>` without an extension, or a site extractor with that shape. The wizard shows spec 27's note and the type step, defaulting to video:

- Enter saves the raw file as `<title>.unknown_video` in `~/Videos`, with this item's warning, `Done.`, exit 0. A video inside is kept, where today it's converted and lost (Findings, The wizard).
- **Cost:** real audio there now needs `2` for an `.mp3` in `~/Music`. Enter gives a `.unknown_video` file that holds the audio, which most players open by sniffing the content. Spec 27 committed to this trade (its "Changes after approval"). See D7.

Direct links aren't affected: yt-dlp takes the extension from the `Content-Type` when the URL has none (`generic.py:865`), so they never come back as `unknown_video` under an `audio/*` label.

### What the user sees

```
[download] Destination: …/vid.ogx
[Metadata] Adding metadata to "…/vid.ogx"
vid.ogx: saved, but ffmpeg couldn't write its metadata, so a rerun won't recognize it and may save another copy.
Done.

Equivalent command to repeat this download:
  download-media -y -o … http://…/vid.ogx
```

No `ERROR:` line: yt-dlp only prints it from `report_error`, which is no longer reached.

### yt-dlp behaviour relied on

Confirmed in the 2026.10.26 source. At the 2024.10.07 floor it was checked by reading the source on GitHub, not by a run:

- `FFmpegMetadataPP.run`: temp file, then `os.replace`; `@_restrict_to(images=False)`; `pp_key` derived as `Metadata` (`ffmpeg.py:662-709`, `common.py:61-64`). The floor has the same structure.
- `run_pp` reports and returns on `PostProcessingError` only when `ignoreerrors is True`, and re-raises otherwise (`YoutubeDL.py:3803-3815`). The same at the floor.
- `process_info` calls `pre_process(info_dict, 'video')` at its start (`:3355`). After `post_process` it catches `PostProcessingError`, then calls each post hook with `filepath` (`:3660-3671`). The same at the floor.
- `add_post_processor` appends after the params' post-processors (`:946`).

## Cases

| Case                                                                                     | Expected after the fix                                                                                       |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `-y`, direct preview, `-P`: a single file ffmpeg can't tag (every row of the first Findings table but junk) | exit 0, one file, streams intact, `untagged` warning, `Done.`, one history row, no `.temp.*` or `.meta` |
| Same, a body that isn't media                                                            | Unchanged: `Download failed`, exit 1, no warning, no history row. The body stays on disk (Out)               |
| Wizard, `-i`: the same files                                                             | As `-y`, after the walk. The wizard doesn't change; `_download_one` is shared                                |
| `-p`, `--playlist-items`: a list with one untaggable item                                | Every item saved, no `N of M` line, one warning per untaggable item, exit 0                                  |
| `-p`: a list with one non-media item                                                     | `1 of 2 items downloaded (… 1 failed)`, `Done.`, exit 0. Today: `2 of 3`                                     |
| `-p`: every item fails post-processing                                                   | `Download failed`, exit 1, no history row. Today: `Done.`, exit 0                                            |
| Single mode on a list with an untaggable first item                                      | Every item saved, exit 0. Today: stops after the first, exit 1                                               |
| `-a`, any `-f`                                                                           | Unchanged: the step runs on the converted file, which ffmpeg can tag                                         |
| `-s` on an untaggable file                                                               | The `untagged` warning, then spec 21's `subs_not_embedded` (yt-dlp only embeds in mp4, webm, mkv, mov). Follows from the code, not run |
| `--cookies`                                                                              | No cookie retry, one file. Today: a retry, a second copy, exit 1                                             |
| Without cookies                                                                          | As the rows above                                                                                            |
| `-l`                                                                                     | Unchanged: nothing is downloaded                                                                             |
| Rerun of an untaggable file                                                              | Second run: a copy `X [id].ext`, exit 0, the warning. Later runs: no new file. D5                            |
| Rerun of a taggable file                                                                 | Unchanged: the tag is written and recognised, one file                                                       |
| Wizard, `<audio src>` with no extension                                                  | Spec 27's note and type step. Enter: raw `.unknown_video` in `~/Videos`, warning, exit 0. `2`: `.mp3` in `~/Music` |
| Several URLs, one of them untaggable                                                     | That URL counts as ok, so the run exits 0 if the others do                                                   |
| `es` and `en`                                                                            | The warning in each language                                                                                 |

## Acceptance criteria

**[fix]** criteria need a test that fails without the fix, which the developer shows by reverting it (`constraints.md`, Verification 5). **[pin]** criteria pass before and after. Tests that can reach a default folder use the `home` fixture. "No leftovers" means no name containing `.temp.`, ending in `.meta` or ending in `.part` in the output folder.

Fixtures, in `tests/conftest.py`:

- **Media:** an audio-only WebM, by stream copy from `tiny.webm` (`-vn -c:a copy`, no new encoder). A short text file to serve as a non-media body; an existing `_SUBTITLES` file will do.
- **URLs through the existing `/typed/<mime>/<file>/<name>` route:** `audio/webm` → `clip.weba`; `audio/mp4` on `tiny.m4a` → `clip.m4r` and `clip.f4a`; `audio/x-wav` and `audio/ogg` on `tiny.mp4` → `clip.wav` and `clip.oga` (the `Conversion failed!` class, which leaves a temp file today); `application/octet-stream` on `tiny.mp4` → `stream` (gives `.unknown_video`); `video/mp4` on the text file → `junk.mp4`.
- **Generic pages:** `<video>` on `theora.ogx` then `tiny.mp4`; `<video>` on the junk URL then `tiny.mp4`; two junk `<video>` tags (two distinct junk URLs); `<audio src="typed/application/octet-stream/tiny.mp4/stream">`.
- **A `fake_site` shape** with one format at `theora.ogx` and `ext` `ogx`, and a variant with `chapters`.

Real yt-dlp, ffmpeg and ffprobe, loopback:

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] **[fix]** `-y`, parametrized over direct `theora.ogx` and the typed `clip.weba`, `clip.m4r`, `clip.f4a`, `clip.wav`, `clip.oga` and `stream`. Expect exit 0; exactly one file in `out`, holding the expected streams; `t("untagged", name)` and `t("done")` in the output; `t("failed", url)` not; no leftovers; one history row
- [ ] **[fix]** `test_wizard_keeps_an_ogx_and_reports_the_metadata_failure` becomes a success test on the same two sources, `video-ogx.html` and `theora.ogx`, with `SINGLE_WALK`: exit 0, `t("done")` and `t("untagged", saved)` in the output, the same file in `~/Videos` with a video stream
- [ ] **[fix]** `-y -p` on the ogx + mp4 page: exit 0, two files, both with a video stream; `partial_result` never said; `t("untagged", …)` exactly once, naming the `.ogx`; one history row
- [ ] **[fix]** `-y` without `-p` on the same page: exit 0, two files
- [ ] **[fix]** `-y -p` on the junk + mp4 page: `t("partial_result", 1, 2, 0, 1)` and `t("done")` in the output, exit 0, `untagged` never said
- [ ] **[fix]** `-y -p` on the all-junk page: exit 1, `t("failed", url)` in the output, `t("done")` not, no history row
- [ ] **[pin]** `-y` on the direct junk URL: exit 1, `t("failed", url)`, `untagged` never said, no history row
- [ ] **[fix]** `-y` three times on the typed `clip.weba`, in one output folder: exit 0 and `t("done")` each time; one file after run 1, two after run 2, still two after run 3
- [ ] **[fix]** `fake_site` `.ogx` shape, `-y --cookies brave`: exit 0, only `Clip.ogx`, `t("cookies_retry", "brave")` not in the output, and `fake_site == [("brave",)]`
- [ ] **[fix]** `fake_site` `.ogx` shape with chapters, `-y`: exit 0, no leftovers, no `Clip.meta`
- [ ] **[fix]** `test_wizard_enter_on_real_audio_with_no_extension_in_an_audio_tag_succeeds` is replaced. On `audio-no-ext.html`: `t("audio_may_hold_video")` in the output; `SINGLE_WALK` gives exit 0, one `.unknown_video` file in `~/Videos`, `t("untagged", …)`, `~/Music` empty; answering `2` at the type step gives one `.mp3` in `~/Music`
- [ ] **[fix]** The wizard on the `<audio>` page holding `tiny.mp4` with no extension, `SINGLE_WALK`: one `.unknown_video` in `~/Videos` with `"video"` in `streams()`, exit 0. Today: an `.mp3`, video gone
- [ ] **[pin]** `-y -a` on `theora.ogx`: one `.mp3`, exit 0, `untagged` never said
- [ ] **[pin]** `-y` on `tiny.mp4`: `_source_url` of the saved file equals the URL, so the tag is still written. The existing rerun tests (`test_wizard_twice_on_a_file_labelled_as_audio_keeps_one_file` and the like) and the subtitle-embed tests pass unchanged

Unit:

- [ ] **[fix]** `tests/test_probe.py`: the `unknown_video` row of `test_audio_known_only_by_a_label_may_hold_a_video` expects `True`
- [ ] `tests/test_downloader.py`: `build_ydl_opts` lists no `FFmpegMetadata` in either mode, and video mode still has a `postprocessors` key
- [ ] `untagged` is in both the `es` and the `en` blocks

The pre-existing tests that change are these four flips seen on the prototype, and no others in the selection run: the two `test_wizard_keeps_an_ogx_and_reports_the_metadata_failure` rows, `test_wizard_enter_on_real_audio_with_no_extension_in_an_audio_tag_succeeds`, and the `unknown_video` row of `test_audio_known_only_by_a_label_may_hold_a_video`. The rest of the suite wasn't run against the prototype. Any other flip must be explained in the developer's report.

Commits: `fix(downloader): count a saved file ffmpeg can't tag as saved` (steps 1 and 3, `cli.py`, `i18n.py`), `fix(downloader): count each -p item once` (step 2), and `fix(probe): flag unknown_video audio as possibly video` (once the first lands). Each comes with its own tests.

## Live check

Not needed. The behaviour sits in yt-dlp's post-processing and in ffmpeg, both run for real on loopback. No site extractor and no cookies are involved.

## SemVer

`fix`. Nothing breaking: no flag, default or on-disk path changes. Exit codes change from 1 to 0 when the file was saved, and from 0 to 1 for an all-failed `-p` run. Both are corrections. One new message, which `AGENTS.md` counts as a minor. 0.3.0 is already a minor, so the next version doesn't change.

**Release note (for the planner):** "Files that ffmpeg can't write tags to (`.ogx`, `.weba`, `.m4r`, `.f4a`, `.f4b`, links with no extension, renamed files) were reported as `Download failed` with exit 1, though they were saved. They now end in `Done.`, with a warning that the file has no metadata and that running the same download again may save a second copy. `-p` no longer counts such items twice. A `-p` run in which every item fails now exits 1 instead of saying `Done.`. In the wizard, an `<audio>` tag on a file with no extension now shows the video/audio choice; Enter keeps the file as served."

## Open decisions

Resolved by the roles under the human's delegation, each on the recommended option: D1 A, D2 A, D3 A, D4 A, D5 A, D6 A, D7 A. D6 (an all-failed `-p` run now exits 1) and D7 (extensionless `<audio>` gets the type step) change shipped behaviour; the human reviews.

The human delegated these to the roles. Each one has a recommendation, chosen to lose no data and to be easy to undo.

### D1: telling "saved, untagged" from a real failure

| Option                                                           | Effect                                                                                                                   |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **A. Catch the step's error, then ffprobe the file (recommended)** | A file holding audio or video counts as saved. Anything else re-raises, as today. Measures the file, and ffprobe is already used twice in `downloader.py` |
| B. Catch every metadata error                                    | A non-media body (`junk.mp4`) reads as `Done.`: a failure reading as success                                            |
| C. Match ffmpeg's error text                                     | Two different texts today (`Error opening output files`, `Conversion failed!`), and no guarantee across ffmpeg versions |

**Recommendation: A.**

### D2: where to handle it

| Option                                                 | Effect                                                                                                                                                     |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Catch it in a subclass of the step (recommended)** | Covers any file ffmpeg can't tag, today's list and any later one. Same pattern as `_EmbedSubtitles`. About 30 lines                                       |
| B. Skip the step up front for a list of extensions     | Can't cover renamed files: a real `.wav` tags fine and a renamed `.mp4` as `.wav` doesn't, so the extension can't decide. The list would also have to be kept by hand |
| C. Retry the download without the step                 | Downloads the file twice, and the retry meets the first file under `_AvoidNameClash`: two copies, as the cookie retry shows today                          |

**Recommendation: A.**

### D3: what the user is told, and the exit code

| Option                                                     | Effect                                                                                                                         |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **A. One warning per file, `Done.`, exit 0 (recommended)** | Matches spec 21's choice for subtitles (warnings, not failures). The warning says what was skipped and what it means for a rerun |
| B. `Done.` with no warning                                 | Hides that the tags were skipped, and the rerun copy comes as a surprise                                                      |
| C. Keep exit 1 with a softer message                       | A script retries a download that worked, and gets the duplicate                                                               |

**Recommendation: A.** The exact wording is in Design and easy to change.

### D4: what counts as "saved"

| Option                                                     | Effect                                                                                    |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **A. Any audio or video stream (recommended)**             | A renamed video under `.wav` counts, which is what the user has on disk                   |
| B. Also require the streams to match the extension's kind  | Turns the renamed files back into failures with the file still on disk                    |

**Recommendation: A.**

### D5: reruns, when the source-URL tag can't be written

| Option                                                                   | Effect                                                                                                                                                                                         |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Accept one extra copy on the second run, and say so (recommended)** | Measured: copies stop at two. No new state. Nothing is ever skipped by mistake                                                                                                                |
| B. Treat an untagged file of the same name as the same download          | No copy, but a different video with the same title (Instagram's "Video by <user>") is silently not saved. That's the bug `_AvoidNameClash` exists for (`downloader.py:159-165`)              |
| C. Store the URL elsewhere: yt-dlp's `XAttrMetadata` (`user.xdg.referrer.url`), read back in `_source_url` | Uses yt-dlp's own feature, but xattrs fail on vfat and exfat drives, are lost on copy, and add on-disk state. Worth a roadmap row if reruns matter                    |
| D. A sidecar file next to each download                                  | New state and clutter in the user's folders                                                                                                                                                    |

**Recommendation: A.** It loses nothing, and B and C can still be added later.

### D6: `-p` items with a post-processing error

| Option                                                         | Effect                                                                                                                                                                   |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A. Count them as failed only, for any step (recommended)**   | About 6 lines. `-p` agrees with single mode, which already calls them failures. An all-failed `-p` run exits 1. It's needed anyway, because D1's re-raise for non-media lands in the same double count |
| B. Fix only the metadata case (the catch alone)                | Non-media bodies stay `2 of 3`, and an all-junk page still says `Done.`, exit 0                                                                                          |

**Recommendation: A.** One edge: if `EmbedThumbnail` or `ExtractAudio` fails under `-p -a`, that item now counts as failed only, though a file stays on disk. Single mode already says `Download failed` for it. Not measured.

### D7: `unknown_video` in the audio-label rule

| Option                                         | Effect                                                                                                                                                          |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Put it back, as spec 27 committed (recommended)** | An `<audio src>` with no extension gets the type step. Enter keeps the raw `.unknown_video` with any video inside. Real audio there needs `2` for an `.mp3` |
| B. Leave it out                                | Real audio keeps its shorter flow. A video in such a tag is still converted and lost on Enter (measured)                                                       |

**Recommendation: A.** It loses no data, and it's a one-line revert.
