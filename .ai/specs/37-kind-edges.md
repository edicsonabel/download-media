# 37 — Stop the probe misreading storyboards, uppercase extensions and video declared in an audio tag

Status: draft
Item: `fix/kind-edges`

## Problem

The wizard's probe gets three shapes wrong, and two of them end with the video converted to mp3 and deleted while the run says `Done.`. The shapes are a result holding only storyboards, an extension in upper case, and a `<source type="video/...">` inside `<audio>`.

All results were measured on `main` at `cfc7c54` (cookies-undecrypted merged, container-choice included), yt-dlp 2026.10.26, ffmpeg 9.0.2, Python 3.14.9. The real console script (`.venv/bin/download-media`) or `cli.main` ran under `env -i`, with `HOME`, `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and `XDG_DATA_HOME` in a fresh scratch folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, no clipboard, and an empty working folder. A scratch `http.server` on `127.0.0.1` served tiny files made with ffmpeg: `tiny.mp4` (H.264 and AAC), `theora.ogg` (Theora and Vorbis), `tiny.ogg` (Vorbis only), `tiny.m4a`, `tiny.mp3`, `tiny.jpg`, uppercase copies `TINY.M4A`, `TINY.MP3` and `TINY.JPG`, and `CLIP.M4A` (a copy of `tiny.mp4`). Python's `mimetypes` lowercases before guessing, so `CLIP.M4A` was served as `audio/mp4`. Streams were checked with ffprobe.

### R1: a storyboard-only result reads as an image

A stub extractor, loaded through `cli.main` the way `fake_site` loads `FakeSiteIE`, returned YouTube's storyboard shape (`youtube/_video.py:3767-3804`: `ext` `mhtml`, `protocol` `mhtml`, both codecs `'none'`) as the only format, with thumbnails:

```text
printf '\n' | download-media https://stub.test/storyboard-only
# This content is a standalone image, not supported by yt-dlp. Cancelling.
# Cancelled.            exit 0, no files
download-media -y https://stub.test/storyboard-only
# ERROR: [Stub] s1: Requested format is not available. Use --list-formats for a list of available formats
# Download failed: https://stub.test/storyboard-only      exit 1
```

In a carousel (a storyboard-only entry, then a video), the wizard lists the entry as `[image] [no soportado]  Clip`, and `-y -p` counts it as `1 failed`.

- **What happened:** a video whose streams yt-dlp couldn't get is called "a standalone image", and the wizard exits as if it had refused one. `-y` shows yt-dlp's real failure.
- **What should happen:** the wizard reports what `-y` reports.

### R2: uppercase extensions read differently from lowercase ones

Six-Enter walks of the wizard on `<video src="...">` pages:

| Page                         | Wizard on `main`                                                  | Same page in lower case                |
| ---------------------------- | ----------------------------------------------------------------- | -------------------------------------- |
| `<video src="TINY.MP3">`     | `Video`, type step, raw `video-upper-mp3 (1).MP3` in `~/Videos`, audio only | `Audio`, `.mp3` in `~/Music`  |
| `<video src="TINY.M4A">`     | `Video`, type step, raw `.M4A` in `~/Videos`, audio only           | `Audio`, `.mp3` in `~/Music`           |
| `<video src="TINY.JPG">`     | `Video`, type step, `.JPG` saved                                    | `Unknown`, `.jpg` saved                |

- **What happened:** `_format_kind` (`probe.py:210-215`) compares `ext` with yt-dlp's lowercase lists as is. yt-dlp keeps the URL's case (`determine_ext`, `utils/_utils.py:1309-1319`), and only fills `acodec` in from a lowercase extension (`YoutubeDL.py:2967`), so `.MP3` loses both signals. The container step's `_converts` lowercases first (`downloader.py:61`), so the probe and the container step disagree on these files.
- **What should happen:** the same file reads the same in either case, unless yt-dlp itself treats the cases differently. It does for one extension, below.

**`.M4A` is the exception.** Spec 17 reads `.m4a` as audio because yt-dlp's metadata step strips its video on every path (`-vn`). That test is `target_ext == 'm4a'` (`postprocessor/ffmpeg.py:670-675`, called with `info['ext']` at `:706`), which is case-sensitive:

| Source (a video in an `.m4a`)                       | `-y` on `main`                         | Wizard on `main`                                   |
| --------------------------------------------------- | -------------------------------------- | -------------------------------------------------- |
| `<video src="clip.m4a">`                            | `.m4a`, **audio only**                 | `Audio`, `.mp3`                                    |
| `<video src="CLIP.M4A">`                            | `.M4A`, **video and audio**            | `Video`, raw `.M4A`, video and audio               |
| direct `CLIP.M4A`, served as `audio/mp4`            | `Videos/CLIP.M4A`, **video and audio** | **`Audio`, no type step, `Music/CLIP.mp3`, audio only, `Done.`, exit 0** |

The last row loses a video that `-y` keeps. It comes from spec 27's amendment `f2ceff7`, which lowercased the label rule on the premise that `.m4a` loses its video anyway. For `.M4A`, that premise is false.

### R3: a video declared in an `<audio>` tag is converted and lost

```text
# <audio><source src="theora.ogg" type='video/ogg; codecs="theora, vorbis"'></audio>
printf '\n\n\n\n' | download-media http://127.0.0.1:18937/audio-source-theora-videotype.html
# ✓ Detected: generic / Audio
# Deleting original file .../Music/audio-source-theora-videotype (1).ogv (pass -k to keep)
# Done.      exit 0. Music/audio-source-theora-videotype (1).mp3: audio only
download-media -y http://127.0.0.1:18937/audio-source-theora-videotype.html
#            exit 0. Videos/audio-source-theora-videotype (1).ogv: video and audio
```

The four answers are URL, audio format, folder and cookies. There's no type step. `<audio><source src="tiny.mp4" type='video/mp4; codecs="avc1.42E01E, mp4a.40.2"'></audio>` does the same: `Music/... .mp3`, audio only, where `-y` keeps the `.mp4` with both streams.

- **What happened:** `_parse_html5_media_entries` parses `vcodec` `theora` and `acodec` `vorbis` from `type`, then `f.update(formats[0])` overwrites `vcodec` with the `'none'` it takes from the tag name (`extractor/common.py:3378`, `:3450`). `acodec` survives. Spec 27's label rule requires `acodec` `None` (`probe.py:197`), so the format reads as audio that someone looked at, and the wizard converts it.
- **What should happen:** Enter keeps the file as served, as for spec 27's `<audio src="theora.ogg">`.

## Goal

The wizard labels these three shapes the way yt-dlp will treat them. A storyboard-only result reports yt-dlp's own failure. An uppercase extension reads as its lowercase form, except `.M4A`, whose video yt-dlp keeps. A video that an `<audio>` tag's own `type` declares gets spec 27's type step, and Enter keeps it.

## Findings

### What yt-dlp produces for each shape

`extract_info` with processing, and `probe()`, on the loopback server (scratch script, `cfc7c54`):

| Page or link                                                         | `extractor_key`   | Formats (`ext`, `vcodec`, `acodec`) | `probe()`                     |
| -------------------------------------------------------------------- | ----------------- | ----------------------------------- | ----------------------------- |
| `<audio><source src="theora.ogg" type='video/ogg; codecs="theora, vorbis"'>` | HTML5MediaEmbed | `ogv`, `none`, `vorbis`     | audio, not flagged            |
| `<audio><source src="tiny.mp4" type='video/mp4; codecs="avc1…, mp4a…"'>` | HTML5MediaEmbed | `mp4`, `none`, `mp4a.40.2`     | audio, not flagged            |
| `<audio><source src="theora.ogg" type='audio/ogg; codecs="theora, vorbis"'>` | HTML5MediaEmbed | `ogg`, `none`, `vorbis`     | audio, not flagged            |
| `<audio><source src="tiny.ogg" type='audio/ogg; codecs="vorbis"'>` (real audio) | HTML5MediaEmbed | `ogg`, `none`, `vorbis`   | audio, not flagged            |
| `<audio><source src="tiny.m4a" type='audio/mp4; codecs="mp4a.40.2"'>` | HTML5MediaEmbed  | `m4a`, `none`, `mp4a.40.2`          | audio, not flagged            |
| `<audio><source src="theora.ogg" type="audio/ogg">`                  | HTML5MediaEmbed   | `ogg`, `none`, `None`               | audio, flagged (spec 27)      |
| `<audio><source src="theora.ogg" type="video/ogg">`                  | HTML5MediaEmbed   | `ogv`, `none`, `None`               | audio, flagged (spec 27)      |
| `<video src="TINY.M4A">`, `<video src="CLIP.M4A">`                   | HTML5MediaEmbed   | `M4A`, `None`, `None`               | video                         |
| `<video src="TINY.MP3">` / `tiny.mp3`                                | HTML5MediaEmbed   | `MP3`, `None`, `None` / `mp3`, `None`, `mp3` | video / audio        |
| `<video src="TINY.JPG">` / `tiny.jpg`                                | HTML5MediaEmbed   | `JPG`, `None`, `None` / `jpg`, …    | video / unknown               |
| direct `TINY.M4A`, `TINY.MP3` (served as `audio/*`)                  | Generic           | `M4A` / `MP3`, `none`, `None`       | audio, not flagged            |
| direct `TINY.JPG` (served as `image/jpeg`)                           | Generic           | `unknown_video`, `None`, `None`     | video                         |

What follows:

1. **The declared MIME type sets `ext`.** `_media_formats` takes `type_info['ext']` before the URL's extension (`common.py:3363`). A `video/*` type gives a video extension (`video/ogg` gives `ogv`, `video/mp4` gives `mp4`), and the tag name still gives `vcodec` `'none'`. That pair is the only trace of the declared video left in the info dict.
2. **An `audio/*` type naming a video codec leaves no trace.** `type='audio/ogg; codecs="theora, vorbis"'` and real Vorbis under `type='audio/ogg; codecs="vorbis"'` give identical formats. No probe rule can tell them apart.
3. **The `extractor_key` tells the generic page's parser from a site.** A single `<audio>` on a page comes back as `HTML5MediaEmbed` (`extractor/genericembeds.py:8-33`), which runs `_parse_html5_media_entries` on whatever page it's given. There, every codec name comes from the page's markup, never from a manifest or a site's API. Site extractors that name `acodec` on an audio-only `webm` or `mp4` (DASH, HLS audio renditions) read a manifest, and spec 27 pins those as not flagged (`webm` with `opus`, `test_probe.py`).
4. **A direct `.JPG` comes back as `unknown_video`,** because yt-dlp compares the extension with `_UnsafeExtensionError.ALLOWED_EXTENSIONS` as is (`generic.py:906-908`). No case rule can reach it: it stays `Video` (Out).

### Where storyboards come from

YouTube adds storyboards after its own no-formats check (`youtube/_video.py:4046-4083`, then `:4170`). That check raises only when YouTube gives a playability reason. When every stream was skipped without one, the result holds only storyboards (read in the source, not measured on a real video). yt-dlp then warns `Only images are available for download` (`YoutubeDL.py:2930-2933`), and every format selector drops formats with both codecs `'none'` (`YoutubeDL.py:2608-2609`), so the download fails with `Requested format is not available`. DASH storyboards from other sites have the same shape (`common.py:3034-3043`: `ext` `mhtml`). yt-dlp names the extension `MEDIA_EXTENSIONS.storyboards == ('mhtml',)` (`utils/_utils.py:5113`, present at the 2024.10.07 floor).

The probe calls it an image in `_ProbeYdl.process_video_result` (`probe.py:293-298`): `_media_kind` reads the storyboard as `SINGLE_IMAGE`, so the override hands the result back before yt-dlp's format selection can fail it. Spec 18 wrote that override to defer to yt-dlp everywhere except real images, and a DRM video already goes through it to yt-dlp's honest failure (spec 18, Design: "It decides on raw formats"). A storyboard is a preview of a video, not the content.

### Prototype

A scratch driver applied the design below at runtime, from a folder outside the repo, and ran `cli.main` (stub extractor) or the real console script (generic pages). `src/` wasn't touched.

| Case                                                      | Prototype                                                                                         |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Wizard, `storyboard-only`                                 | `Could not inspect URL: Requested format is not available.`, manual flow, `Download failed`, exit 1, no files, as `-y` |
| Wizard, storyboard-only entry and a video                 | `Some items could not be inspected: Requested format is not available.`, `[?] [no soportado]  (item #1)`, `[video]`, Enter saves `02 - Clip [v1].mp4`, exit 0. `-y -p`: the same file, `1 of 2 items downloaded (0 without downloadable media, 1 failed)` |
| Wizard, `image-format` (a `jpg` format, both codecs `'none'`) | Unchanged: `image_only_skipped`                                                               |
| Wizard, `<audio><source type="video/ogg; codecs=…">`, `type="video/mp4; codecs=…"` | `Audio`, the spec 27 note, type step, Enter: raw `.ogv` / `.mp4` in `~/Videos`, video and audio, `download-media -y <url>` |
| Wizard, `<audio><source type="audio/ogg; codecs=…">` (Theora inside) | Unchanged: `.mp3`, video lost (finding 2)                                            |
| Wizard, real Vorbis and AAC under `audio/*` types with codecs | Unchanged: `Audio`, no type step, `.mp3` in `~/Music`                                       |
| Wizard, `<video src="TINY.MP3">`                          | `Audio`, no type step, `.mp3` in `~/Music`                                                        |
| Wizard, `<video src="TINY.JPG">`                          | `Unknown`, the extra `Proceed with download?` that `Unknown` brings (spec 17, Out), `.JPG` saved   |
| Wizard, `<video src="CLIP.M4A">` / `TINY.M4A`             | `Video`, as `main`                                                                                |
| Wizard, direct `CLIP.M4A` (video) / `TINY.M4A` (audio)    | `Audio`, the note, type step; Enter keeps the raw `.M4A`, with its video for `CLIP`               |
| Wizard, direct `TINY.MP3`                                 | Unchanged: `Audio`, `.mp3` in `~/Music`                                                            |

The prototype's first storyboard version filtered storyboards in `_media_kind`. It behaved the same, but would break the probe and the match filter agreeing on "nothing to fetch" (`test_probe_and_match_filter_agree_on_nothing_to_fetch`, `{"formats": [STORYBOARD]}`), so the design below changes only the override. The rerun with the override-only version gave the same rows.

The prototype's first case version also let `_converts` follow the `.M4A` rule. `-y` on `<video src="CLIP.M4A">` then saved `video-upper-clip-m4a (1).M4A.mp4`: see the naming finding under Out. The design leaves `_converts` alone (D2).

## Scope

### In

- `_ProbeYdl` passes a result whose formats are all storyboards to yt-dlp, so its failure reaches the probe (D1).
- One case rule for extensions, shared by the probe's two rules, with `.M4A` read as a container that may hold a video (D2).
- Spec 27's flag also covers a generic page's `<audio>` whose own `type` declares a video container (D3).
- Tests, with new fixture pages.

### Out

- **Direct mode, `-y`, the preview, `-y -a`, `-p`, `-P`, `--playlist-items`:** they never probe, and `_converts` doesn't change (D2), so they save the same files with the same exit codes. Pinned.
- **The six prompts of the manual flow after a storyboard-only probe.** They end in `Download failed`, which is row 38's (`probe-fail-fast`) case. This item adds one more cause to it: `Requested format is not available` after storyboards. Noted for the planner.
- **`<audio><source type="audio/...; codecs=…">` naming a video codec** (finding 2): nothing in the info dict tells it from real audio. A strict xfail pins it, and the planner can add it to row 64's yt-dlp reports: `common.py:3450` overwrites the codecs parsed from `type`, which is also spec 17's F4.
- **A site extractor calling `_parse_html5_media_entries` on its own `<audio>`** (22 modules use it) keeps its own `extractor_key`, so D3 doesn't cover it.
- **A direct `.JPG` link reading `Video`** (finding 4): yt-dlp gives `unknown_video`. Unchanged.
- **Uppercase sources the container step converts get a doubled extension, and a rerun downloads again.** Measured on `cfc7c54`: `-y` on a direct `TINY.WEBM` saves `TINY.WEBM.mp4`; `<video src="TINY.WEBM">` gives `video-upper-webm (1).WEBM.mp4`; `CLIP.MOV` gives `CLIP.MOV.mp4`; `--video-format mkv` gives `TINY.WEBM.mkv`; a second `-y` run into the same folder downloads and converts again. yt-dlp's converter lowercases `source_ext` and then calls `replace_extension(filename, target, source_ext)` (`postprocessor/ffmpeg.py:558-564`), which appends when the file's real extension is `.WEBM`; `final_ext`'s rerun check does the same (`YoutubeDL.py:3470`). Spec 30's default made it reachable from every default run. New row for the planner (`P3`, suggested id `upper-ext-names`). It's why D2 keeps `_converts` as it is.
- **`<video src="X.M4A">` keeps today's mismatch:** the probe says `Video`, the container step doesn't convert (`_converts` lowercases), so the summary says `mp4` over a raw `.M4A`. Converting it would hit the naming bug above.
- **A flagged audio kept as video stays in its container:** `_converts` skips `vcodec` `'none'`, so Enter on R3 saves `.ogv` or `.mp4` as served under the default mp4, as spec 27's shapes do since spec 30. Unchanged.
- **The probe and `_converts` read `.gif` and `.jpeg` differently** (`MEDIA_EXTENSIONS.thumbnails` against `_IMAGE_EXTS`): a label only, accepted in spec 17 D1.

## Files

Line numbers on `main` at `cfc7c54`.

| Action | File                                | What, and where                                                                                                                                       |
| ------ | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| modify | `src/download_media/downloader.py`  | The comment and `_ext_says_audio` (`:45-52`). `_converts` (`:59-65`) keeps its code; at most a comment saying why it lowercases first                  |
| modify | `src/download_media/probe.py`       | `_audio_by_label_only` (`:192-197`), `_format_kind`'s thumbnails test (`:210-215`), `_ProbeYdl.process_video_result` (`:293-298`), the `may_hold_video` line in `_read_result` (`:339-340`) |
| modify | `tests/conftest.py`                 | The generic `pages` dict (around `:255-305`): new pages. `FakeSiteIE._codec_shape` (`:633-638`): `storyboard-carousel`                                 |
| modify | `tests/test_probe.py`               | The rule tables (`:225-275`), the flag tables (`:327-414`), new override test                                                                          |
| modify | `tests/test_e2e.py`                 | The criteria below; `storyboard-only` leaves two refusal tests (`:3546-3547`, `:4786-4787`); `test_entries_yt_dlp_cannot_fetch_stay_images_with_no_warning_of_failure` (`:4795`) changes; the `TINY.M4A` row moves (`:3216` to `:3233-3243`) |
| modify | `tests/test_downloader.py`          | One row in `test_converts_reads_like_the_probe` (`:145-168`)                                                                                            |

No new message, flag, default, dependency or on-disk state. `wizard.py`, `cli.py`, `i18n.py`, `equivalent.py` don't change.

**Collisions with items merging first:**

- `fix/m4a-cover` changes `build_ydl_opts` (`downloader.py:396-403`) and `download()` (`:787-797`). No shared lines. It adds to `tests/conftest.py` and `tests/test_e2e.py`, in other places.
- `fix/cancel-exits` changes `cli.py`, `i18n.py`, `__main__.py`, and flips `code == 0` to `code == 1` in `tests/test_e2e.py`, including in the two refusal tests whose parameter lists hold `storyboard-only` (`:3546`, `:4786` on `main`). This item removes that parameter from both, on top of cancel-exits' version.
- `fix/subs-failures` (spec 36) changes `downloader.py` at `:68-91`, `:155-299`, `:514-575` and `:775-825`, plus `cookies.py`. No shared lines.

## Design

Written for the recommended option of each decision.

### 1. Storyboards (D1 A)

In `probe.py`, next to `_has_thumbnail`:

```python
def _only_storyboards(info: dict[str, Any]) -> bool:
    # A preview of a video yt-dlp couldn't get, not an image: its format selection fails it.
    formats = _formats(info)
    return bool(formats) and all(
        str(f.get("ext") or "").lower() in MEDIA_EXTENSIONS.storyboards for f in formats
    )
```

`_ProbeYdl.process_video_result` sends such a result to `super()` before its image test. Everything else stays: `_format_kind` still calls a storyboard format `SINGLE_IMAGE`, `_media_kind` still returns `SINGLE_IMAGE` for `{"formats": [STORYBOARD]}`, so the probe and the match filter still agree on "nothing to fetch", and a storyboard next to a real format still loses to it.

What yt-dlp then does, confirmed in its source and in the prototype:

- **Single:** `process_video_result` warns `Only images are available` (silenced by the probe's logger) and raises `Requested format is not available`. `ignoreerrors` turns that into `data is None` with the error recorded, so `_read_result` returns `failed=True` with `_short_reason`'s `Requested format is not available.` (it stops before `Use --list-formats`). The wizard prints `probe_failed` and falls back to the manual flow, as it does for a DRM video today.
- **Carousel entry:** the same raise, inside `process_ie_result`'s entry loop, makes the entry `None`. `_classify_entry(None)` gives `[?]`, unsupported, titled `(item #N)`. `_read_result` keeps the error as `error_message`, so the wizard prints `probe_partial`. `-y -p` counts that entry as failed, so the warning matches what a download would do.

The override is the same hook spec 18 relies on (`YoutubeDL.py:2833` at 2026.10.26, `:2720` at the floor). `MEDIA_EXTENSIONS.storyboards` exists at the floor.

### 2. Extension case (D2 A)

In `downloader.py`:

```python
def _ext_says_audio(ext: Any) -> bool:
    if not isinstance(ext, str):
        return False
    low = ext.lower()
    # yt-dlp's metadata step strips the video only from an extension written "m4a"
    # (target_ext == 'm4a'): an .M4A keeps it, like an .m4b.
    if low == "m4a" and ext != "m4a":
        return False
    return low in MEDIA_EXTENSIONS.audio and low not in _AUDIO_LISTED_BUT_MAY_HOLD_VIDEO
```

The comment above `_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO` (`:45-47`) gains the same caveat for its `.m4a` sentence.

In `probe.py`:

- `_audio_by_label_only` stops lowercasing `ext` itself (`:195-196`) and passes it as is, so the `.M4A` rule applies. Lowercasing there would turn `M4A` back into `m4a`.
- `_format_kind`'s thumbnails test (`:213`) compares the lowercased `ext`. `_ext_says_audio(ext)` at `:211` gets `ext` as is.

`_converts` (`downloader.py:59-65`) keeps its code. It lowercases `ext` before calling `_ext_says_audio`, so the `.M4A` exception never applies there and the container step behaves exactly as on `main`. A comment there may say so and point at the naming row.

Net effect: `MP3`, `FLAC`, `WAV`, `AAC`, `Opus` and the rest of yt-dlp's audio list read as their lowercase form; `OGG`, `WMA`, `M4B`, `ASF`, `OGX` stay "may hold video" as before; `JPG`, `PNG`, `WEBP` read `Unknown`; `M4A` in any case but `m4a` is a container that may hold a video, in both of the probe's rules.

### 3. Video declared in an `<audio>` tag (D3 A)

In `_read_result`'s single branch, `may_hold_video` also holds when the result came from yt-dlp's generic HTML5 parser and a format's `ext` is a video container with `vcodec` `'none'`, whatever `acodec` says:

```python
def _declared_video(fmt: dict[str, Any], info: dict[str, Any]) -> bool:
    # On a page, <audio> sets vcodec 'none' over a type that declared a video, and that type
    # still sets ext (common.py:3363, :3450). Site extractors read manifests: not them.
    ext = str(fmt.get("ext") or "").lower()
    return (
        info.get("extractor_key") == "HTML5MediaEmbed"
        and fmt.get("vcodec") == "none"
        and ext in MEDIA_EXTENSIONS.video
    )
```

`may_hold_video = kind == SINGLE_AUDIO and any(_audio_by_label_only(f) or _declared_video(f, data) for f in _formats(data))`. The developer may fold the two predicates into one if it reads better; the truth table is in the criteria. Nothing in the wizard changes: spec 27's type step, default and note apply as they are.

If yt-dlp renames `HTML5MediaEmbed`, the extra flag stops applying and the behaviour falls back to today's; the e2e criteria fail at the gate.

## Cases

Only the wizard, `-i` and `-l` (which opens the wizard until row 22) probe.

| Case                                                                         | Expected                                                                                                       |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Wizard, storyboard-only single                                               | `probe_failed` with `Requested format is not available.`, manual flow, `Download failed`, exit 1, no files, no history row, the printed command. Today: `image_only_skipped` |
| Wizard, storyboard-only entry in a carousel                                  | `probe_partial` with the same reason, `[?]` for the entry, Enter saves the rest. Today: `[image]`, no warning  |
| Wizard, a storyboard next to a real format                                   | Unchanged: the real format decides                                                                            |
| Wizard, `image-format`, `ig-image`, `thumbnail-image`, `only-images`         | Unchanged                                                                                                      |
| Wizard, `<video src>` on `.MP3`, `.FLAC`, `.WAV`                             | `Audio`, no type step, `.mp3` in `~/Music`, as in lower case. Today: `Video`, raw file in `~/Videos`           |
| Wizard, `<video src>` on `.JPG`                                              | `Unknown`, as `.jpg`                                                                                           |
| Wizard, `<video src>` on `.M4A`, audio or video inside                       | Unchanged: `Video`, raw `.M4A` in `~/Videos`, video kept                                                       |
| Wizard, direct `.M4A` sent as `audio/mp4`, or `<audio src="X.M4A">`          | `Audio`, the note, type step at video; Enter keeps the raw `.M4A`, video kept. `2`: `.mp3`. Today: `.mp3`, no type step, video lost |
| Wizard, direct `.MP3`, `.WAV`, `.FLAC` sent as `audio/*`                     | Unchanged: `Audio`, `.mp3` in `~/Music`                                                                        |
| Wizard, `<audio><source type="video/ogg; codecs=…">` or `type="video/mp4; codecs=…"` | `Audio`, the note, type step; Enter keeps the `.ogv` / `.mp4` with its video, `download-media -y <url>`. `2`: `.mp3`, `-y -a`. Today: `.mp3`, video lost |
| Wizard, `<audio><source type="audio/…; codecs=…">` with real audio           | Unchanged: no type step, `.mp3`                                                                                |
| Wizard, `<audio><source type="audio/ogg; codecs=\"theora, vorbis\"">`        | Unchanged: `.mp3`, video lost (Out, strict xfail)                                                              |
| Site extractor, audio-only `webm`/`mp4` with a named codec                   | Unchanged: not flagged                                                                                         |
| `-i -a` on an R3 page or a direct `.M4A`                                     | Type step at `[2]`, Enter gives `.mp3`, as spec 27                                                             |
| Several URLs (spec 19)                                                       | Each walk decides alone; an R1 URL fails its own download and the run reports it as any failed URL            |
| Direct with the preview, `-y`, `-y -a`, `-p`, `-P`, `--playlist-items`       | Unchanged on every page above                                                                                  |
| `-l`                                                                         | `-y -l` unchanged. `-l` alone walks the wizard, with the rows above                                            |
| With / without cookies                                                       | No difference: the rules read the result. A storyboard-only result under cookies fails the same way; it isn't an auth error, so no cookie prompt |
| Rerun                                                                        | Wizard twice on an R3 page: one file, two history rows, as spec 27's labelled shapes                           |
| Partial and total failure                                                    | R1 single: total failure, exit 1. R1 entry: partial, as `-y -p`                                                |
| `es` and `en`                                                                | No new message; the existing ones appear in each language                                                      |

## Acceptance criteria

Every **[fix]** needs a test that fails without the fix, shown by reverting it (`constraints.md`, Verification 5). **[pin]** criteria pass before and after. Tests that can reach a default folder use the `home` fixture.

Fixtures, in `tests/conftest.py`:

- Generic pages: `audio-source-theora-videotype` (`<audio><source src="theora.ogg" type='video/ogg; codecs="theora, vorbis"'></audio>`), `audio-source-mp4-videotype` (`<audio><source src="tiny.mp4" type='video/mp4; codecs="avc1.42E01E, mp4a.40.2"'></audio>`), `audio-source-theora-audiotype` (`type='audio/ogg; codecs="theora, vorbis"'`), `video-upper-mp3`, `video-upper-flac`, `video-upper-wav`, `video-upper-jpg` (`<video src="typed/audio/mpeg/tiny.mp3/TINY.MP3">` and the like, through the existing `/typed/` route), `video-upper-m4a-video` (`<video src="typed/video/mp4/clip.m4a/CLIP.M4A">`). No new media file: `/typed/` serves `clip.m4a` (a copy of `tiny.mp4`) under any name.
- `FakeSiteIE`: `storyboard-carousel`, a playlist of `_with_formats("s1", self._storyboard())` with `thumbnails`, then `self._video("v1")`.

`fake_site`:

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] **[fix]** `test_e2e.py::test_wizard_on_a_storyboard_only_video_reports_yt_dlp_s_failure`: `-i` on `fake.test/storyboard-only`, Enter through the manual flow to the end: `t("probe_failed", "Requested format is not available.")` in the output, `t("image_only_skipped")` not in it, `t("failed", url)` in it, exit 1, no files, no history row
- [ ] **[fix]** `test_e2e.py::test_wizard_marks_a_storyboard_only_entry_as_not_inspected`: wizard on `storyboard-carousel`, `PICKER_WALK`: `partly_inspected("Requested format is not available.")` in the output, `tag("kind_image")` not in it, `[?]` and `tag("kind_video")` once each, `names(out) == ["02 - Clip [v1].mp4"]`, exit 0; `-y -p` saves the same file and prints `t("partial_result", …)` with one failed
- [ ] **[fix]** `storyboard-only` is removed from `test_wizard_on_a_lone_format_without_streams_is_refused_as_an_image` and `test_a_lone_image_is_refused_at_once`. Their other rows stay **[pin]**
- [ ] **[fix]** `test_entries_yt_dlp_cannot_fetch_stay_images_with_no_warning_of_failure` (`undownloadable-carousel`) is renamed to say what it checks, and expects 2 `tag("kind_image")`, 1 `[?]`, 1 `tag("kind_video")`, and `partly_inspected("Requested format is not available.")`. Spec 18's criterion ("3 `[image]`, no `probe_partial`") is superseded for the storyboard entry only
- [ ] **[pin]** `storyboard-video` and `storyboard-audio` rows of `test_wizard_on_real_shapes_with_partial_codec_info` unchanged
- [ ] **[pin]** `-y` on `storyboard-only`: exit 1, `Requested format is not available` in the output, as on `main`

Generic extractor, loopback, real yt-dlp, ffmpeg and ffprobe:

- [ ] **[fix]** `test_e2e.py::test_wizard_keeps_the_video_an_audio_tag_declares[theora|mp4]`: the two `*-videotype` pages, `SINGLE_WALK`: `detected("kind_audio")`, `t("audio_may_hold_video")`, `t("done")`, exactly one file in `~/Videos` (`.ogv`, `.mp4`) with `"video"` in `streams()`, `~/Music` empty, `commands(output) == [f"download-media -y {url}"]`, and that command replayed in a fresh `HOME` saves the same name with a video stream
- [ ] **[fix]** Same pages, answers URL, `2`, format, folder, cookies: one `.mp3` in `~/Music`, `~/Videos` empty, command `download-media -y -a {url}`
- [ ] **xfail, strict:** `test_e2e.py::test_an_audio_tag_whose_audio_type_names_a_video_codec_keeps_its_video` on `audio-source-theora-audiotype`, `SINGLE_WALK`: expects the raw `.ogg` with its video in `~/Videos`. Reason names `common.py:3450` and that the formats equal real Vorbis audio
- [ ] **[pin]** `audio-source-vorbis` keeps the audio flow (its existing test), and so does `site-audio-named` (`fake_site`)
- [ ] **[fix]** `test_e2e.py::test_wizard_reads_an_uppercase_audio_extension_as_audio[mp3|flac|wav]`: `video-upper-*` pages, `AUDIO_WALK`: `detected("kind_audio")`, `t("ask_type")` not in the output, one `.mp3` in `~/Music`, `~/Videos` empty
- [ ] **[fix]** `test_e2e.py::test_wizard_reads_an_uppercase_image_extension_as_unknown`: `video-upper-jpg`, the walk the lowercase `.jpg` page takes: `detected("kind_unknown")`, one `.JPG` saved
- [ ] **[fix]** `test_e2e.py::test_wizard_keeps_the_video_of_an_uppercase_m4a_labelled_as_audio`: direct `typed/audio/mp4/clip.m4a/CLIP.M4A`, `SINGLE_WALK`: `t("audio_may_hold_video")`, one file in `~/Videos` named `CLIP.M4A` with `"video"` in `streams()`, `~/Music` empty, command `download-media -y {url}`
- [ ] **[fix]** The `typed/audio/mp4/tiny.m4a/TINY.M4A` row moves from `test_wizard_keeps_the_audio_flow_for_an_uppercase_audio_extension` to `test_wizard_offers_the_video_of_other_labelled_shapes`. The `MP3`, `WAV` and `FLAC` rows stay **[pin]**
- [ ] **[pin]** `video-upper-m4a-video`, wizard with `SINGLE_WALK` and `-y`: `Video`, one `.M4A` in `~/Videos` with `"video"` in `streams()`, as on `main`
- [ ] **[pin]** Direct with the preview, `-y` and `-y -a` on `audio-source-theora-videotype`, `video-upper-mp3` and direct `CLIP.M4A`: the files and exit codes measured on `main` (`.ogv` with video, raw `.MP3`, `CLIP.M4A` with video; `-y -a` gives an `.mp3`)

Unit:

- [ ] **[fix]** `test_probe.py`: `_format_kind` table gains `MP3`, `FLAC`, `WAV`, `AAC`, `Opus` → audio; `JPG`, `PNG`, `WebP` → unknown; `M4A`, `M4a` → video; `OGG`, `WMA` → video (**[pin]**), all with `vcodec` and `acodec` `None`
- [ ] **[fix]** `test_probe.py`: the flag table gains `M4A` and `M4a` (`vcodec` `'none'`, `acodec` `None`) → flagged; `m4a` stays not flagged; `M4A` leaves `test_an_uppercase_extension_that_says_audio_is_not_flagged`
- [ ] **[fix]** `test_probe.py::test_an_audio_tag_declaring_a_video_container_is_flagged`: with `extractor_key` `HTML5MediaEmbed` and `vcodec` `'none'`, flagged for `ext` `ogv`, `mp4`, `webm`, `MP4`, with `acodec` `vorbis`, `mp4a.40.2`, `opus` or `None`; not flagged for `ogg`, `oga`, `m4a`, `mp3` with a named `acodec`; not flagged with any other `extractor_key` or none (`webm` with `opus` stays as spec 27 pinned it); not flagged for a playlist or a video result
- [ ] **[fix]** `test_probe.py::test_a_storyboard_only_result_goes_to_yt_dlp`: `_ProbeYdl.process_video_result` on `{"id": "s", "formats": [STORYBOARD], "thumbnails": [...]}` calls `_Ydl.process_video_result` (patched), and on an `image-format` result doesn't
- [ ] **[pin]** `test_probe_and_match_filter_agree_on_nothing_to_fetch` (with its `{"formats": [STORYBOARD]}` row), the storyboard row of `test_format_with_one_codec_set_or_neither` and `test_strongest_format_decides` unchanged
- [ ] **[pin]** `test_downloader.py::test_converts_reads_like_the_probe` gains `{"ext": "M4A"}` → `False` and `{"ext": "MP3"}` → `False`: the container step doesn't change

Commits: one per decision is fine (`fix(probe): let yt-dlp fail a result that only has storyboards`, `fix(probe): read extensions in any case, keeping .M4A's video`, `fix(probe): flag an audio tag whose type declares a video`), each with its tests.

## Live check

Not needed. The generic shapes come from yt-dlp's real generic extractor and HTML5 parser, run on loopback with the real ffmpeg. The storyboard behaviour depends only on the format dicts and yt-dlp's generic format selection, which the stub exercises for real. A YouTube storyboard-only result can't be produced on demand: it depends on what YouTube returns and on the local yt-dlp setup.

What a live check would add: that a real storyboard-only result reaches `_ProbeYdl` with only `mhtml` formats, rather than failing earlier in the extractor. If the human meets one (`download-media -y <url>` printing `Requested format is not available` on a YouTube video), `download-media -i <url>` should print `Could not inspect URL: Requested format is not available.` instead of `This content is a standalone image`.

## SemVer

`fix`. No flag, default, path or message changes, and no exit code changes outside the wizard's walk. Not breaking by `AGENTS.md`'s list. 0.4.0 is already a minor.

Behaviour changes for the 0.4.0 review list (pending merge):

1. Spec 37 D1: in the wizard, a video for which yt-dlp lists only storyboards (preview frames) now says `Could not inspect URL: Requested format is not available.` and goes to the manual flow, which ends in `Download failed`, exit 1, as `-y` does. It used to say `This content is a standalone image`, exit 0 (exit 1 after spec 23). In a carousel such an entry shows `[?]`, with `Some items could not be inspected: …`, where it showed `[image]`.
2. Spec 37 D2: an uppercase audio extension (`.MP3`, `.FLAC`, `.WAV`, …) with no codec info reads `Audio` and converts to mp3 in `~/Music`, as in lower case, where it read `Video` and landed raw in `~/Videos`. `.JPG`, `.PNG`, `.WEBP` read `Unknown` (with its extra confirmation), where they read `Video`.
3. Spec 37 D2: an `.M4A` written in any case but `m4a` and labelled audio (a direct link sent as `audio/mp4`, `<audio src>`) now gets spec 27's note and type step, and Enter keeps the raw file in `~/Videos`, where it converted to mp3 in `~/Music` and dropped any video. This reverses part of spec 27's `f2ceff7` for `.M4A` only. The cost: real uppercase `.M4A` audio now needs `2` for an mp3.
4. Spec 37 D3: on a generic page, `<audio>` with a `<source type="video/…; codecs=…">` now gets the note and the type step, and Enter keeps the file with its video, where it converted to mp3 and lost the video.

Release note: "The wizard no longer calls a video an image when yt-dlp finds only its preview frames; it shows yt-dlp's error. It reads uppercase extensions like lowercase ones, and keeps the video of an `.M4A`, or of an `<audio>` tag that declares a video, when you press Enter."

## Open decisions

The human delegated tonight's decisions to the roles. Each has a recommendation, chosen to lose no video and to match specs 17, 18, 27 and 30.

### D1: a result with only storyboards

| Option                                                    | Effect                                                                                                                                                                                                  |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Let yt-dlp fail it (recommended)**                   | `_ProbeYdl` hands it to yt-dlp, as spec 18 does for DRM. Single: `probe_failed` with `Requested format is not available.`, manual flow, `Download failed`, exit 1, as `-y`. Entry: `[?]` plus `probe_partial`, as `-y -p` counts it failed. No new message, one predicate |
| B. Refuse at once with a new message                      | Something like "yt-dlp found only preview images for this video, no video or audio". Skips the manual flow's six prompts, but needs a new `Probe` field or kind, two messages, and a guess at the cause (DRM, a skipped stream) that yt-dlp's warning only partly states |
| C. Leave it an image                                      | Today's false "standalone image"                                                                                                                                                                       |

**Recommendation: A.** It's the simplest change, it makes the wizard say what `-y` says, and it follows spec 18's own rule that only real images are handed back. The six prompts are row 38's to remove, for every unrecoverable probe failure at once; B would solve them for this cause only. The cost: the reason is yt-dlp's terse `Requested format is not available.`, and a carousel entry loses its title (`(item #N)`), as any entry yt-dlp fails.

### D2: extension case

| Option                                                                 | Effect                                                                                                                                                                                                 |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A. Lowercase, `.M4A` may hold a video, `_converts` unchanged (recommended)** | Both probe rules read `MP3`, `FLAC`, `JPG`… as lowercase. `.M4A` reads like `.m4b`, because yt-dlp strips video only from `m4a` (measured: `CLIP.M4A` keeps it under `-y`). Fixes the direct `CLIP.M4A` loss. No change under `-y` |
| B. Plain lowercase everywhere                                          | Simplest. But `<video src="CLIP.M4A">` becomes `Audio` and loses its video in the wizard, where `main` keeps it, and the direct `CLIP.M4A` loss stays. Spec 27's `M4A` pins hold                      |
| C. A, and `_converts` follows it                                       | One rule for the probe and the container step, as spec 30 intends. But `-y` on `<video src="X.M4A">` then converts to `X.M4A.mp4` (the naming bug in Out) where it saved `X.M4A`: a `-y` change, and a worse name |
| D. Leave it                                                             | `.MP3` reads `Video`; `CLIP.M4A` keeps losing its video in the wizard                                                                                                                                  |

**Recommendation: A.** It's the only option that loses no video and changes nothing under `-y`. It departs from spec 27's amendment for `.M4A`, with a measurement that amendment didn't have, and it costs real uppercase `.M4A` audio a keypress: spec 17's asymmetry, which the human approved. C is the right end state once the naming row lands; until then it would make `-y` worse.

### D3: a video declared in an `<audio>` tag

| Option                                                                          | Effect                                                                                                                                                                                         |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Flag a generic page's audio on a video container (recommended)**           | `extractor_key` `HTML5MediaEmbed`, `vcodec` `'none'`, `ext` in `MEDIA_EXTENSIONS.video`, any `acodec`. Catches every `type="video/…; codecs=…"`. No real audio changes: a page declares `video/*` only for a video. Uses yt-dlp's own extension list |
| B. The same for every extractor                                                  | Also flags site audio-only `webm` and `mp4` with a named codec (DASH, HLS audio renditions): a prompt and the `~/Videos` default for real audio, and spec 27's pinned `webm`/`opus` row flips |
| C. After the download, ask ffprobe (spec 30's `_has_media_streams`) before converting | Sees the video, but only after the download: it's spec 27 D2's option D, rejected because the printed `-y -a` replays differently. `_has_media_streams` answers "audio or video", and an mp3 or m4a cover is a video stream to ffprobe (spec 41), so it needs a new query that skips `attached_pic`, a `DownloadOptions` field to know the user didn't choose audio, a step and a message |
| D. ffprobe the URL during the probe                                              | Spec 27 D1's option B, rejected: a network read in every audio probe, with yt-dlp's headers and cookies, re-implementing part of a download                                                      |
| E. Accept it                                                                     | A strict xfail and a yt-dlp report (row 64). The video stays lost behind `Done.`                                                                                                               |

**Recommendation: A.** Spec 27's rule is "trust a codec only when something looked at the stream". On a generic page nothing did: the `type` attribute is the page's word, like the tag name, and yt-dlp keeps the half that says audio. The extractor name narrows the extra flag to that parser; it isn't the main test, which was spec 27 D1 C's objection. Neither ffprobe check is a wrapper-level way to see the video before the conversion: one runs after the download, the other re-implements it. The residual, an `audio/*` type naming a video codec (finding 2), stays lost and pinned: nothing tells it from real audio.
