# 27 — Let the wizard keep a video that is labelled as audio

Status: done
Item: `fix/audio-type-step`

## Problem

When a server or a page calls a file audio, the wizard treats it as audio, skips the type step, converts it to mp3, deletes the video inside, and says `Done.`.

Reproduced on `main` at `81ee658`, with yt-dlp 2026.10.26, ffmpeg 9.0.2 and Python 3.14.9. The real console script ran against a loopback server, under `env -i`, with `HOME` and `XDG_*` in a temp folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, and no clipboard:

```bash
# <media>/vid.ogg is 1 s of Theora video and Vorbis audio. The scratch server sends
# /a/<file> as audio/<type> (audio/ogg here), /o/<file> as application/octet-stream,
# and /audio/<file>.html as a page holding <audio src="/o/<file>">.
printf '\n\n\n\n' | download-media http://127.0.0.1:18931/a/vid.ogg
# ✓ Detected: generic / Audio
# [ExtractAudio] Destination: <tmp>/Music/vid.mp3
# Deleting original file <tmp>/Music/vid.ogg (pass -k to keep)
# Done.
#   download-media -y -a http://127.0.0.1:18931/a/vid.ogg
# exit 0. On disk: Music/vid.mp3, audio only (ffprobe). One history row, kind audio.

download-media -y http://127.0.0.1:18931/a/vid.ogg
# exit 0. On disk: Videos/vid.ogg, video and audio.
```

The four answers are URL, audio format, folder and cookies. There is no type step to answer. `<audio src="vid.mp4">` behaves the same way: the wizard saves `Music/audio vid.mp4 (1).mp3` and drops the video, exit 0, while `-y` keeps the `.mp4` with both streams.

- **What happened:** the video was deleted and the run reported success. Nothing offered to keep it.
- **What should happen:** the user can keep the file the way `-y` saves it, and pressing Enter never deletes a video.

## Goal

When a label is yt-dlp's only reason to call a file audio, the wizard shows the type step, and Enter keeps the file as served, video included. Audio that can't hide a video keeps today's flow: no type step, mp3 in `~/Music`.

## Findings

Each result comes from a run: either the real console script (the reproduction) or `cli.main` with the real yt-dlp and ffmpeg, in the same isolated environment. Streams were checked with ffprobe. "Prototype" means the design below applied to a scratch copy of `src/` (44 changed lines); `src/` itself wasn't touched. "0.2.0" and "pre-fix" are `src/` at `v0.2.0` and at `2b9eb5c` (spec 17's pre-fix point), run from scratch copies.

### Every shape loses the video

Video files (both streams), wizard with Enter on every prompt:

| Source                                                                         | `main`, wizard                                                     | `main`, `-y`                       | 0.2.0 and pre-fix, wizard                          | Prototype, wizard                                         |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------ | ---------------------------------- | -------------------------------------------------- | --------------------------------------------------------- |
| Direct `.ogg` (Theora), `.wma`, `.m4b`, `.asf`, `.mp4`, `.webm`, sent as `audio/*` | `Audio`, no type step, `Music/vid.mp3` audio only, `Done.`, exit 0 | `Videos/vid.<ext>`, video kept, exit 0 | `Unknown`, type step, `Videos/vid.<ext>`, video kept | `Audio`, a note, type step, `Videos/vid.<ext>`, video kept, exit 0 |
| `<audio src>` on a Theora `.ogg` or a `.webm`                                  | Same: `.mp3`, video gone                                           | Raw file, video kept               | Same as the row above                              | Same as the row above                                     |

Only the wizard has the bug. `-y`, direct mode with the preview and `-y -a` don't probe, and they save the same files on `main` and on the prototype: `-y` keeps the raw file, and `-y -a` converts it, as asked.

### What yt-dlp knows (question 1)

`extract_info` with processing, on the loopback server:

| Source                       | `extractor_key` | `direct` | `format_id` | `ext` | `vcodec` | `acodec` |
| ---------------------------- | --------------- | -------- | ----------- | ----- | -------- | -------- |
| `/a/vid.ogg`, holding video  | Generic         | True     | `ogg`       | `ogg` | `'none'` | `None`   |
| `/a/aud.ogg`, audio only     | Generic         | True     | `ogg`       | `ogg` | `'none'` | `None`   |
| `/a/vid.mp4` and `/a/aud.mp4` | Generic        | True     | `mp4`       | `mp4` | `'none'` | `None`   |
| `/a/aud.mp3`                 | Generic         | True     | `mpeg`      | `mp3` | `'none'` | `mp3`    |
| `/v/vid.ogg`, `/o/vid.ogg`   | Generic         | True     | `ogg`, `0`  | `ogg` | `None`   | `None`   |
| `<audio src="vid.ogg">`      | HTML5MediaEmbed | —        | `0`         | `ogg` | `'none'` | `None`   |

- **A video and an audio file under the same label give identical formats.** Nothing in the info dict is a measurement, because yt-dlp doesn't read the media before downloading it. `generic.py:877` takes `'none'` from the `Content-Type`, and `_parse_html5_media_entries` takes it from the tag name (`common.py:3378`). Both lines exist at the floor too (2024.10.07: `generic.py:2421`, `common.py:3198`).
- **What can be told apart is how much the source said.** A label names neither codec, so `acodec` stays `None`. An extractor that read a manifest or a site's API names it. yt-dlp also fills it in from the extension for `aac`, `opus`, `mp3`, `flac` and `vorbis` (`YoutubeDL.py:2967`, floor `:2855`). So `vcodec` `'none'` with `acodec` `None` marks a label, not a look at the stream.
- **The extractor's name can't be the test.** `<audio>` comes back as `HTML5MediaEmbed`, not `Generic`, and site extractors produce the same shape. A static count over yt-dlp 2026.10.26's extractors found 142 dict literals that set `vcodec` `'none'`, in 99 modules. 80 of them, in 71 modules, have no `acodec` key. Their `ext`: not written in 46 (it comes from the URL later), `mp3` in 17, an expression in 9, `m4a` in 5, `aac` in 2, `flv` in 1. HLS audio renditions have the same shape (`common.py:2324`), but they belong to a master playlist's video variants, so such a result reads as Video and the flag never applies. An audio-only result with that shape would get the step, which costs a prompt.
- **Measuring would mean reading the file before yt-dlp does,** with ffprobe on the media URL during the probe. See D1.

### What real audio gets today

Real audio in the same shapes, wizard with Enter:

| Source                                                                                         | `main`                                      | 0.2.0                                          |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------- | ---------------------------------------------- |
| Direct `.ogg`, `.wma`, `.m4b`, `.asf`, `.mp4`, `.webm` sent as `audio/*`; `<audio src="x.ogg">` | `Audio`, no type step, `.mp3` in `~/Music` | `Unknown`, type step, raw file in `~/Videos`   |
| Direct `.m4a`, `.wav`; `<audio src="x.m4a">`                                                   | Same                                        | Same as the row above                          |
| Direct `.mp3`                                                                                  | Same                                        | `Audio`, `.mp3` in `~/Music`                   |
| `.ogg` sent as `application/octet-stream`                                                      | `Video`, type step, raw `.ogg` in `~/Videos` | `Unknown`, raw `.ogg` in `~/Videos`           |

### `keepvideo`

`run_pp` (`YoutubeDL.py:3818`) is its only reader. It keeps what a post-processor hands back for deletion, and `FFmpegExtractAudio` hands back the original (`postprocessor/ffmpeg.py:535`). `EmbedThumbnail` deletes its own files (`embedthumbnail.py:229-232`), so `keepvideo` doesn't leave thumbnails behind. Measured with `keepvideo` patched into `-y -a`, two runs each:

- Video `.ogg`: `vid.mp3` and `vid.ogg`, both streams.
- Audio `.ogg`: `aud.mp3` and `aud.ogg`, a duplicate of the sound.
- `.mp3`: `aud.mp3` only, since there's nothing to convert.

The second runs left no third file.

### Renamed videos under `aiff`, `alac`, `ape` and `wav` (question 5)

An `.mp4` renamed to each of the four:

| Source                                                                   | `main`, wizard                              | `main`, `-y`                                                                                             | With the four added to the set                        |
| ------------------------------------------------------------------------ | ------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `<video src>` on `vid.wav`, `.aiff`, `.ape`; `vid.wav` as octet-stream    | `Audio`, `.mp3`, video gone, `Done.`, exit 0 | Raw file kept, `Download failed`, exit 1. `.wav` and `.aiff` also leave an empty `<name>.temp.<ext>`      | `Video`, type step; then the same as `-y`             |
| Direct `vid.wav` and `vid.alac` sent as `audio/*`                         | Same                                        | Same                                                                                                     | `Audio`, the note, type step; then the same as `-y`   |
| Real `.wav` audio: direct `audio/wav`, octet-stream, `<video src>`        | `.mp3` in `~/Music`                         | Raw `.wav` in `~/Videos`, exit 0                                                                         | Type step, defaulting to video: raw `.wav` in `~/Videos` |

The prototype, with the four not added, matches `main` on every row.

### Reruns

Running `-y` twice on `main`: on `.ogg`, the second run writes `vid [vid].ogg` next to `vid.ogg`. On `.wma`, `.m4b`, `.asf`, `.mp4` and `.webm` it doesn't. The cause is row 25's: ogg keeps `purl` in stream tags, while `_source_url` reads format tags. Today the wizard avoids this on these shapes, because it converts to `.mp3`. Once Enter keeps the raw `.ogg`, a wizard rerun does what `-y` does. Measured on the prototype: two runs leave `vid.ogg` and `vid [vid].ogg`, with two history rows. The printed command, replayed in a fresh `HOME`, saved `Videos/vid.ogg` with both streams.

### The prototype against the suite

`PYTHONPATH=<prototype>/src pytest -q`: 1221 passed, 4 failed, and the 2 known xfails. All four failures are expected and listed under Acceptance criteria. Three rows assume that real `.ogg` audio skips the type step. The fourth, `test_wizard_trusts_an_audio_tag_on_such_a_container`, pins the bug itself: a Theora `.ogg` in `<audio>` saved as `.mp3`.

## Scope

### In

- The probe marks a single audio result whose only `'none'` comes from a label, on a container that can hold a video.
- For such a result, the wizard shows the type step, defaults to video, and prints one line saying why.
- Tests, including a loopback route that sends `.mp4`, `.webm` and `.asf` as `audio/*`.

### Out

- **Direct mode, `-y`, `-y -a`, `-p`, `-P`, `--playlist-items`:** they never probe. Unchanged.
- **Playlists and carousels:** they already get the type step, defaulting to video, and their audio items keep `[audio]`. Measured on a page with two `<audio>` items, one Theora and one Vorbis: both saved as raw `.ogg` in `~/Videos`, video kept, on `main` and on the prototype.
- **Audio whose extension says audio** (`m4a`, `wav`, `mp3`, `aac`, `flac`, `opus` and the rest of yt-dlp's audio list, apart from the five in spec 17's set; see D5): no type step. `.m4a` loses a video on every path (spec 17). Renamed videos under the seven extensions the human accepted stay accepted.
- **Reading the file before downloading it** (D1, option B).
- **Keeping the original after a conversion** (D2, options C and D): no `keepvideo`.
- **The `.ogg` rerun duplicate** belongs to row 25. This item makes it reachable from the wizard, for direct `.ogg` links sent as `audio/*` and for `<audio>` on `.ogg`, as `-y` already is. A strict xfail pins it.
- **The false `Download failed` after a kept renamed file, and its stray `.temp.*` file,** belong to row 29 (`false-failure`). The stray file isn't in that row yet; see the notes.
- **`b` at the first step after the probe** still asks that step again. Here that step is the type step.
- **`-l`** still opens the wizard (row 22), now with the type step on these sources.

## Files

| Action | File                            |
| ------ | ------------------------------- |
| modify | `src/download_media/probe.py`   |
| modify | `src/download_media/wizard.py`  |
| modify | `src/download_media/i18n.py`    |
| modify | `tests/conftest.py`             |
| modify | `tests/test_probe.py`           |
| modify | `tests/test_wizard_flow.py`     |
| modify | `tests/test_e2e.py`             |

No new dependency, flag, default or yt-dlp option. `downloader.py`, `cli.py` and `equivalent.py` don't change. Possible collisions: row 25 (`opus-rerun`) changes `_source_url` and will flip the strict xfail added here. Row 31 changes `step_select` in `wizard.py`. Rows 22 and 23 change `cli.py`.

## Design

Written for the recommended option of each decision. The prototype produced every "after" result in this spec.

### `probe.py`

Amended after approval: the label rule lowercases `ext` and leaves out `unknown_video`. See Changes after approval.

```python
def _ext_says_audio(ext: Any) -> bool:
    return ext in MEDIA_EXTENSIONS.audio and ext not in _AUDIO_LISTED_BUT_MAY_HOLD_VIDEO


def _audio_by_label_only(fmt: dict[str, Any]) -> bool:
    # A Content-Type or an <audio> tag says 'none' and names no audio codec:
    # nothing looked at the stream, and the container can hold a video.
    return (
        fmt.get("vcodec") == "none"
        and fmt.get("acodec") is None
        and not _ext_says_audio(fmt.get("ext"))
    )
```

- `_format_kind` calls `_ext_says_audio` where it now writes the same test out (`probe.py:200`). One predicate then serves both rules: spec 17's (unknown `vcodec` plus an extension that says audio) and this one. D5 changes it in one place.
- Extract `_formats(info)` from `_media_kind` (the `formats`-or-`[info]` lines, `:217-220`), so both functions use it.
- `Probe` gains `may_hold_video: bool = False`.
- In `_read_result`, single branch only (`:312-321`): `may_hold_video=kind == ContentKind.SINGLE_AUDIO and any(_audio_by_label_only(f) for f in _formats(data))`. See D6 for why it's "any".
- `_classify_entry`, `_classify_single`, the rule table and `_ProbeYdl` don't change. A format whose `vcodec` and `acodec` are both `'none'` is still an image, so the flag never applies to it.

### `wizard.py`

- `_build_post_probe_plan` (`:385-387`): `if probe_result.content_kind != ContentKind.SINGLE_AUDIO or probe_result.may_hold_video:`, with the comment updated to match.
- `_walk_url`, phase 2.5 (`:565-567`):

  ```python
  _print_detection(probe_result)
  if probe_result.may_hold_video:
      warn(t("audio_may_hold_video"))
  elif probe_result.content_kind == ContentKind.SINGLE_AUDIO:
      opts.audio_only = True
  ```

  `audio_only` stays as the command line set it: `False`, or `True` under `-i -a`. That value becomes the type step's default, and the folder step follows the answer (`step_output`).
- The `playlist_mode = "no"` lock (`:577-580`) still applies, because the kind is still `SINGLE_AUDIO`. So there is no extra `Proceed with download?`, unlike 0.2.0's `Unknown`.
- Each walk in `run_all` starts from the command line (spec 19), so a flagged URL never changes the default for the next URL.

### `i18n.py`

| Key                    | en                                                                                     | es                                                                                         |
| ---------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `audio_may_hold_video` | Marked as audio, but the file may hold a video too: choosing video saves it as it is. | Marcado como audio, pero el archivo puede tener video también: si eliges video, se guarda tal cual. |

### What the user sees

```
✓ Detected: generic / Audio
  vid

Marked as audio, but the file may hold a video too: choosing video saves it as it is.
What do you want to download?
  1) Video (with audio)
  2) Audio only
  b) ← back
  c) ✗ cancel
Select [1]:
```

Then come quality, container, folder (`~/Videos`) and cookies: six prompts, as for any video. The printed command is `download-media -y <url>`, the command `-y` already saves the same file with. Choosing `2` leads to format, folder (`~/Music`) and cookies, and prints `download-media -y -a <url>`. Measured on the prototype: `-i -q 720` prints `download-media -y -q 720 <url>`, and `-i -a` opens the type step at `[2]`.

### yt-dlp behaviour relied on

Confirmed in the 2026.10.26 source, and at the 2024.10.07 floor where marked:

- **Where the label comes from:** `generic.py:877` (floor `:2421`) and `common.py:3378` (floor `:3198`).
- **`acodec` filled in from the extension:** `YoutubeDL.py:2967` (floor `:2855`). These extensions also say audio, so they're never flagged twice over.
- **The video path saves a format labelled audio-only.** In `bv*[height<=?N]+ba/b[height<=?N]/b/best`, `bv*` finds nothing, and `b` falls back to the incomplete formats (`YoutubeDL.py:2625`). Measured: the raw file, both streams, on every row.
- **Conversion deletes the original:** `ffmpeg.py:535` returns it, and `run_pp` (`YoutubeDL.py:3818`, floor `:3715`) deletes it unless `keepvideo` is set.
- **`MEDIA_EXTENSIONS`** is unchanged at the floor (spec 17).

## Cases

Only the wizard, `-i` and `-l` probe. The fixture names are the ones in `tests/conftest.py`, and `as-audio/` is the new route.

| Case                                                                                            | Expected                                                                                                                                    |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Wizard, video under a label: `theora.ogg`, `clip.wma`, `clip.m4b`, `as-audio/` `.mp4`, `.webm`, `.asf`; `<audio>` on Theora, `.mp4`, `.webm` | `Audio`, the note, type step at `[1]`, six prompts, raw file with its video in `~/Videos`, exit 0, `download-media -y <url>`. Today: `.mp3`, video gone |
| Same, `2` at the type step                                                                      | `.mp3` in `~/Music`, `download-media -y -a <url>`, as `-y -a`                                                                              |
| Wizard, real audio under the same labels (`tiny.ogg`, `audio-ogg.html`)                         | Type step at `[1]`: raw `.ogg` in `~/Videos`, audio only. `2`: `.mp3` in `~/Music`. Today: `.mp3`, no type step                            |
| Wizard, `.m4a`, `.wav`, `.mp3`, `.flac`, `<audio src="x.m4a">`                                  | Unchanged: `Audio`, no type step, `.mp3` in `~/Music`                                                                                      |
| Wizard, `<video src>` or octet-stream on an `.ogg`, `.wma`, `.m4b`, `.asf`, `.ogx`              | Unchanged: `Video` (spec 17)                                                                                                               |
| Wizard, renamed video under `aiff`, `alac`, `ape`, `wav`                                        | Unchanged under D5 A: `.mp3`, video gone. Under D5 B: see Findings                                                                         |
| `-i`, no flags                                                                                  | As the wizard                                                                                                                              |
| `-i -a`                                                                                         | Type step shown at `[2]`; Enter gives `.mp3`, as `-y -a`                                                                                   |
| `-i -q 720`, `-i -o <dir>`                                                                      | Type step at `[1]`; the flags preset their own steps                                                                                       |
| Direct with the preview, `-y`, `-y -a`, `-p`, `-P`                                              | Unchanged: same files and exit codes                                                                                                       |
| `-l`                                                                                            | `-y -l` unchanged. `-l` alone walks the wizard (row 22), now with the type step                                                            |
| Several URLs (spec 19): a labelled video, then `audio.html`                                     | Walk 1: six prompts, raw file. Walk 2: four prompts, `.mp3`. Two commands: `-y <video>` and `-y -a <audio.html>`                           |
| Carousel or playlist with labelled audio items                                                  | Unchanged: `[audio]` tags, type step at `[1]`, raw files                                                                                   |
| Rerun, `.wma`, `.m4b`, `.mp4`                                                                   | One file, `Done.` both times, two history rows                                                                                             |
| Rerun, `.ogg`                                                                                   | `X [id].ogg` on the second run, as `-y` on `main` (row 25). Strict xfail                                                                   |
| With and without cookies                                                                        | No difference: the flag reads the result, and `cookiesfrombrowser` is unchanged                                                           |
| Partial and total failure                                                                       | Unchanged: a flagged single goes through `_download_one`, the same as any single                                                           |
| `es` and `en`                                                                                   | The note appears in each language                                                                                                          |

## Acceptance criteria

Every criterion marked **[fix]** needs a test that fails without the fix, which the developer shows by reverting it (`constraints.md`, Verification 5). **[pin]** criteria protect behaviour that must not change, so they pass before and after. Tests that can reach a default folder use the `home` fixture.

Fixtures, in `tests/conftest.py`:

- **A route in `_QuietHandler`,** for example `/as-audio/<name>`, that serves an existing file with an `audio/*` `Content-Type`: `audio/mp4` for `.mp4`, `audio/webm` for `.webm`, `audio/x-ms-asf` for `.asf`. `theora.ogg`, `clip.wma` and `clip.m4b` are already sent as `audio/*` by `extensions_map`. `clip.asf` isn't (`video/x-ms-asf`), so it needs the route.
- **Pages** `<audio src="tiny.mp4">` and `<audio src="tiny.webm">`, next to the existing `audio-theora-ogg`.

Generic extractor, loopback, real yt-dlp, ffmpeg and ffprobe:

- [x] `scripts/pre-release.sh --no-live` passes
- [x] **[fix]** Parametrized over direct `theora.ogg`, `clip.wma` and `clip.m4b`; `as-audio/` on `tiny.mp4`, `tiny.webm` and `clip.asf`; and the pages `audio-theora-ogg`, `<audio src="tiny.mp4">` and `<audio src="tiny.webm">`. The wizard with `SINGLE_WALK`: exit 0; exactly one file in `~/Videos`, with `"video"` in `streams()`; `~/Music` empty; `detected("kind_audio")`, `t("audio_may_hold_video")` and `t("done")` in the output; `commands(output) == [f"download-media -y {url}"]`; that command, replayed in a fresh `HOME`, saves the same name with a video stream
- [x] **[fix]** Same sources, answers URL, `2`, format, folder, cookies: one `.mp3` in `~/Music`, `~/Videos` empty, command `download-media -y -a {url}`
- [x] **[fix]** `-i -a` on direct `theora.ogg`, five Enters: `t("ask_type")` in the output, one `.mp3` in `~/Music`
- [x] **[fix]** `-i -q 720` on direct `theora.ogg`, `SINGLE_WALK`: the `.ogg` with its video in `~/Videos`, command `download-media -y -q 720 {url}`
- [x] **[fix]** Real audio, direct `tiny.ogg` and `audio-ogg.html`: with `SINGLE_WALK`, the raw `.ogg` in `~/Videos` and `streams() == {"audio"}`; with `2`, the `.mp3` in `~/Music`. This replaces the `tiny.ogg` and `audio-ogg.html` rows of `test_wizard_converts_audio_without_codec_info_and_reruns`, and the `tiny.ogg` row of `test_wizard_on_audio_without_codec_info_honours_the_format`, which gains the type answer `2`
- [x] **[fix]** `test_wizard_trusts_an_audio_tag_on_such_a_container` is removed: the `audio-theora-ogg` row of the first criterion states the opposite
- [x] **[fix]** Several URLs: `theora.ogg` and then `audio.html`, ten prompts: `Videos/theora.ogg` with its video, `Music/audio (1).mp3`, history kinds `video` then `audio`, commands `download-media -y {ogg}` and `download-media -y -a {audio}`
- [x] **[fix]** The wizard twice on direct `clip.wma`: one file in `~/Videos`, `t("done")` both times, two history rows
- [x] **xfail, strict:** the wizard twice on direct `theora.ogg` leaves one file. The reason names row 25: ogg keeps `purl` in stream tags. It fails today with `theora [theora].ogg`, as `-y` does on `main`
- [x] **[pin]** The remaining rows of `test_wizard_converts_audio_without_codec_info_and_reruns` (`tiny.m4a`, `tiny.m4a?token=1`, `tiny.wav`, `audio-wav.html`, `audio.html`) pass unchanged, with no type step
- [x] **[pin]** Direct with the preview, `-y` and `-y -a`, on `theora.ogg`, `clip.wma` and `as-audio/tiny.mp4`: the files and exit codes measured on `main`. `-y` and the preview keep the raw file with its video in `~/Videos`; `-y -a` gives the `.mp3`
- [x] **[pin]** `test_wizard_tells_ogg_video_from_ogg_audio_on_one_page_and_repeats_it` and the `test_wizard_keeps_the_video_in_a_container_yt_dlp_lists_as_audio` table pass unchanged

Unit:

- [x] **[fix]** `tests/test_probe.py`, one parametrized table over `_read_result`, or `probe()` on a scripted result. `may_hold_video` is true for `vcodec` `'none'`, `acodec` `None`, with `ext` in `ogg`, `wma`, `m4b`, `asf`, `ogx`, `mp4` and `webm`. It is false with `ext` in `m4a`, `wav`, `aiff`, `alac`, `ape`, `mp3`, `aac`, `flac`, `opus`, `oga` and `mka`, in upper case too (`MP3`, `M4A`, `WAV`, `FLAC`, `Opus`), and for `unknown_video` until row 29 lands (see Changes after approval). It is also false for `webm` with `acodec` `opus`, for `vcodec` `None` on any extension, for a playlist, and for a video result that also holds a labelled audio format (the HLS rendition shape). It is true for an audio result whose formats mix a named `mp3` and a labelled `webm` (D6). The `aiff`, `alac`, `ape` and `wav` rows follow D5
- [x] **[fix]** `tests/test_wizard_flow.py`, with a scripted `Probe(..., content_kind=SINGLE_AUDIO, may_hold_video=True)`: six answers walk it, and `opts.audio_only is False`. With `audio_only=True` preset, as under `-i -a`, Enter keeps it `True`, and the walk takes five answers. `test_single_audio_skips_type_step` passes unchanged
- [x] `audio_may_hold_video` is in both the `es` and the `en` blocks

The pre-existing tests that change are the four in "The prototype against the suite", and no others.

Commit: `fix(wizard): let the type step keep a video labelled as audio`, or split by file if the developer prefers. The probe flag and the wizard change belong together.

## Live check

Not needed. The labels come from yt-dlp's generic extractor and its HTML5 parser, and the tests run both for real on loopback, with the real ffmpeg and ffprobe. No site-specific extractor is involved, and no cookies.

## SemVer

`fix`. Nothing breaking: no flag, default folder, default format or on-disk path changes. What changes is which steps the wizard shows for some sources. It adds one message, which `AGENTS.md` counts as a minor. 0.3.0 is already a minor (`PLAN.md`, "Next release"), so the next version doesn't change.

**Release notes:** compared with 0.2.0, which is what users have, these sources keep 0.2.0's result on Enter (the raw file in `~/Videos`), now with `Audio` and a note where 0.2.0 said `Unknown`, and without its extra confirmation. The 0.3.0 note "a video served as `audio/*` converts too, and loses its video" can go. The `.ogg` note narrows: `.ogg` sent as `audio/*`, or in `<audio>`, no longer converts on Enter. `.m4a` and `.wav` pages still do.

Suggested 0.3.0 note (QA, for the planner): "In the wizard, a file a server or page labels as audio but whose container can hold a video (`.ogg`, `.wma`, `.m4b`, `.asf`, `.mp4`, `.webm` sent as `audio/*` or in `<audio>`) now shows `Audio` with a note and the video/audio choice. Enter keeps the file as served in ~/Videos, as 0.2.0 and `-y` do. Choose 2 for an mp3. `.mp3`, `.m4a`, `.wav`, `.flac`, `.aac` and `.opus` keep the audio flow and convert to ~/Music."

## Changes after approval

Each change has its own test, and the gate covers them.

**The label rule lowercases the extension** (`f2ceff7`). yt-dlp keeps the URL's case in `ext`, so real audio at `.MP3`, `.M4A`, `.WAV` or `.FLAC` missed "the extension says audio", got the type step, and Enter saved the raw file in `~/Videos`. `_audio_by_label_only` now lowercases `ext` before the test. Spec 17's `_format_kind` is left as it was. Tests: `test_an_uppercase_extension_that_says_audio_is_not_flagged` (unit), `test_wizard_keeps_the_audio_flow_for_an_uppercase_audio_extension` (e2e), and the uppercase `.OGG` row of `test_wizard_offers_the_video_of_other_labelled_shapes`, which keeps the flag.

**`unknown_video` is not flagged** (`f2ceff7`). An `<audio src>` with no extension comes back as `unknown_video`. Flagged, Enter took the video path, which today saves `.unknown_video`, says `Download failed` and exits 1 (row 29), where `main` saved an `.mp3`. It is left out of the rule until row 29 lands, and **must rejoin the flagged side when row 29 lands**: a video in such a tag is still converted and lost on Enter today. Extensionless `.ogg` and `.webm` direct URLs keep the flag, because yt-dlp takes `ogg` and `webm` from the `Content-Type`. The unit criterion above is corrected to match. Test: `test_wizard_enter_on_real_audio_with_no_extension_in_an_audio_tag_succeeds`, and the `unknown_video` row of `test_audio_known_only_by_a_label_may_hold_a_video`.

**`isolated_env` runs tests from an empty folder** (`254af34`). Outside the spec's scope; QA flagged it. A scripted wizard answer that drifts by one step can be read as a relative output folder: during this item it left `<worktree>/2/`. Outside the live tests, the autouse fixture now changes into an empty `tmp_path / "cwd"` and fails the test if anything is left there. Test-only, no behaviour change. Test: `test_relative_paths_resolve_under_tmp_path` in `tests/test_isolation.py`.

**Files outside the table.** `tests/test_isolation.py`, for the fixture above. Nothing else outside `.ai/` changed beyond the Files table.

**More coverage than the criteria asked** (`e828336`): real audio that says so (`.mp3`, `.opus`, `.flac`, `.aac`, a `<source>` naming its codec, extensionless audio `Content-Type`s, a site extractor naming the codec) keeps the audio flow; query strings, extensionless ogg and webm, a bare `<source type>` and a site extractor's unnamed codec get the step; `-i` with `-f`, `--video-format` and `-a`; `b` and `c` at the new step; HLS in `<audio>`; a page of labelled `<audio>` items; two multi-URL walks that share no answer.

## Validation

On `main` at `11711f2` (merge `cc4de64`), yt-dlp 2026.10.26, ffmpeg 9.0.2, Python 3.14.9. **GitHub CI hasn't run on this code: nothing has been pushed.**

- **Gate:** `timeout 400 scripts/pre-release.sh --no-live`, exit 0. Lint and types clean. 1370 passed, 9 deselected, 3 xfailed in each locale run (es, en), coverage 98.44%. The xfails: this item's strict `.ogg` rerun pin (row 25), the `opus` rerun (row 25) and spec 17's D4 relative `og:image`.
- **Criteria,** in order, all passing in the gate: `test_wizard_enter_keeps_the_video_of_a_file_labelled_as_audio` (9 sources, with the replay in a fresh `HOME`); `test_wizard_can_still_take_the_audio_of_a_file_labelled_as_audio` (9); `test_interactive_audio_flag_opens_the_type_step_of_a_file_labelled_as_audio_at_audio`; `test_interactive_quality_flag_on_a_file_labelled_as_audio_keeps_the_video`; `test_wizard_enter_keeps_real_audio_under_a_label_raw` and `test_wizard_converts_real_audio_under_a_label_when_asked` (2 each), with the two rows gone from `test_wizard_converts_audio_without_codec_info_and_reruns` and the `tiny.ogg` row of `test_wizard_on_audio_without_codec_info_honours_the_format` answering `("2", "2")`; `test_wizard_trusts_an_audio_tag_on_such_a_container` is no longer collected; `test_wizard_gives_a_file_labelled_as_audio_its_type_step_among_several_urls`; `test_wizard_twice_on_a_file_labelled_as_audio_keeps_one_file` (`clip.wma`, `clip.m4b`, `as-audio/tiny.mp4`); the strict xfail `test_wizard_twice_on_an_ogg_labelled_as_audio_keeps_one_file`; pins: the 5 remaining rows of `test_wizard_converts_audio_without_codec_info_and_reruns`, `test_direct_runs_on_a_file_labelled_as_audio_are_unchanged` (9), `test_wizard_tells_ogg_video_from_ogg_audio_on_one_page_and_repeats_it` and `test_wizard_keeps_the_video_in_a_container_yt_dlp_lists_as_audio` (6), unchanged. Unit: `test_audio_known_only_by_a_label_may_hold_a_video` (41 rows), plus `test_a_url_only_result_under_a_label_may_hold_a_video`, `test_a_playlist_of_labelled_audio_is_not_flagged` and `test_the_flag_reads_every_format_of_an_audio_result`; `test_audio_that_may_hold_a_video_gets_the_type_step` (`plain`: 6 answers, `audio_only` False; `audio-flag`: 5, True); `test_single_audio_skips_type_step` unchanged. `audio_may_hold_video` is in both blocks (`i18n.py:75`, `:153`), checked by `test_es_and_en_have_same_keys` and `test_the_note_on_a_file_labelled_as_audio_in_both_languages`. Removed lines in `tests/` are only the four pre-existing tests the spec named, and the old body of `isolated_env`.
- **Reverting the fix:** not repeated here. This validation relies on the testing and review stages for it; QA approved the item.
- **Reproduction:** the real console script (`.venv/bin/download-media`) against a scratch `http.server` that sends `/a/vid.ogg` (Theora and Vorbis) as `audio/ogg` and `/audio/vid.mp4.html` as `<audio src="/o/vid.mp4">`, under `env -i` with `HOME` and `XDG_*` in a temp folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, no clipboard, run from an empty folder. Streams by ffprobe.
  - Six Enters on `/a/vid.ogg`: `Detected: generic / Audio`, the note, the type step at `[1]`, `Done.`, exit 0, `Videos/vid.ogg` with video and audio, one history row of kind `video`, command `download-media -y <url>`. `-y` saves the same file.
  - The Problem's own four Enters: the walk reaches the folder prompt and stdin ends: `Cancelled.`, exit 0, nothing saved. Nothing is deleted.
  - `2` at the type step: `Music/vid.mp3`, audio only, `download-media -y -a <url>`, kind `audio`.
  - `-i -o <absolute folder>`, six Enters: `vid.ogg` with both streams in that folder, nothing in the temp home's `Videos`, command `-y -o <folder> <url>`.
  - Six Enters on the `<audio src="vid.mp4">` page: `Videos/vid.mp4 (1).mp4` with both streams, exit 0, the same as `-y`.
  - Nothing was left in the working folder by any run. The full matrix was QA's and isn't repeated.
- **Scope:** outside `.ai/`, the diff `a3f63df..cc4de64` touches the seven files in the Files table plus `tests/test_isolation.py`. No change to `pyproject.toml`, `PKGBUILD`, `downloader.py`, `cli.py`, `equivalent.py` or the version.

## Open decisions

**Waiting for the human: D5.** The roles chose A (renamed videos under `aiff`, `alac`, `ape` and `wav` are accepted, like the other seven: such a file becomes an `.mp3` and its video is lost). The decision was put to the human, so it needs their confirmation. Switching to B or C is a one-word change in `_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO`, plus the D5 rows of the probe tests.

Resolved by the roles under the human's overnight delegation, each on the recommended option: D1 A, D2 A (default video, no keepvideo), D3 A (`audio_may_hold_video`), D4 A, D5 A (accept aiff, alac, ape, wav like the other seven; the human should confirm this one, since it was put to them), D6 A. The human reviews this.

The human delegated tonight's decisions to the roles. Each decision has a recommendation, chosen to lose no user data and to be easy to undo, with one exception, D5, which explains why.

### D1: telling a label from a real "no video"

| Option                                                                        | Effect                                                                                                                                                                                               |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. By what the source said (recommended)**                                  | `vcodec` `'none'` with no `acodec`, on an extension that doesn't say audio. No measurement exists in the info dict, so this is as far as it can be told. Two small functions, no network            |
| B. Measure: ffprobe the media URL in the probe                                | The honest answer, but it reads the file before yt-dlp does. It has to pass yt-dlp's headers and cookies to ffprobe, adds a process and a network read to every audio probe, and re-implements part of a download |
| C. Only for the generic extractor (`extractor_key`)                           | Ties the rule to extractor names (`HTML5MediaEmbed` already differs from `Generic`) and leaves the 71 site modules with the same shape unprotected                                                    |

**Recommendation: A.** It's the only one with no network cost, and it errs towards showing the step, which costs a keypress.

### D2: the type step's default, and whether converting keeps the original

| Option                                                         | Real audio under a label (most cases)                                       | Video under a label                                                       | Printed command          |
| -------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------ |
| **A. Default video, no `keepvideo` (recommended)**             | Enter: raw file in `~/Videos`, as 0.2.0 and `-y`. `2`: `.mp3` in `~/Music`  | Enter keeps it, as `-y`                                                   | Replays the same files   |
| B. Default audio, no `keepvideo`                               | Enter: `.mp3`, one more prompt than today                                   | **Enter deletes it**, with the choice on screen                           | Replays                  |
| C. Default audio, `keepvideo` when audio is taken              | Enter: `.mp3` plus the original, a duplicate on every run (measured)        | Kept, in `~/Music`, next to an `.mp3`                                     | **`-y -a` deletes it**: replay differs |
| D. Default audio, keep the original only if ffprobe finds a video stream | Enter: `.mp3` only                                               | Kept in `~/Music`                                                         | **Replay differs**; needs a post-processor subclass, cover-art handling and a new message |

**Recommendation: A.** It's the only option that loses nothing and replays faithfully. It follows spec 17's rule, which the human approved: a wrong "audio" loses the video, while a wrong "video" only keeps the raw file and brings the type step back. It's also the smallest change: one line, easy to undo. The cost falls on real audio under these labels, which now needs `2` to get an `.mp3`. Common audio isn't affected: `.mp3`, `.m4a`, `.wav`, `.flac`, `.aac` and `.opus` never get the step. When the user picks `2`, the conversion deletes the original, as `-y -a` does: they asked for audio.

### D3: saying why the type step appeared

| Option                                     | Effect                                                                                                |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| **A. One line under the banner (recommended)** | One new message. Explains a banner that says `Audio` above a step that defaults to video          |
| B. No line                                 | `Audio`, then `Select [1]` on Video, unexplained                                                      |
| C. Show `Unknown` in the banner            | No new message, but it hides that the source says audio                                               |

**Recommendation: A.** The new message doesn't change the next version (see SemVer).

### D4: overriding the other `Audio` labels

| Option                                                    | Effect                                                                                                     |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **A. Only the flagged sources get the step (recommended)** | Others have no video to keep: `.m4a` loses it on every path, and the rest can't hold one                  |
| B. Every single `Audio` gets the type step                | One more prompt for every `.mp3` and `.m4a` user, and "video" on them only saves the raw audio in `~/Videos` |
| C. `b` at the first step reopens a hidden type step        | Nobody would find it, and the back-navigation needs a special case                                         |

**Recommendation: A.** No new flag in any option.

### D5: renamed videos under `aiff`, `alac`, `ape` and `wav`

| Option                                         | Effect                                                                                                                                                                                                                                      |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Accept them, like the seven (recommended)** | A renamed video under them becomes an `.mp3`, video gone, `Done.`                                                                                                                                                                         |
| B. Add them to `_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO` | That video is kept, but the run ends in `Download failed`, exit 1, with a stray `.temp.wav` or `.temp.aiff`, as `-y` does today (row 29). Every real `.wav`, `.aiff`, `.alac` or `.ape` link then gets the type step and defaults to raw in `~/Videos` |
| C. Add `aiff`, `alac` and `ape`, not `wav`      | Spares the common `.wav`, but splits a class of four extensions with no measurement behind the split                                                                                                                                        |

**Recommendation: A,** which departs from "lose no data". None of the four can hold a video: the only way to lose one is a file renamed on purpose, which is the same reason the human accepted the seven. B protects that file only with a false failure until row 29 lands, and it charges real `.wav` audio, which is common, a step and a worse default. Both A and B are a one-word change in one constant, so either is easy to undo.

### D6: which formats raise the flag

| Option                                                 | Effect                                                                                                                     |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| **A. Any format of the audio result (recommended)**    | A wrong yes costs a prompt, a wrong no a video. Direct links and `<audio>` have one format, so it rarely matters        |
| B. Only the format yt-dlp's audio selector would pick  | Means running format selection in the probe, which `_ProbeYdl` avoids                                                     |

**Recommendation: A.**
