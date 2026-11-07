# 21 — Make `-s` embed subtitles as documented, and let `--sub-langs` turn them on

Status: draft
Item: `fix/subs-as-documented`

> **For the human's review: D1, D2 and D3 change shipped behaviour.** The roles decided them under the overnight delegation. D1: with `-s`, video files now hold the subtitles as streams, and the `.vtt`/`.srt` files users get today stay next to them. In mp4, ffmpeg always marks the first embedded language as the default track, so some players may show it straight away. D2: `--sub-langs` on its own now downloads subtitles. D3: two new warnings, and the exit status stays 0. All three are easy to undo and none loses a file. See Open decisions and SemVer.

## Problem

`-s` never embeds subtitles, although `README.md:14`, `README.md:78` and `docs/index.html:421` say it does. `--sub-langs` without `-s` downloads none and still says `Done.`. A run that finds no subtitles in the requested languages also says `Done.`, and the only trace is yt-dlp's own English `[info]` line.

Reproduced on `main` at `be78a3c`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. The real console script (`.venv/bin/download-media`) ran against a loopback `http.server` serving a tiny h264/aac mp4, a vp8/vorbis webm, `en.vtt`, `es.vtt`, `fr.vtt`, `en.srt` and `en.ttml`. It also served pages for yt-dlp's real generic extractor, which reads `<video><track kind="subtitles" srclang=…>` as subtitles. `HOME` and every `XDG_*` pointed at a scratch folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` were unset, `-o` was absolute and `LANG=en_US.UTF-8`. Streams were read back with `ffprobe`.

```bash
download-media -y -s -o <out> http://127.0.0.1:<port>/subs.html          # en + es tracks
# [info] subs-1: Downloading subtitles: es, en
# Done.   exit 0
# on disk: "subs (1).mp4"  video:h264 audio:aac      <- no subtitle stream
#          "subs (1).es.vtt", "subs (1).en.vtt"

download-media -y --sub-langs en -o <out> http://127.0.0.1:<port>/subs.html
# Done.   exit 0, on disk: "subs (1).mp4" only, no subtitle file, no stream
# printed: download-media -y -o <out> <url>          <- --sub-langs dropped

download-media -y -s -o <out> http://127.0.0.1:<port>/subs-fr.html       # fr track only
# [info] There are no subtitles for the requested languages
# Done.   exit 0, on disk: the mp4, nothing else

download-media -y -s --video-format webm -o <out> http://127.0.0.1:<port>/subs-webm.html
# Done.   on disk: "subs-webm (1).webm" vp8+vorbis, no subtitle stream; "subs-webm (1).en.vtt"

download-media -y -a -s -o <out> http://127.0.0.1:<port>/subs.html
# Done.   on disk: "subs (1).mp3", "subs (1).es.vtt", "subs (1).en.vtt"

download-media -y -p -s -o <out> http://127.0.0.1:<port>/subs-multi.html # item 1 has en, item 2 none
# Done.   on disk: item 1 mp4 + .en.vtt, item 2 mp4; no subtitle stream in either
```

- **What happened:** `-s` saves sidecar files only. `--sub-langs` alone is ignored, and so is the user's flag in the printed command. Missing subtitles pass silently.
- **What should happen:** `-s` puts the subtitles inside the video and keeps the files it saves today. `--sub-langs` turns subtitles on. A run that couldn't save any subtitle, or couldn't embed one, says so.

## Goal

What `-s` and `--sub-langs` do matches the README, the landing page and `--help`, and a run that asked for subtitles says when none were saved or embedded.

## Findings

Each claim names how it was checked: **real** is the console script on loopback, as above. **Prototype** is the same script with the design below patched in from a scratch wrapper (`src/` untouched). **In-process** is a scratch script driving `YoutubeDL` with a fake extractor and split video/audio formats.

### The cause

`build_ydl_opts` sets `"postprocessors": [{"key": "FFmpegEmbedSubtitle"}]` at `downloader.py:100`. Both branches below it then assign `postprocessors` again (`:109` audio, `:138` video), so the embed step never reaches yt-dlp. `"embedsubtitles": True` (`:99`) is a yt-dlp **command-line** option: `YoutubeDL` never reads it (no `embedsubtitles` in `YoutubeDL.py`; it is only read by `get_postprocessors` in `yt_dlp/__init__.py:674`). `test_subs_enables_subtitle_options` (`tests/test_downloader.py:67`) asserts that dead key, which is why the bug passed. `--sub-langs` has `default=DEFAULT_SUB_LANGS` (`cli.py:94`), and `options_from_args` sets `subs=args.subs` only (`cli.py:129`).

The project has always promised embedding. The README line dates from the first Python commit (`6fd8fdd`). The Bash prototype passed `--write-subs --write-auto-subs --sub-langs … --embed-subs --convert-subs srt`, and `--sub-langs` set `SUBS=1` (`legacy/bin/download-media:474`, `:772-779`).

### yt-dlp behaviour the design relies on

Confirmed in the installed source, and in the `pyproject.toml` floor (2024.10.7, wheel downloaded into a scratch folder and read, not run): the class, `SUPPORTED_EXTS`, `already_have_subtitle`, `-map -0:s` and `_restrict_to` have the same shape there.

| Fact | Where | Checked |
| --- | --- | --- |
| `FFmpegEmbedSubtitlePP(already_have_subtitle=False)` returns the subtitle files for deletion unless `already_have_subtitle` is true. yt-dlp's CLI passes `writesubtitles and keep_subs`, so `--write-subs --embed-subs` keeps them | `postprocessor/ffmpeg.py:584`, `:658`; `__init__.py:674-681` | source; prototype with `already_have_subtitle=True` keeps both `.vtt` |
| It only embeds into `mp4 mov m4a webm mkv mka`. Any other ext prints `Subtitles can only be embedded in …` and returns with nothing changed | `ffmpeg.py:582`, `:590` | source |
| mp4/mov/m4a get `-c:s mov_text`. webm takes only WebVTT and skips other formats with a warning. JSON is skipped. ASS in mp4 warns | `ffmpeg.py:220`, `:620-632` | source; real/prototype: mp4 → `mov_text`, webm + vtt → `webvtt`, srt → `mov_text` in mp4 |
| Images are skipped (`_restrict_to(images=False)`) | `ffmpeg.py:588`, `common.py:115` | source |
| It drops existing subtitle streams first (`-map -0:s`), so a rerun doesn't duplicate tracks | `ffmpeg.py:643` | prototype rerun: still 2 tracks, same names, no `[id]` |
| An ffmpeg failure raises `PostProcessingError`. Without `ignoreerrors`, `process_info` reports it as an error and the run raises `DownloadError` | `YoutubeDL.py:3803-3812` | prototype with a TTML track: `Download failed`, exit 1, **with the mp4 and `.en.ttml` on disk**. Under `-p`: `2 of 3 items downloaded (… 1 failed)` on a 2-item page |
| One embed call covers every language, so one bad file blocks all of them for that video | `ffmpeg.py:637-656` | in-process: TTML + `es.vtt`, neither embedded |
| No subtitles selected: `process_subtitles` returns `None`; `_write_subtitles` prints `[info] There are no subtitles for the requested languages`; the per-language `… subtitles not available` is a `report_warning`, hidden by our `no_warnings` | `YoutubeDL.py:3166-3197`, `:4453` | real (`subs-fr.html`, `nosubs.html`) |
| `_write_subtitles` sets `sub_info['filepath']` only for a file it wrote or found | `YoutubeDL.py:4461-4492` | source |
| `add_post_processor` appends after the `postprocessors` from params, so a custom step added there runs after `FFmpegMetadata` | `YoutubeDL.py:946` | in-process: embedding after `FFmpegMetadata` keeps `purl`/`comment` (mkv: both, mp4: `comment`), which `_source_url` reads for reruns |
| The mp4 muxer always enables the first subtitle track. `-disposition:s 0` doesn't stop it, and `postprocessor_args` can't either. mkv and webm leave it off | ffmpeg n9.0.2 | direct ffmpeg: mp4 `mov_text,1` even with `-disposition:s 0`; mkv `webvtt,0` |
| Merged split streams (`bv*+ba`) embed into the merged file | — | in-process: `--video-format mkv` → 2 `webvtt` streams; mp4 → 2 `mov_text` |

### Other paths, read or run

- **Wizard:** there is no subtitle step. `subs` is never set by a step (`grep subs src/download_media/wizard.py` is empty). `run_all` starts each walk from the command-line options (`wizard.py:500`, `replace(opts, …)`), so `-i -s` and `-i --sub-langs …` carry subtitles through untouched and unseen. A plain wizard run never asks for subtitles.
- **Equivalent command:** `equivalent.py:51-54` emits `-s` whenever `subs` is on, and `--sub-langs` only when it isn't the default, quoted, with `=` for a leading `-` (spec 09).
- **`-a -s`:** the audio branch replaces `postprocessors` too, so today it saves the mp3 and the `.vtt` files and embeds nothing (real, above). mp3, opus and wav can't hold subtitles. m4a could hold `mov_text`, but yt-dlp would have to run the embed step after `FFmpegExtractAudio`.
- **Subtitle 404:** in single mode a subtitle that fails to download aborts the item **before** the video: `Download failed`, exit 1, nothing saved (real, `subs-404.html`). Under `-p` (`ignoreerrors`) it becomes a hidden warning, and the run saves the video with `Done.`. A video 404 after the subtitle was written leaves a stray `.en.vtt` (real, `subs-novideo.html`). Both happen today and are out of scope (see Notes).

## Scope

### In

- Embed subtitles into the saved video with `-s`, and keep the subtitle files (D1).
- `--sub-langs` turns subtitles on (D2).
- If embedding fails or can't hold every file, say so, keep the files, and don't count the item as failed (D3b).
- If subtitles were asked for and none was saved for a URL, warn once, exit 0 (D3a).
- `--help`, `README.md` and the dead `embedsubtitles` key match the result.

### Out

- **A wizard subtitle step** (and showing subtitles in the wizard summary). It's a feature, not this fix. Noted for the planner.
- **Embedding into audio** (`-a -f m4a`). D4 keeps today's files-only behaviour.
- **A subtitle 404 that aborts the video in single mode**, and **the stray `.vtt` after a failed video.** Both happen today, are unrelated to embedding, and are noted for the planner.
- **Converting subtitle formats** (the prototype's `--convert-subs srt`). It would change the sidecar files users get today.
- **Choosing which languages download by default** (`es,en` plus auto-captions, including YouTube's auto-translated ones). Unchanged.
- **The container choice** (`--video-format mkv` on a single-format mp4 source still saves `.mp4`, which `real` showed). That's row 30.

## Files

| Action | File | What |
| --- | --- | --- |
| modify | `src/download_media/downloader.py` | drop `embedsubtitles` and the overwritten `postprocessors` from the subs block; embed step + subtitle report; two `DownloadResult` fields |
| modify | `src/download_media/cli.py` | `--sub-langs` default `None`, implies `subs`; `--help` for `-s` and `--sub-langs`; the two warnings in `_download_one` |
| modify | `src/download_media/i18n.py` | `subs_none`, `subs_not_embedded` in `es` and `en` |
| modify | `src/download_media/equivalent.py` | the comment at `:52` only (it says `--sub-langs` alone doesn't enable subtitles) |
| modify | `README.md` | lines 14, 100-101 |
| modify | `tests/conftest.py` | subtitle files in `media_url`, subtitle pages on `FakeSiteIE` |
| modify | `tests/test_e2e.py`, `tests/test_downloader.py`, `tests/test_cli.py`, `tests/test_wizard_flow.py` | criteria below |
| modify | `.ai/specs/PLAN.md` | row 21 status |

`docs/index.html:421` ("With embedded subtitles", `-s --sub-langs en,es`) becomes true under D1 and needs no change. It would need one if the human picks D1-C.

Collisions: `downloader.py` is also touched by rows 28, 29 and 30, `cli.py` `--help` strings by row 02, and `conftest.py` fixtures by row 34.

## Design

### 1. yt-dlp options (`build_ydl_opts`)

The `if opts.subs:` block keeps only `writesubtitles`, `writeautomaticsub` and `subtitleslangs`, as today. Remove `"embedsubtitles"` and `"postprocessors"` from it. Neither branch changes its own `postprocessors`.

### 2. The embed step (`download`, video branch only)

When `opts.subs` and not `opts.audio_only`, add a subclass of `yt_dlp.postprocessor.ffmpeg.FFmpegEmbedSubtitlePP` with `ydl.add_post_processor(…)` (`when="post_process"`), constructed with `already_have_subtitle=True` so the files stay. It runs after `FFmpegMetadata`, which keeps the `purl`/`comment` tags (in-process). Its `run`:

1. Collects the subtitle files yt-dlp wrote for the item: `requested_subtitles` entries whose `filepath` is an existing file.
2. Calls `super().run(info)`. On `PostProcessingError` it removes yt-dlp's temp file if it exists (`prepend_extension(filepath, "temp")`) and carries on as if nothing was embedded. It must never re-raise, or the item becomes `Download failed` while the video sits on disk (prototype).
3. If there was at least one file, it counts the subtitle streams in `info["filepath"]` with `ffprobe -v error -select_streams s -show_entries stream=index -of csv=p=0`, bounded like `_source_url`. If there are fewer streams than files, it records the file name for D3b. This one rule covers an ffmpeg failure, a container outside `SUPPORTED_EXTS`, srt in webm and JSON. If ffprobe can't run, it records nothing.
4. Returns `[], info`.

The embed step keeps yt-dlp's language tags and order: the first language in `--sub-langs` comes first.

### 3. The subtitle report (both branches)

When `opts.subs`, `download` also records whether **any** item that reached `post_process` had at least one subtitle file (step 2.1's test). In the video branch, the embed step can do this. In the audio branch, use a small `PostProcessor` like `_PlaylistCount`. `DownloadResult` gains:

```python
subs_missing: bool = False              # subtitles requested, something saved, no subtitle file at all
subs_not_embedded: tuple[str, ...] = () # file names holding fewer subtitle streams than files
```

### 4. CLI

- `--sub-langs` gets `default=None`. `options_from_args` sets `subs=args.subs or args.sub_langs is not None` and `sub_langs=DEFAULT_SUB_LANGS if args.sub_langs is None else args.sub_langs`. An explicit `--sub-langs es,en` turns subtitles on too.
- `--help`: `-s` → `"Download subtitles, embed them in videos and keep them as files."`. `--sub-langs` → `f"Subtitle languages, comma-separated; implies -s (default: {DEFAULT_SUB_LANGS})."`. Both stay hardcoded English, like the rest of `--help` until row 02.
- In `_download_one`, after a run that saved something (after `partial_result`, before `Done.`), print one `warn(t("subs_not_embedded", name))` per name, then `warn(t("subs_none", effective.sub_langs))` if `result.subs_missing`. Neither changes the outcome, the exit status or the history row.
- `equivalent.py` is unchanged apart from the comment. It keeps emitting `-s`, which the 0.2.0 binary needs, so the printed command works on both versions.

### 5. Messages (both blocks, new keys)

| key | en | es |
| --- | --- | --- |
| `subs_none` | `No subtitles in {} could be saved; the media was saved without them.` | `No se pudo guardar ningún subtítulo en {}; el contenido se guardó sin ellos.` |
| `subs_not_embedded` | `{}: not every subtitle could be embedded; they are all kept as separate files.` | `{}: no se pudieron incrustar todos los subtítulos; todos se conservan como archivos aparte.` |

### 6. Docs

- `README.md:14` → `- **Subtitles** — \`-s\` embeds them in the video and keeps them as files next to it (\`.vtt\`, \`.srt\`, …); \`--sub-langs es,en,fr\` picks the languages and turns subtitles on. With \`-a\` they're saved as files only.`
- `README.md:100-101` → `-s, --subs   Subtitles, embedded in videos and kept as files (default: es,en)` / `--sub-langs <langs>   Comma-separated subtitle languages (implies -s)`.
- `README.md:78` (`-s --sub-langs en … # English subtitles embedded`) and `docs/index.html:421-424` stay as they are: both become true.

## Cases

"Today" is `main` at `be78a3c`, from real runs. "After" is the design (prototype where marked).

| Case | Today | After |
| --- | --- | --- |
| `-y -s`, mp4, subs in es+en | mp4 without streams, 2 `.vtt` | mp4 with `mov_text` `spa`, `eng` (first marked default by the muxer), 2 `.vtt` kept (prototype) |
| `-y -s --video-format mkv`, merged source | 2 `.vtt` only | mkv with 2 `webvtt` streams, 2 `.vtt` (in-process) |
| `-y -s --video-format webm`, webm + vtt | `.vtt` only | `webvtt` stream + `.vtt` (prototype) |
| webm + srt, or a saved ext outside `SUPPORTED_EXTS` | sidecar only | sidecar only, plus `subs_not_embedded` |
| TTML/other undecodable track | sidecar, `Done.` | sidecar, `subs_not_embedded`, `Done.`, exit 0. Not `Download failed` (the naive embed's regression, prototype) |
| `--sub-langs en` without `-s` | no subtitles, `Done.`, flag dropped from the command | en saved and embedded; command prints `-s --sub-langs en` |
| `-s`, no subs in the languages (fr only, none) | `Done.` + yt-dlp `[info]` | same, plus `subs_none` with `es,en`; exit 0 |
| `-a -s` (mp3, m4a, opus, wav) | audio + `.vtt` | unchanged: no embed step in the audio branch, no `subs_not_embedded` |
| `-a -s`, no subs | `Done.` | `subs_none` |
| direct (preview + confirm) | as `-y` | as `-y` after the confirmation |
| `-p -s` carousel, item 1 with subs, item 2 without | `.vtt` for item 1 | item 1 embedded + `.vtt`, item 2 plain, no `subs_none` (something got subtitles) |
| `-p -s` carousel, one item's track undecodable | — (naive embed: counted as `failed`) | `subs_not_embedded` for that item, no `partial_result` failure, all items saved |
| `-p -s`, carousel with images | images skipped by the embed step | same; `subs_none` only if no item got a subtitle file |
| `-P` / single mode on a carousel | as `-y` | as `-y` |
| wizard, no flags | no subtitles | unchanged |
| `-i -s`, `-i --sub-langs en` | `-s`: sidecars; `--sub-langs`: nothing | subtitles on, embedded; the wizard still neither asks nor shows them |
| rerun of `-s` | same files | same files, still one stream per language, no `[id]` duplicate (prototype) |
| `--cookies`, cookie fallback retry | — | the retry's result carries the subtitle report; nothing cookie-specific |
| total failure (nothing saved) | `Download failed` | unchanged; no subtitle warning |
| subtitle 404, single mode | `Download failed`, nothing saved | unchanged (out of scope) |
| subtitle 404, `-p` | video saved, silent | video saved, `subs_none` |
| `-l` | no subtitle options | unchanged |
| multi-URL | — | each URL gets its own warnings right after its download |

## Acceptance criteria

Every criterion is an automated test unless it says otherwise. The e2e ones run `cli.main` on `fake_site` with the real yt-dlp and ffmpeg, and read streams back with `ffprobe`. `media_url` gains real `en.vtt`, `es.vtt`, `en.srt` and `en.ttml` files (text, no new encoder). `FakeSiteIE` gains pages whose entries carry `subtitles` and/or `automatic_captions` pointing at them. Use a merged-format page for mkv and a `tiny.webm` page for webm. "Fails on `main`" means: run the test on a tree with only the `src/` changes reverted. It is not a check to repeat after the merge.

- [ ] `scripts/pre-release.sh --no-live` passes.
- [ ] `-y -s` on an mp4 page with en + es: the mp4 holds 2 `mov_text` streams tagged `spa` and `eng`, both `.vtt` files are on disk, exit 0, `Done.` once, one history row. Fails on `main`.
- [ ] `-y -s --video-format mkv` on a merged page: the `.mkv` holds 2 subtitle streams, the files are kept. Fails on `main`.
- [ ] `-y -s --video-format webm` on a webm page with a vtt track: one `webvtt` stream. Fails on `main`.
- [ ] `-y --sub-langs en` (no `-s`): `Clip.en.vtt` on disk and one `eng` stream. The printed command has `-s` and `--sub-langs en`, and replaying it gives the same file set. Fails on `main`.
- [ ] `options_from_args` unit tests: `--sub-langs X` gives `subs=True`, `sub_langs="X"`. No flag gives `subs=False`, `sub_langs=DEFAULT_SUB_LANGS`. `--sub-langs es,en` gives `subs=True`.
- [ ] `-y -s` on a page with only an `fr` track, and on one with none: the video is saved, exit 0, `Done.`, and `t("subs_none", "es,en")` is printed once. No subtitle file is written. Fails on `main`.
- [ ] `-y -s` on a page whose only track is `en.ttml`: exit 0, `Done.`, no `t("failed", url)`, `t("subs_not_embedded", "Clip.mp4")` printed, `Clip.en.ttml` kept, no `*.temp.*` file, one history row. Fails on `main` (no warning). It also fails on a naive embed (`Download failed`).
- [ ] `-y -s --video-format webm` on a webm page with an `en.srt` track: `t("subs_not_embedded", …)`, `.srt` kept, exit 0.
- [ ] `-y -p -s` on a 2-item carousel where item 1 has a track and item 2 has none: item 1's file holds the stream, item 2's holds none, no `subs_none`, no `partial_result`. A 2-item carousel where one item's track is TTML: both saved, `subs_not_embedded` names that item's file, no `partial_result` line. Fails on `main`.
- [ ] `-y -a -s` (mp3, and `-f m4a`): the audio file has no subtitle stream, the `.vtt` files are on disk, no `subs_not_embedded`. Pins D4: it passes on `main` too.
- [ ] Rerun: `("-s",)` in `test_rerun_over_existing_files_is_not_a_false_failure` points at the new subtitle page. After the second run, the file set is unchanged and the video still holds exactly 2 subtitle streams.
- [ ] Wizard: `-i -s` with scripted answers on the subtitle page saves an mp4 with subtitle streams. A plain wizard walk on the same page writes no subtitle file.
- [ ] `test_subs_enables_subtitle_options` no longer asserts `embedsubtitles`. `build_ydl_opts` has no `embedsubtitles` key, and with `subs=True` its `postprocessors` match `subs=False`.
- [ ] `subs_none` and `subs_not_embedded` exist in both blocks (`test_es_and_en_have_same_keys`).
- [ ] Docs (command): `grep -n "embeds them in the video" README.md` matches line 14, `grep -n "implies -s" README.md` matches the options block, and `download-media --help` shows both new help strings.

## Live check

Not needed. Selecting (`process_subtitles`), writing (`_write_subtitles`) and embedding (`FFmpegEmbedSubtitlePP`) subtitles doesn't depend on the extractor. `fake_site` and the generic loopback pages feed them real subtitle files, and `ffprobe` checks the streams on disk. No cookies or site-specific code is involved. An optional manual check, not a gate: `download-media -y -s -o tmp/ '<any public video with captions>'`, then `ffprobe` shows `mov_text` streams next to the `.vtt` files.

## SemVer

**Minor.** There are new user-visible messages (`AGENTS.md` counts those as minor), and `-s` and `--sub-langs` change behaviour. Nothing is breaking: no flag is renamed, no default value changes, no path moves, and the files `-s` users get today are still written. 0.3.0 is already a minor because of `8cc3d30`. The 0.3.0 notes should say: "`-s` now embeds subtitles in the video (the subtitle files are still saved); `--sub-langs` alone now turns subtitles on; in mp4 the first language is marked as the default track." Commit type: `fix`.

## Open decisions

Each was decided by the roles under the overnight delegation, following the rule: easiest to undo, no data lost, least change to what users rely on. **⚠** marks a change to shipped behaviour.

**D1 ⚠ — Embed or not.**
- A: embed and delete the files (`already_have_subtitle=False`, yt-dlp's default for a bare embed). It matches the docs literally, but `-s` users lose the `.vtt`/`.srt` they get today.
- **B (recommended): embed and keep the files** (`already_have_subtitle=True`, what yt-dlp's own `--write-subs --embed-subs` does, and what the Bash prototype passed). No file is lost. It's undone by dropping one `add_post_processor` call, and it makes the docs true. Cost: the video file changes (a stream-copy remux adds tracks), and in mp4 ffmpeg always marks the first language (`es` with the default `es,en`) as the default track, which some players show without being asked. No yt-dlp or ffmpeg option avoids that (checked).
- C: keep files only and change the docs to say so. It changes nothing on disk, but drops the promise made since the first release and by the prototype, and leaves the PLAN row's goal unmet.
- If the human rejects B, C is the fallback. It touches `README.md:14`, `:78`, `:100` and `docs/index.html:421` instead of `src/`.

**D2 ⚠ — Does `--sub-langs` imply `-s`?**
- **A (recommended): yes.** It's the only reason to pass the flag, the prototype did it, and README line 14 reads that way. A run that saved no subtitles now saves them. No data is lost, and it's undone by one line in `options_from_args`. The printed command keeps `-s` (spec 09), so it also works on 0.2.0.
- B: no, but warn when `--sub-langs` comes without `-s` (a new message, and the run still saves nothing).
- C: no, and fix the docs only.

**D3 ⚠ — Reporting.**
- a) No subtitles in the requested languages: **recommended: warn once per URL when nothing it saved got a subtitle file, exit 0, `Done.` stays.** Alternatives: silent, as today. Or exit 1, which would break scripts that ask for `-s` on videos without captions. Per-item or per-language warnings are noisy with the default `es,en`, since most videos lack one of them.
- b) Embedding failed or incomplete: **recommended: warn per file, keep the files, count the item as saved, exit 0.** The naive alternative, letting yt-dlp's `PostProcessingError` through, turns a saved video into `Download failed` (prototype).

**D4 — `-a -s`.** **Recommended: unchanged, files only.** mp3, opus and wav can't hold subtitles, and m4a subtitles are rarely shown. Alternatives: embed into m4a only, or skip subtitles on `-a` (which would drop files users get today).

**D5 — Wizard.** **Recommended: no subtitle step in this item** (a feature; planner note). `-i -s` and `-i --sub-langs` keep working through the walk, unseen.
