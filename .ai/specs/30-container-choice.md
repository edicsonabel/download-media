# 30 — Make the container choice hold on single-format sources

Status: draft
Item: `fix/container-choice`

> **For the human: D1 is the remux-or-recode call this row has waited on.** The recommendation is C: yt-dlp's remux for mkv, yt-dlp's recode for mp4 and webm, each applied only to what the tool calls video. It rests on measurements, not on reading: every option below was run on loopback with the real yt-dlp and ffmpeg, and on one real page. D2 to D7 follow from it. **Published behaviour changes** (see SemVer): the default mp4 now re-encodes a single-format `.webm`, `.ogv`, `.flv`, `.ogx` and other non-mp4 videos to H.264/AAC, which costs time and a little quality, and `--video-format mkv` and `webm` now always give that extension. No flag, default value or path changes, so it isn't breaking by `AGENTS.md`'s list, but D7 asks you to confirm that reading. It doesn't hold up 0.4.0.

## Problem

`merge_output_format` (`downloader.py:392`) only applies when yt-dlp merges a video and an audio stream. A source with one format keeps its own extension, so the default mp4, `--video-format mkv` and `--video-format webm` all save whatever the site serves, exit 0, while the wizard's summary says the container the user picked.

Reproduced on `main` at `7a3d41c`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14. The real console script (`.venv/bin/download-media`) ran against a scratch `http.server` on `127.0.0.1`, with `HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` and `XDG_DATA_HOME` in a fresh folder per run, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, `LANG=en_US.UTF-8`, stdin from `/dev/null` (or a file of replies for the wizard), an absolute `-o` and no cookies. Each page is `<video src="<file>">`; the media are 0.5 s, 64x64 clips made with ffmpeg. Streams were read back with `ffprobe`.

```bash
download-media -y -o "$OUT" http://127.0.0.1:47613/page_vp8_vorbis_webm.html
# Done.   exit 0. On disk: "vp8_vorbis (1).webm" (vp8, vorbis)
download-media -y --video-format mkv -o "$OUT" http://127.0.0.1:47613/page_vp8_vorbis_webm.html
# Done.   exit 0. On disk: "vp8_vorbis (1).webm" (vp8, vorbis)
download-media -y --video-format mkv -o "$OUT" http://127.0.0.1:47613/page_h264_aac_mp4.html
# Done.   exit 0. On disk: "h264_aac (1).mp4" (h264, aac)
download-media -y --video-format webm -o "$OUT" http://127.0.0.1:47613/page_h264_aac_mp4.html
# Done.   exit 0. On disk: "h264_aac (1).mp4" (h264, aac)

# Wizard, Enter at every step (URL, type, quality, container, folder, cookies):
download-media http://127.0.0.1:47613/page_vp8_vorbis_webm.html
#   ✓ Container: mp4
# Done.   exit 0. On disk: Videos/"vp8_vorbis (1).webm"; printed: download-media -y <url>
# Same walk answering 2 (mkv) at the container step, on page_h264_aac_mp4.html:
#   ✓ Container: mkv
# Done.   exit 0. On disk: Videos/"h264_aac (1).mp4"; printed: download-media -y --video-format mkv <url>
```

The same happens on a real page. yt-dlp's own Wikimedia test URL offers only VP9/Opus webm and a video-only `mov`:

```bash
download-media -y -q 360 -o "$OUT" 'https://commons.wikimedia.org/wiki/File:Die_Temperaturkurve_der_Erde_(ZDF,_Terra_X)_720p_HD_50FPS.webm'
# [info] 83227919: Downloading 1 format(s): 0
# Done.   exit 0. On disk: "Die Temperaturkurve der Erde (ZDF, Terra X) 720p HD 50FPS.webm" (vp9, opus, 426x240, 45.3 s)
```

- **What happened:** the file kept the source's container, exit 0, and the summary named another one.
- **What should happen:** the file on disk is in the container the user picked, or the run says why it isn't.

## Findings

"Prototype" means a design applied at runtime from the scratchpad (a monkeypatch of `build_ydl_opts`, a subclass of yt-dlp's post-processor, and a wrapper of `_AvoidNameClash.run`), driving the real `cli.main`. `src/` wasn't touched. The fake-site shapes (merged streams, carousels, same-title items) came from a scratch extractor wired the way `fake_site` wires `FakeSiteIE`, serving the same loopback files.

### What yt-dlp offers

| What                                                       | Where (yt-dlp 2026.10.26)                  | What it does                                                                                                     |
| ---------------------------------------------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `--remux-video` → `FFmpegVideoRemuxer`                     | `__init__.py:663-667`, `postprocessor/ffmpeg.py:573-577` | `-map 0 -dn -ignore_unknown -c copy` into the target: no re-encode                                     |
| `--recode-video` → `FFmpegVideoConvertor`                  | `__init__.py:668-672`, `ffmpeg.py:538-570` | Same mapping without `-c copy`: ffmpeg's default encoders for the target container                                |
| The target is a mapping string, matched on the extension   | `ffmpeg.py:67-79` (`resolve_mapping`)      | `mp4` converts anything not already `.mp4`; `jpg>jpg/mp4` leaves `.jpg` alone. It never looks at codecs           |
| Both run only on video and audio                           | `postprocessor/common.py:115-133`, `ffmpeg.py:556` | `_restrict_to(images=False)`: skipped only when yt-dlp labels both codecs `'none'`. Unknown codecs count as video |
| `final_ext`                                                | `YoutubeDL.py:433`, `:3468-3473`; `__init__.py:778-781` | The CLI sets it from `--remux-video`/`--recode-video`, so a rerun finds the converted file and skips the download |
| Fixups skipped when a conversion follows                   | `YoutubeDL.py:3638-3643`                   | yt-dlp drops its m4a, HLS and moov fixups when a convertor will change the extension                               |
| Merge extension                                            | `YoutubeDL.py:2480-2486`, `utils/_utils.py:3091-3129` | A one-value `merge_output_format` is used even when the codecs don't fit (`return preferences[-1]`); `webm/mkv` falls back to mkv |
| Format sorting by container                                | `format_sort` field `ext`                  | `ext:webm:webm` ranks webm video and audio first                                                                 |

### Which codec pairs each one handles

yt-dlp's own `FFmpegVideoRemuxerPP` and `FFmpegVideoConvertorPP`, run directly on each source (scratch `matrix.py`):

| Source               | remux → mp4      | remux → mkv | remux → webm   | recode → mp4              | recode → mkv          | recode → webm   |
| -------------------- | ---------------- | ----------- | -------------- | ------------------------- | --------------------- | --------------- |
| h264+aac `.mp4`      | already mp4      | ok          | **fails**      | already mp4               | h264+**vorbis**       | vp9+opus        |
| h264 only `.mp4`     | already mp4      | ok          | **fails**      | already mp4               | h264                  | vp9             |
| vp9+opus `.mp4`      | already mp4      | ok          | ok             | **left as vp9+opus**      | h264+vorbis           | vp9+opus        |
| av1+aac `.mp4`       | already mp4      | ok          | **fails**      | **left as av1+aac**       | h264+vorbis           | vp9+opus        |
| vp8+vorbis `.webm`   | **fails**        | ok          | already webm   | h264+aac                  | h264+vorbis           | already webm    |
| vp9+opus `.webm`     | vp9+opus in mp4  | ok          | already webm   | h264+aac                  | h264+vorbis           | already webm    |
| av1+opus `.webm`     | av1+opus in mp4  | ok          | already webm   | h264+aac                  | h264+vorbis           | already webm    |
| h264+aac `.mkv`      | ok               | already mkv | **fails**      | h264+aac (re-encoded)     | already mkv           | vp9+opus        |
| h264+aac `.flv`/`.mov` | ok             | ok          | **fails**      | h264+aac (re-encoded)     | h264+vorbis           | vp9+opus        |
| theora+vorbis `.ogv` | **fails**        | ok          | **fails**      | h264+aac                  | h264+vorbis           | vp9+opus        |
| opus only `.webm`    | opus in mp4      | ok          | already webm   | aac                       | vorbis                | already webm    |
| aac only `.m4a`      | aac in mp4       | ok          | **fails**      | aac (re-encoded)          | vorbis                | opus            |

- **Remux into mkv never failed** (13 of 13), and is lossless.
- **Remux into mp4 "works" for VP9, AV1 and Opus,** but the file then breaks what mp4 promises: the wizard says `works on WhatsApp, iPhone, TV (H.264, up to 1080p)` (`i18n.py:129`), and the comment at `downloader.py:386-387` says those reject VP9/AV1 even inside mp4. It fails outright on Vorbis and Theora.
- **Remux into webm fails on anything with H.264 or AAC,** which is most of the web outside YouTube.
- **Recode never failed on these,** but it re-encodes even streams that already fit (h264 in `.mkv`, `.flv`, `.mov`), turns AAC into Vorbis for mkv for no gain, and leaves a VP9 or AV1 `.mp4` alone, since the mapping only reads the extension.
- Every failure is ffmpeg's `Conversion failed!`.

### What a recode costs

10-second clips, ffmpeg n9.0.2 with the defaults yt-dlp passes (none), 12 threads, measured with scratch `cost.py`. "Noisy" clips add temporal noise so the encoder can't coast. SSIM is against the source.

| Clip                      | To   | Remux wall | Recode wall / CPU | SSIM  | Size (source → recode) | Peak RSS |
| ------------------------- | ---- | ---------- | ----------------- | ----- | ---------------------- | -------- |
| 720p VP9, synthetic       | mp4  | 0.11 s     | 2.1 s / 14 s      | 0.975 | 1.95 → 3.20 MB         | 450 MB   |
| 720p VP9, noisy           | mp4  | 0.12 s     | 3.2 s / 22 s      | 0.974 | 2.73 → 3.05 MB         | 430 MB   |
| 1080p VP9, noisy          | mp4  | 0.19 s     | 7.0 s / 60 s      | 0.965 | 7.17 → 11.68 MB        | 850 MB   |
| 1080p VP9, noisy          | mkv  | 0.13 s     | 7.9 s / 63 s      | 0.985 | 7.17 → 11.61 MB        | 860 MB   |
| 720p H.264, noisy         | webm | fails      | 22.1 s / 61 s     | 0.979 | 4.65 → 2.45 MB         | 270 MB   |
| 1080p H.264, noisy        | webm | fails      | 55.1 s / 149 s    | 0.975 | 16.19 → 8.87 MB        | 470 MB   |

So a 10-minute 1080p VP9 video takes about 7 minutes to become an mp4 on this machine, and a 10-minute 1080p H.264 video about 55 minutes to become a webm (libvpx-vp9's defaults are slow). The real 240p, 45 s Wikimedia clip took 2.6 s longer than the plain download. The quality loss is small (SSIM 0.965 to 0.98), and the H.264 file comes out 10 to 65% larger. yt-dlp prints `[VideoConvertor] Converting video from webm to mp4; Destination: …` and nothing more until ffmpeg ends.

### Each option through the tool

Single-format generic pages, `-y`, one run each (scratch `grid.sh`, `harness.py`):

- **Remux everywhere:** `vp8_vorbis.webm` and `theora.ogv` under mp4, and `h264_aac.mp4`, `.flv`, `.mkv`, `theora.ogv`, `aac_only.m4a` under webm end in `ERROR: Postprocessing: Conversion failed!`, `Download failed`, exit 1, with the download **and an empty target file** (`vp8_vorbis (1).mp4`, no streams) left behind. In single mode on a carousel the run stops there: the items after it are never fetched. `vp9_opus.webm` under mp4 saves a VP9 `.mp4`.
- **Recode everywhere:** every run saves the chosen extension, exit 0. Under mkv, `h264_aac.mp4` becomes h264+vorbis, and a direct `image.jpg` becomes a one-frame H.264 `.mkv`.
- **Remux, recode if remux fails:** every run saves the chosen extension, exit 0, nothing left behind; `vp9_opus.webm` under mp4 still comes out VP9.
- **By container (mkv remux, mp4 and webm recode):** every run saves the chosen extension, exit 0; mp4 always holds H.264/AAC unless the source already was an `.mp4`.
- **Any option, with yt-dlp's only guard:** a direct `image.jpg` in video mode becomes `image (1).mp4` (one MJPEG or H.264 frame), and `tiny.gif` becomes a video. An audio item picked with `all` on a mixed page (`a1.m4a`) becomes an audio-only `.mp4`. `_restrict_to(images=False)` doesn't catch either: the generic extractor leaves their codecs unknown.
- **Carousel images** saved by spec 01's thumbnail writer are never touched: `Clip [i1].jpg` stayed a `.jpg` under every option, since they never reach `post_process`.

### Merged sources (`main`)

| Streams          | mp4                    | mkv          | webm                                                                                  |
| ---------------- | ---------------------- | ------------ | ------------------------------------------------------------------------------------- |
| h264 + aac       | `.mp4`                 | `.mkv`       | `ERROR: Postprocessing: Conversion failed!`, `Download failed`, exit 1, `Clip.fv.mp4`, `Clip.fa.mp4` and an empty `Clip.temp.webm` left |
| vp9 + opus       | `.mp4` holding VP9/Opus | `.mkv`      | `.webm`                                                                               |
| vp9 + aac (m4a)  | `.mp4` holding VP9/AAC | `.mkv`       | same failure as the first row                                                         |

The container already holds on merges, except for webm on H.264 or AAC streams, which fails loudly. With `merge_output_format: "webm/mkv"` and a recode after it, all three save a `.webm`, exit 0 (prototype). The same trick for mp4 (`mp4/mkv`) is not safe: yt-dlp's codec table (`_utils.py:3100-3109`) doesn't know a bare `aac`, so an H.264/AAC merge went to mkv and was then re-encoded to mp4 for nothing.

A VP9 merge under the default mp4 keeps VP9 inside the `.mp4`: the extension holds, the "plays everywhere" promise doesn't. That needs a codec check, and stays out (see Out).

### Reruns and same-title clashes

With the conversion but nothing else, both regress:

- **A rerun downloads and converts again** (`Converting video from webm to mp4` twice, one file). `main` says `has already been downloaded`. Setting yt-dlp's `final_ext` to the container, as yt-dlp's CLI does, restores that: the second run printed `has already been downloaded` and converted nothing.
- **A second video with the same title overwrites the first.** `fake.test/single-webm` then `fake.test/single-webm-b`, both titled `Clip`: one `Clip.mp4`, the first one's content gone, `Done.` both times. `_AvoidNameClash` (`downloader.py:425-436`) looks for `Clip.webm`, which the conversion deleted. Looking for the converted name gave `Clip.mp4` and `Clip [single-webm-b].mp4`.
- An image in video mode reruns into a second copy (`image (1) [page_image_jpg-1].jpg`) on `main` too, since ffmpeg can't tag a `.jpg`. That's row 25 and 47's, unchanged.

### A conversion that fails on real media

An mkv carrying an attachment stream (a font, or a text file) can't go into mp4. With yt-dlp's plain recode: `Conversion failed!`, `Download failed`, exit 1, the `.mkv` and an empty `.mp4` left. Under `-p`, `1 of 2 items downloaded (0 without downloadable media, 1 failed).`, `Done.`, with the empty `.mp4` left. In single mode on a carousel, the item after it is never fetched. Catching the error, removing the empty target and keeping the download (prototype) gave `Done.`, exit 0, both items saved, and the one line the prototype printed. Into webm the same file converted fine.

### Format sorting

A page offering both an mp4 and a webm format, with no codecs: `--video-format webm` picked the mp4 (`Downloading 1 format(s): alt`) and re-encoded it to VP9. With `format_sort: ["ext:webm:webm"]`, it picked the webm and converted nothing. The default mp4 already picks the mp4 on both pages tried (`vcodec:h264` plus yt-dlp's own extension order), so mp4 needs nothing new.

### HLS

`<video src="hls.m3u8">` under mkv: yt-dlp skipped its MPEG-TS fixup, as `YoutubeDL.py:3638-3643` says, and the remux gave a clean h264+aac `.mkv`. Under the default mp4 the fixup ran and nothing was converted, as on `main`.

### The existing suite

`tests/test_e2e.py` on `main`: 1026 passed, 2 xfailed. With the recommended design applied at runtime (scratch `proto_plugin.py`, `pytest -p proto_plugin`: D1 C, D2 B, D3 A, D4 A, D5 A, `final_ext` and the `_AvoidNameClash` change): 997 passed, **29 failed**, 2 xfailed. The same 29 fail without `final_ext`, D4 and D5. Every failure pins "the raw file as served" in video mode, which is this bug:

- 10 save a single-format `.webm`, `.ogg`, `.ogv`, `.asf`, `.m4b`, `.wma` as is (`test_wizard_reads_webm_and_hls_without_codec_info_as_video[webm…]`, `test_wizard_keeps_the_video_in_a_container_yt_dlp_lists_as_audio[…]`, `test_wizard_tells_ogg_video_from_ogg_audio_on_one_page_and_repeats_it`, `test_wizard_keeps_audio_in_such_a_container_raw_when_nothing_says_it_is_audio[…]`). They now get an `.mp4`; the `<video src="tiny.ogg">` holding only audio gets an audio-only `.mp4`.
- 17 use a video-mode `.ogx` or an extensionless direct link to reach spec 29's `untagged` warning (`test_wizard_keeps_an_ogx_and_reports_it_untagged`, `test_a_saved_file_ffmpeg_cannot_tag_is_a_success[theora.ogx|…/stream]`, and the `untaggable` family). Converted to mp4, ffmpeg can tag them, so the warning no longer fires there. It still fires on files the conversion skips: `test_a_saved_file_ffmpeg_cannot_tag_is_a_success[…clip.weba|clip.m4r|clip.f4a|clip.wav|clip.oga]` all pass.
- 1 expects `subs_not_embedded` on an `.flv` (`test_a_container_that_cannot_hold_every_file_warns[flv]`): converted to mp4, the subtitles now embed.

Two more design choices are measured by the same count. Converting everything except `jpg`/`png`/`webp` (yt-dlp's guard plus a mapping) failed 80 tests; skipping what yt-dlp labels audio and `gif`/`jpeg` too, 31; also skipping extensions yt-dlp lists as audio (the probe's rule), 29. The two that the last step fixed were an audio `a1.m4a` carousel item, and `test_yt_dlp_strips_video_from_m4a_so_reading_it_as_audio_loses_nothing`.

## Goal

In video mode, the file on disk is in the container the user picked, for single-format sources too: mp4 holds H.264/AAC whenever the source wasn't an mp4 already, mkv holds the source's streams untouched, webm holds VP8/VP9/AV1 with Vorbis/Opus. When a video can't be converted, the run keeps it, says so by name, and still ends in `Done.`. Images and audio keep their own extension.

## Scope

### In

- One container step after the download, video mode only: yt-dlp's remux for mkv, yt-dlp's recode for mp4 and webm (D1), on what the tool calls video (D2).
- A failed conversion of real media keeps the download, with a new warning (D3).
- `final_ext` and `_AvoidNameClash` follow the converted name, so reruns and same-title items behave as on `main`.
- webm prefers webm formats (D4), and merges that can't go into webm go through mkv first (D5).
- One README line.

### Out

- **VP9 or AV1 inside an `.mp4`** (a merge with no H.264 on offer, or a single-format `.mp4` holding them). The extension holds; the codecs don't play everywhere. Fixing it needs a codec check after the download and a recode of files yt-dlp's mapping calls done. For the planner, as a new row.
- **High bit depth or HDR sources.** libx264 keeps 10-bit, which gives an H.264 High 10 `.mp4` that many devices refuse. Not measured; for the same row.
- **Quality settings for the recode** (CRF, preset). yt-dlp passes none and ffmpeg's defaults gave SSIM 0.965 to 0.98. Exposing them would be a new flag.
- **Files yt-dlp labels audio in video mode** (spec 27's `.ogg`, `.wma`, `.asf`, `.m4b` and `.webm` under `audio/*` or `<audio>`, spec 29's `<audio src>` with no extension). They keep the raw file, as spec 27 D2 and spec 29 D7 decided, though the summary says mp4. They still get spec 29's `untagged` warning where it applies.
- **Images in video mode** (a direct `.jpg`, `.png`, `.webp`, `.gif`, `.jpeg`), and carousel images. They keep their extension.
- **`-a`** keeps its own extraction step and isn't touched. **`-l`** lists formats and converts nothing.
- **A cookie-less retry** runs the whole download again, conversion included. Same as any other step today.
- **The image rerun copy** (row 25 and 47's).
- **`--help` wording.** `--video-format`'s help is a hardcoded English string, and moving help text to `t()` is row 02's. The text stays true.

## Files

| Action | File                              |
| ------ | --------------------------------- |
| modify | `src/download_media/downloader.py` |
| modify | `src/download_media/probe.py`      |
| modify | `src/download_media/cli.py`        |
| modify | `src/download_media/i18n.py`       |
| modify | `README.md`                        |
| modify | `tests/conftest.py`                |
| modify | `tests/test_e2e.py`                |
| modify | `tests/test_downloader.py`         |

`wizard.py`, `equivalent.py`, `history.py` and `config.py` don't change. No new flag, default, path or dependency. One new message (D3).

## Design

Written for the recommended option of each decision.

### `downloader.py`

1. **What gets converted (D2).** Move `_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO` and `_ext_says_audio` from `probe.py:186-193` into `downloader.py`, and import them back in `probe.py` (it already imports from `downloader`; the reverse would be a cycle). Add:

   ```python
   _IMAGE_EXTS = frozenset({*MEDIA_EXTENSIONS.thumbnails, "gif", "jpeg"})

   def _converts(info: dict[str, Any]) -> bool:
       """What the probe calls video: the container choice applies to it."""
       ext = (info.get("ext") or "").lower()
       vcodec = info.get("vcodec")
       if vcodec == "none" or ext in _IMAGE_EXTS:
           return False
       return bool(vcodec) or not _ext_says_audio(ext)
   ```

   It reads the info yt-dlp hands the post-processor: the chosen format's `ext`, `vcodec` and, for a merge, the merged result.

2. **The container step (D1, D3).** One mixin and two thin subclasses of yt-dlp's own post-processors:

   - `_RemuxTo(FFmpegVideoRemuxerPP)` for mkv, `pp_key` `"VideoRemuxer"`.
   - `_RecodeTo(FFmpegVideoConvertorPP)` for mp4 and webm, `pp_key` `"VideoConvertor"`.
   - Both take the container as yt-dlp's `preferedformat` and a `not_converted: list[str]`. Their `run`: return `[], info` untouched, and silently, when `not _converts(info)` or when the file is already in the container. yt-dlp's own skip prints `[VideoConvertor] Not converting media file "…"; already is in target format mp4`, measured on every merged mp4: that would add a line to nearly every default download. Otherwise note the target path (`replace_extension(filepath, target, ext)` with `target` from `resolve_mapping`) and whether it existed, and call yt-dlp's `run`. On `PostProcessingError`: remove the target if this step created it, then do what `_TagSource` does (`downloader.py:560-571`): if `_has_media_streams(info["filepath"])` is false, re-raise (a junk body is still a failed download); otherwise append the file's name to `not_converted` and return `[], info`.
   - Keep yt-dlp's `run`, its mapping and its conversion messages (`Converting video …`, `Remuxing video …`, `Deleting original file …`). Nothing re-implements the ffmpeg call.

3. **`download()`** adds the step in video mode, right after `ydl.add_post_hook(keep)` and before `_EmbedCover` and `_TagSource` (`downloader.py:665-669`), so the file is tagged and gets its subtitles in its final container: `ydl.add_post_processor(_RemuxTo(...) if fmt == "mkv" else _RecodeTo(...), when="post_process")`. `DownloadResult` gains `not_converted: tuple[str, ...] = ()`.

4. **`build_ydl_opts`, video branch:**
   - `final_ext: opts.video_format`, as yt-dlp's CLI sets it (`__init__.py:778-781`), so a rerun finds the converted file.
   - webm: `format_sort: ["ext:webm:webm"]` (D4) and `merge_output_format: "webm/mkv"` (D5). mp4 and mkv keep `merge_output_format` as today, and mp4 keeps its `format_sort`.
   - `postprocessors` stays `[]`: the step needs `not_converted`, so `download()` adds it, as it adds `_TagSource`.

5. **`_AvoidNameClash`** takes the container as well (for example `_AvoidNameClash(final_ext, container=...)`), and checks `path.with_suffix(f".{container}")` when there's no audio `final_ext` and `_converts(info)`. Otherwise a second video with the same title overwrites the first (Findings). Pass `opts.video_format`, never `merge_output_format`: under D5 that is `webm/mkv`, and a prototype that read it ended every `-s --video-format webm` run in `ValueError: Invalid name 'Clip.webm/mkv'` from `with_suffix`.

### `cli.py`, `i18n.py`

After `untagged` (`cli.py:392-393`): `for name in result.not_converted: warn(t("not_converted", name, effective.video_format))`. New key in both blocks, for example:

- en `"not_converted": "{0}: couldn't be converted to {1}; it's kept as downloaded."`
- es `"not_converted": "{0}: no se pudo convertir a {1}; se conserva tal como se descargó."`

The item counts as saved, so `N of M` doesn't move, and the run ends in `Done.`.

### What the user sees

| Where                 | Effect                                                                                                                  |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Disk                  | The chosen extension for every video; images, audio-labelled files and audio items keep theirs                            |
| yt-dlp's output       | `[VideoConvertor] Converting video from webm to mp4; Destination: …` or `[VideoRemuxer] Remuxing video from mp4 to mkv; …`, then `Deleting original file …` |
| Summary               | Unchanged. `Container: mp4` is now what's on disk                                                                        |
| Printed command       | Unchanged text. Run again, it converts the same way, and with `final_ext` a rerun in the same folder downloads nothing     |
| History               | Unchanged: one row per URL that saved, `video`, the folder. The log holds no extension                                    |
| Warnings              | New `not_converted`. Spec 29's `untagged` stops firing for video-mode `.ogx` and extensionless links, which become taggable `.mp4` files |

### README

Under Features, one line: videos are saved in the chosen container, mkv by remuxing (no quality loss), mp4 and webm by re-encoding when the site offers no such format, which can take a while on long videos.

## Cases

| Case                                                           | Expected                                                                                                     |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| wizard, single-format `.webm`, Enter                           | `Container: mp4`, `Videos/<title>.mp4` H.264/AAC, `Done.`, `download-media -y <url>`                          |
| wizard, mkv on an `.mp4`                                       | `.mkv`, same streams, no re-encode                                                                            |
| `-i --video-format webm` on an `.mp4`                          | Preselected webm; `.webm` VP9/Opus                                                                           |
| direct with preview, `-y`                                      | Same files as the wizard                                                                                      |
| `-p` on a carousel of images and webm videos                   | Images `.jpg` untouched; videos `.mp4`; a video that can't convert stays as downloaded, warned, counted saved |
| `-P`, `--playlist-items`                                       | Per item, as above                                                                                            |
| single mode on a carousel                                      | yt-dlp fetches every item; each video converted; a failed conversion no longer stops the run                  |
| `-l`                                                           | Unchanged: lists formats, converts nothing                                                                    |
| `-a`                                                           | Unchanged                                                                                                     |
| with / without cookies                                         | No difference. A cookie-less retry downloads and converts again                                               |
| merged mp4 or mkv                                              | Unchanged, and no line from the container step                                                                |
| merged H.264/AAC under webm                                    | `.webm`, exit 0, nothing left behind (D5). Today: `Download failed`, exit 1, three leftovers                   |
| HLS under mkv                                                  | `.mkv`, h264+aac                                                                                              |
| rerun, same folder                                             | `has already been downloaded`, no conversion, one file                                                        |
| two videos with the same title                                 | `Clip.mp4` and `Clip [<id>].mp4`                                                                              |
| `-s`                                                           | Subtitles embed in the converted file. An `.flv` source now gets them embedded                                |
| a direct `.jpg`, `.gif`, `.png` in video mode                  | Unchanged                                                                                                     |
| spec 27's labelled audio, Enter (video)                        | Unchanged raw file                                                                                            |
| `<video src>` on audio-only `.ogg` or `.m4b` (vcodec unknown)  | Audio-only `.mp4`. Today: raw file                                                                            |
| `.ogx` video, extensionless direct link                        | `.mp4`, tagged, no `untagged` warning. Today: raw file and the warning                                        |
| a junk body                                                    | Still `Download failed`, exit 1, no converted file left; the junk body itself stays, as today (row 46). Measured under mkv and webm |
| 404, total failure                                             | Unchanged                                                                                                     |

## Acceptance criteria

Every **[fix]** test fails on `main` (shown by reverting the fix, `constraints.md` Verification 5); every **[pin]** passes before and after. Anything that depends on yt-dlp or ffmpeg runs `cli.main` on `fake_site` or a generic loopback page, and reads streams back with `ffprobe`. Assert codec families, not exact encoders, where ffmpeg picks the encoder (webm may come out VP8 or VP9).

Fixtures: `media_url` gains `attached.mkv` (`tiny.mp4` stream-copied with `-attach` of a small text file, `mimetype=text/plain`; no new encoder). `FakeSiteIE` gains `single-webm` and `single-webm-b` (one `tiny.webm` format each, same title, different pages), `attached` (one `attached.mkv` format), `attached-carousel` (`attached` plus a `tiny.mp4` video) and `mp4-or-webm` (an mp4 and a webm format, no codecs). The existing `merged`, `webm.html`, `tiny.webm`, `theora.ogg`, `tiny.flv`, `jpg-alone.html`, `tiny.gif` and `ig-carousel` are reused.

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] **[fix]** `test_e2e.py::test_the_default_mp4_converts_a_single_format_source[webm.html|tiny.webm|video-ogg.html|tiny.flv]`: `-y`, exactly one file, `.mp4`, video `h264` and audio `aac`, no source file left, `Done.`, exit 0
- [ ] **[fix]** `test_e2e.py::test_wizard_summary_matches_the_saved_container`: the wizard on `webm.html` with `SINGLE_WALK` shows `Container: mp4` and saves one `.mp4`; with `-i --video-format mkv` on `tiny.mp4`, `Container: mkv` and one `.mkv`
- [ ] **[fix]** `test_e2e.py::test_mkv_remuxes_without_re_encoding[tiny.mp4|webm.html|theora.ogg|tiny.flv]`: `-y --video-format mkv`, one `.mkv` whose codec names equal the source's, `Remuxing video` in the output, no `Converting video`
- [ ] **[fix]** `test_e2e.py::test_webm_re_encodes_what_it_cannot_hold`: `-y --video-format webm` on `tiny.mp4`, one `.webm`, video in `{vp8, vp9, av1}`, audio in `{vorbis, opus}`
- [ ] **[pin]** `test_e2e.py::test_a_file_already_in_the_container_is_left_alone[mp4-tiny.mp4|webm-tiny.webm|mkv-merged]`: no `[VideoConvertor]` or `[VideoRemuxer]` line at all, codecs unchanged
- [ ] **[fix]** `test_e2e.py::test_a_video_that_cannot_be_converted_is_kept[es|en]`: `-y` on `fake.test/attached`: `Clip.mkv` kept, no `.mp4` left, `t("not_converted", "Clip.mkv", "mp4")` once, `Done.`, exit 0, one history row
- [ ] **[fix]** `test_e2e.py::test_a_failed_conversion_does_not_stop_a_carousel[single|p]`: `-y` and `-y -p` on `attached-carousel`: both items saved, the warning once, no `partial_result` line, exit 0
- [ ] **[pin]** `test_e2e.py::test_a_junk_body_still_fails_when_converted`: `-y --video-format mkv` on a junk body: `Download failed`, exit 1, no `.mkv` left
- [ ] **[fix]** `test_e2e.py::test_a_rerun_after_a_conversion_downloads_nothing`: `-y` twice on `webm.html` into one folder: one `.mp4`, the second run says `has already been downloaded` and has no `Converting video`
- [ ] **[pin]** `test_e2e.py::test_same_title_videos_both_survive_the_conversion`: `-y` on `single-webm` then `single-webm-b` into one folder: `Clip.mp4` and `Clip [single-webm-b].mp4`, different content. Passes on `main` (as `.webm`); fails with the conversion but without the `_AvoidNameClash` change
- [ ] **[pin]** `test_e2e.py::test_images_and_audio_keep_their_extension_in_video_mode`: `jpg-alone.html`, `tiny.gif` and the `query-carousel` audio item keep `.jpg`, `.gif`, `.m4a`; `-y -p` on `ig-carousel` keeps its images `.jpg`
- [ ] **[fix]** `test_e2e.py::test_webm_prefers_a_webm_format` (D4): `-y --video-format webm` on `mp4-or-webm` downloads the webm format and prints no `Converting video`
- [ ] **[fix]** `test_e2e.py::test_webm_on_merged_h264_streams` (D5): `-y --video-format webm` on `merged`: one `Clip.webm`, exit 0, no `.fv.`, `.fa.` or `.temp.` file left
- [ ] **[pin]** `test_e2e.py::test_merges_into_mp4_and_mkv_are_unchanged`: `merged` with the default and with mkv: `Clip.mp4` h264/aac and `Clip.mkv`, no `[VideoConvertor]` or `[VideoRemuxer]` line
- [ ] **[pin]** `test_e2e.py::test_subtitles_embed_in_the_converted_file`: `-y -s` on `subs-flv` saves `Clip.mp4` with one subtitle stream and keeps `Clip.en.vtt` (replaces the `flv` case of `test_a_container_that_cannot_hold_every_file_warns`)
- [ ] The 29 tests listed in Findings are updated to the new files, each named in the commit body. The 17 `untagged` ones move to a source the conversion skips (`typed/audio/webm/audio.webm/clip.weba` or another audio-labelled file already in `media_url`), so spec 29's warning stays covered in video mode; none is deleted without saying why
- [ ] unit `test_downloader.py`: `build_ydl_opts` sets `final_ext` to the container in video mode only; webm gets `format_sort == ["ext:webm:webm"]` and `merge_output_format == "webm/mkv"`; mp4 and mkv keep today's values; `test_non_mp4_container_keeps_default_codec_order` still holds for mkv
- [ ] unit `test_downloader.py::test_converts_reads_like_the_probe`: one table over `vcodec` `'none'`, a named codec, unknown with `mp4`, `ogg`, `ogx`, `m4a`, `mp3`, `jpg`, `JPG`, `gif`
- [ ] unit `test_downloader.py`: `_AvoidNameClash` with a container checks the converted name, and an image's own name
- [ ] `not_converted` exists in both `i18n.py` blocks (the parity test)

## Live check

Needed once, at validation: the Wikimedia extractor's real formats (VP9/Opus webm plus a video-only `mov`) are a shape the fixtures don't have. It's yt-dlp's own test URL, public, no cookies. Into `tmp/`, alone:

```bash
U='https://commons.wikimedia.org/wiki/File:Die_Temperaturkurve_der_Erde_(ZDF,_Terra_X)_720p_HD_50FPS.webm'
.venv/bin/download-media -y -q 360 -o tmp/container-choice "$U"
.venv/bin/download-media -y -q 360 --video-format mkv -o tmp/container-choice-mkv "$U"
```

On disk: one `.mp4` holding h264 and aac in the first folder, one `.mkv` holding vp9 and opus in the second, each 45 s. Measured with the prototype during planning: `.mp4`, h264+aac, 426x240, 45.3 s, 2.6 s longer than `main`'s run.

## SemVer and the 0.4.0 review list

**Minor** on its own: a new message (`not_converted`), and `AGENTS.md` counts new messages as minor. 0.4.0 is already a minor.

**Breaking?** Not by `AGENTS.md`'s list: no flag is renamed, no default value changes (`DEFAULT_VIDEO_FORMAT` stays `mp4`), no path or state format moves. But what the default *produces* changes on some sources, which is close to "alters defaults". D7 puts that reading to the human. If it's read as breaking, the commit needs `BREAKING CHANGE:` and the human's approval.

If this ships in 0.4.0, add to the review list in `PLAN.md`:

1. The default mp4 re-encodes single-format non-mp4 videos to H.264/AAC: about 0.7x real time at 1080p on 12 threads, SSIM about 0.97, files 10 to 65% larger. A script that expected `.webm`, `.ogv`, `.flv`, `.ogx` or `.unknown_video` files gets `.mp4`.
2. `--video-format mkv` remuxes every video to `.mkv`. `--video-format webm` re-encodes H.264 sources, about 5.5x real time at 1080p, prefers webm formats, and no longer fails on H.264 merges.
3. A video that can't be converted is kept, with the new warning, and no longer fails the run or stops a carousel.
4. Spec 29's `untagged` warning no longer appears for video-mode `.ogx` files and extensionless links: they become `.mp4` files ffmpeg can tag.

Release note: "Videos are now saved in the container you choose even when the site offers a single file in another one. mkv remuxes without losing quality; mp4 and webm re-encode when they have to, which can take a while on long videos. A video that can't be converted is kept as downloaded, and the run says so."

## Open decisions

D1 is the human's call, and the reason this row waited. D2 to D7 have a recommended option each.

### D1: remux or recode

| Option | What runs | Into mp4 | Into mkv | Into webm | Fails on | Cost |
| ------ | --------- | -------- | -------- | --------- | -------- | ---- |
| A. Remux | yt-dlp `FFmpegVideoRemuxer` | VP9/AV1/Opus kept inside the `.mp4`; fails on Vorbis, Theora | always works, lossless | fails on H.264, AAC, Theora | 2 of 13 sources into mp4, 8 of 13 into webm: `Download failed`, exit 1, an empty target left, a single-mode carousel stops | none |
| B. Recode | yt-dlp `FFmpegVideoConvertor` | H.264/AAC from any non-`.mp4`; a VP9 `.mp4` left alone | needless re-encode (h264→h264, aac→vorbis) | VP9/Opus | an attachment into mp4 (D3) | 7 s per 10 s of 1080p into mp4 or mkv, 55 s into webm |
| **C. By container (recommended)** | remux for mkv, recode for mp4 and webm | H.264/AAC from any non-`.mp4` | always works, lossless | VP9/Opus | nothing measured but an attachment into mp4 (D3) | mp4: 0.7x real time at 1080p, only when the page has no mp4; mkv: none; webm: up to 5.5x real time, only on H.264 sources |
| D. Remux, else recode | a ~10-line subclass chaining the two | VP9/AV1 kept inside the `.mp4` | lossless | lossless when it fits, else VP9/Opus | nothing | as C for webm; none for mp4 |
| E. No conversion | nothing; a notice when the extension differs | `.webm` stays | `.mp4` stays | `.mp4` stays | nothing | none, and the default still doesn't play everywhere |

**Recommendation: C.** It's yt-dlp's own `--remux-video mkv` and `--recode-video mp4|webm`, with `final_ext` as yt-dlp's CLI sets it, so the tool wraps instead of re-implementing. It keeps each container's promise as the wizard words it: mkv "best quality" (no re-encode), mp4 "H.264, plays everywhere", webm "VP9/AV1". The cost lands only where the promise can't otherwise be kept: a page with no mp4 for the default, which `format_sort` already avoids whenever an mp4 exists. A is free but fails loudly on common pairs and makes VP9 `.mp4` files that break the default's promise. B wastes quality on mkv. D keeps VP9 in mp4. E fixes only the report.

**Effect on the rest, per option:** the equivalent command's text is the same under all five; under A to D, running it again produces the converted file, and with `final_ext` (A to D) a rerun in the same folder downloads nothing. History rows don't change under any option (they hold no extension). The summary becomes true under A to D, except A's failures, which end in `Download failed`; under E it stays wrong and a notice follows it.

### D2: what the container step applies to

- **A.** Everything yt-dlp's `_restrict_to(images=False)` lets through, plus a mapping that leaves `jpg`, `png` and `webp` alone. Audio items picked with `all` become audio-only `.mp4` files, a `.gif` becomes a video, spec 27's labelled files are converted. 80 existing e2e tests change.
- **B (recommended).** What the probe calls video (`_format_kind` == video: `vcodec` not `'none'`, and an unknown codec only when the extension isn't one yt-dlp lists as audio), minus `gif` and `jpeg`. Audio items, labelled audio and images keep their extension. 29 tests change, all of them this bug.
- **C.** Only formats with a named video codec. The generic `<video src>` pages, which are this row's reproduction, would stay unconverted.

### D3: a conversion that fails on real media

- **A (recommended).** Keep the download, remove the empty target, warn with the new `not_converted`, count it saved, `Done.`. A junk body still fails, by the same ffprobe check `_TagSource` uses. Matches spec 29: a file that is on disk is never reported as a failed download.
- **B.** yt-dlp's default: `Download failed`, exit 1, the file and an empty target left; under `-p` a failed item; in single mode on a carousel the later items are never fetched.

### D4: webm prefers webm formats

- **A (recommended).** `format_sort: ["ext:webm:webm"]` for webm: a page that offers a webm gets it, with no re-encode. Measured: without it, an mp4-or-webm page took the mp4 and spent a VP9 encode.
- **B.** Leave the sort as today and re-encode.

mp4 needs nothing: `vcodec:h264` already picked the mp4 on both pages tried. mkv takes anything.

### D5: webm on merged H.264 or AAC streams

- **A (recommended).** `merge_output_format: "webm/mkv"` for webm only: yt-dlp merges into mkv when the codecs don't fit, and C's recode makes the webm. Measured: three merges that fail on `main` save a `.webm`, exit 0, nothing left behind. One option, the same promise as the rest of this item.
- **B.** Leave the merge failure (`Download failed`, exit 1, `.fv`, `.fa` and `.temp` files left) for a new row. Then `--video-format webm` saves a webm from a single H.264 file but fails on the same streams split in two.

Not for mp4: `mp4/mkv` sent an H.264/AAC merge to mkv and re-encoded it (Findings).

### D6: saying a recode is under way

- **A (recommended).** yt-dlp's own `[VideoConvertor] Converting video from … to …` line, as for every other yt-dlp step. No new message.
- **B.** A `t()` notice before a recode, in both languages, saying it re-encodes and may take a while. It would need a hook before the step, and a 55-minute webm encode still shows no progress.

### D7: SemVer reading

- **A (recommended).** Not breaking: `DEFAULT_VIDEO_FORMAT` and every flag stay as they are; the default now does what it says (`mp4 (H.264, plays everywhere)`, `README.md:94`). Minor for the new message. The changes go on the 0.4.0 review list above.
- **B.** Breaking, since the default produces different files on some sources: `BREAKING CHANGE:` in the commit, and the human's approval. Under `AGENTS.md`'s versioning that points at a major bump.
