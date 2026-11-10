# 41 — Keep the cover image in `-a -f m4a`

Status: done
Item: `fix/m4a-cover`

> **For the 0.4.0 review list:** D1 to D4 are recommendations, made for approval under the human's overnight delegation. The fix changes published behaviour in four ways, none of them breaking by `AGENTS.md`'s list. They are listed under "Behaviour changes for the 0.4.0 review" at the end.

## Problem

`-a -f m4a` saves an `.m4a` with no cover image, prints `Done.` and exits 0. Nothing tells the user that the cover is missing.

Reproduced on `main` at `a30d433`, with yt-dlp 2026.10.26, ffmpeg n9.0.2, Python 3.14 and no `mutagen` or AtomicParsley. The real console script ran under `env -i` against a scratch loopback server (`python -m http.server`). `HOME` and `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` pointed into a scratch folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` were unset, `LANG=en_US.UTF-8`, `-o` was absolute and no cookies were used. `audio.html` is the `fake_site` `audio` page: `<audio src="tiny.m4a">` with an absolute `og:image` on a real `tiny.jpg`. Streams were read with `ffprobe -show_entries stream=codec_type,codec_name:stream_disposition=attached_pic`.

```bash
download-media -y -a -f m4a -o "$OUT" http://127.0.0.1:18941/audio.html
# [info] Writing video thumbnail 0 to: …/audio (1).cover.jpg
# [ExtractAudio] Not converting audio …/audio (1).m4a; file is already in target format m4a
# [EmbedThumbnail] Neither mutagen nor AtomicParsley was found. Falling back to ffmpeg
# [EmbedThumbnail] ffmpeg: Adding thumbnail to "…/audio (1).m4a"
# [Metadata] Adding metadata to "…/audio (1).m4a"
# Done.
# exit 0. On disk: only `audio (1).m4a`, holding a single aac stream and no attached picture.
```

- **What happened:** the cover was embedded, then the metadata step rewrote the file and dropped it. The run said `Done.` with no `no_cover` warning.
- **What should happen:** the `.m4a` keeps its cover, as the `.mp3` from the same page already does.

**Cause.** `download()` adds `_EmbedCover` before `_TagSource` (`downloader.py:667-669` on `main`). yt-dlp's metadata step maps m4a with `-vn -acodec copy` (`FFmpegMetadataPP._options`, `postprocessor/ffmpeg.py:671-675`), which drops every video stream, the attached picture included. yt-dlp's own CLI runs the two steps the other way round: `FFmpegMetadata`, then `EmbedThumbnail` (`yt_dlp/__init__.py:700-713`). Its comment says metadata goes after anything that changes the container. The cover step never changes it.

## Goal

With `-a`, every format that can hold a cover keeps it, and the tags (title, date, source URL) stay as they are today. Video mode doesn't change.

## Findings

All results come from runs. "Swap" means the design below (two lines moved) applied to a scratch copy of `src/download_media`, run through the real console entry point. `src/` itself wasn't touched. "mutagen" means `mutagen` 1.48.1, installed with `pip --target` into a scratch folder and put on `PYTHONPATH` for that run only.

### Every audio format, before and after

`-y -a -f <fmt>` on `audio.html` (JPEG cover), with and without `mutagen`. `-f` offers exactly `mp3`, `m4a`, `opus` and `wav` (`config.py:24`). `flac` and `ogg` can't be chosen, so they aren't covered here.

| `-f`  | mutagen | `main` `a30d433`                                                                                                                                                           | Swap                                                                  |
| ----- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| mp3   | no      | cover (mjpeg, `attached_pic=1`), tags `title`, `comment`, `purl`, ID3v2.4 + ID3v1                                                                                             | same cover and frames, **ID3v2.3** + ID3v1; the date keeps only the year (D3) |
| mp3   | yes     | same as without; mp3 embeds through ffmpeg (`embedthumbnail.py:90`)                                                                                                           | same as without                                                       |
| m4a   | no      | **no cover**, `Done.`, no warning                                                                                                                                             | cover, written as a real `covr` atom (read back with mutagen); `title` and `comment` (the URL) kept |
| m4a   | yes     | **no cover**: mutagen writes `covr`, then `-vn` drops it. `Done.`, no warning                                                                                                | cover (`covr`), tags kept                                             |
| opus  | no      | no cover, `audio (1).opus: saved without a cover image.` (opus needs mutagen, `embedthumbnail.py:197-200`)                                                                    | unchanged                                                             |
| opus  | yes     | cover kept, but `audio (1).opus: saved, but ffmpeg couldn't write its metadata, so a rerun won't recognize it…`: the metadata step fails on the file the cover step wrote, and no `title`, `date` or `purl` is written | cover **and** `title`, `date`, `purl`, `comment`; no warning          |
| wav   | either  | no cover, by design (`writethumbnail` is off for wav, `downloader.py:362`), tags kept                                                                                         | unchanged                                                             |

The same runs with a real WebP thumbnail (`ffmpeg -i tiny.jpg real.webp`, the kind YouTube serves): on `main`, m4a has no cover and mp3 has a png cover. With the swap, both have a png cover. Neither leaves an image file behind.

### An `.m4a` that holds a video

`-y -a -f m4a` on `<audio src="clip.m4a">` (the fixture's `clip.m4a`, which is `tiny.mp4` renamed: h264 and aac) with the absolute `og:image`. `ExtractAudio` skips it (`file is already in target format m4a`).

- `main`: aac only. The source's video is gone, and so is the cover.
- Swap: aac plus one mjpeg stream with `attached_pic=1`. The source's h264 is still dropped by the metadata step, which now runs first. So `.m4a` remains safe to read as audio (spec 30's `_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO` comment), and the file keeps its cover.

### Video mode

`-y` and `-y --video-format mkv` on `<video src="tiny.mp4">` with an `og:image`, on `main` and with the swap: identical files (h264 + aac, no attached picture), identical tags, identical step lines (`[Metadata]` only). Video mode never adds `_EmbedCover`, since `writethumbnail` is set only under `-a` (`downloader.py:357-374`). The swap only changes where `_TagSource` sits relative to a step that video mode doesn't have.

On spec 30's branch (`fix/container-choice` at `3a7fdd7`), with the swap applied to a scratch copy: `-a -f m4a` and `-a -f mp3` give the same files as the swap on `main`. `--video-format mkv` remuxes to `.mkv` and tags it, as spec 30 intends.

### Reruns

With the swap, the same `-y -a -f m4a` run twice into the same folder gives one file and one attached picture. The second run prints `has already been downloaded`, the metadata step drops the first cover, and the cover step adds a new one. The `comment` tag still holds the URL, so `_AvoidNameClash` recognises the file. mp3 run twice also gives one file and one picture.

### Chapters

yt-dlp's cover commands, run by hand after a metadata step that wrote two chapters: mp3 (`-map 0:0 -map 1:0 …`) and m4a (`-map 0 -dn -ignore_unknown -c copy -map 1 -disposition:1 attached_pic`). Both files keep the 2 chapters and the global tags, since ffmpeg copies chapters and metadata from the first input by default. mutagen edits in place, so nothing is copied. None of the tool's fixtures has chapters. The order is also yt-dlp's own CLI order.

### When the metadata step fails before the cover

The swap creates one new state: the thumbnail is still on disk when `_TagSource` raises. A scratch driver replaced `_TagSource.run` with one that raises, then ran `-y -a -f m4a` on `audio.html`:

| Raises                | Mode     | `main`                                                     | Swap                          |
| --------------------- | -------- | ---------------------------------------------------------- | ----------------------------- |
| `PostProcessingError` | single   | `ERROR: Postprocessing: …`, `Download failed`, `audio (1).m4a` left | same, no `.cover.*` left |
| `PostProcessingError` | `-p`     | —                                                          | `Download failed`, no `.cover.*` left |
| `KeyError`            | single   | `Unexpected error (KeyError: 'streams')…`, `Download failed` | same, no `.cover.*` left    |
| `KeyError`            | `-p`     | —                                                          | `ERROR: 'streams'`, `Download failed`, no `.cover.*` left |

`_Ydl.process_info` calls `drop_thumbs()` on both paths (`downloader.py:189-198`), so the thumbnail goes. The `.m4a` left behind by a failed item is row 46's, the same as on `main`. In audio mode `_TagSource` can only raise on a body ffprobe finds no media in, and `ExtractAudio` fails on such a body first, so this state is close to unreachable. yt-dlp stops an item's post-processing at the first `PostProcessingError` (`YoutubeDL.py:3663-3665`), which is why the cover step doesn't run after a failed metadata step.

### Existing tests against the swap

Run against the scratch copy (`PYTHONPATH=<scratch>` puts it ahead of the editable install's `.pth`):

- `tests/test_downloader.py -k "cover or thumb or untagged or tag or order or postprocessor"`: 11 passed.
- `tests/test_e2e.py -k "cover or thumb or untagged or opus or audio_extraction or m4a or png"`: 90 passed, 1 xfailed (row 25's opus rerun).
- `tests/test_e2e.py -k "audio and not (…the above…)"`: 130 passed, 1 xfailed (row 25).

Nothing existing breaks. `test_a_working_cover_is_still_embedded[m4a]` passes on both sides, because it skips the stream check for m4a (`test_e2e.py:2661-2663`). That gap is why the bug wasn't caught.

## Scope

### In

- In audio mode, the metadata step runs before the cover step.
- Tests: m4a keeps its cover, including on a rerun and when the source `.m4a` holds a video; the order is pinned; a failed metadata step leaves no cover file.
- Comments that describe the order.

### Out

- **A post-download check that the cover is on disk** (D2). The remaining silent no-cover paths, such as a thumbnail lost on the network, belong to row 43 (`cover-gaps`).
- **`mutagen` as a dependency** (row 48). Without it, opus still saves with no cover and says so.
- **opus reruns** (row 25): `_source_url` reads format tags, while ogg keeps them on the stream. With `mutagen`, opus now gets tags, but they are still stream tags, so row 25 is unchanged.
- **`flac`, `ogg`, `alac`:** `-f` doesn't offer them.
- **Covers in video files:** video mode writes no thumbnail, and nobody has asked for it.
- **AtomicParsley:** not installed, not measured. After the swap, the cover step is the last one that rewrites an audio file, so whichever of its three methods embeds the cover, nothing runs after it to drop it.
- **Spec 29's text** (`29-false-failure.md:175`), which records the old order ("audio is `ExtractAudio`, `EmbedThumbnail`, `Metadata`"). That spec is done and is the record of what was true then.

## Files

| Action | File                              | What                                                                                       |
| ------ | --------------------------------- | ------------------------------------------------------------------------------------------ |
| modify | `src/download_media/downloader.py` | `download()`: add `_TagSource` before `_EmbedCover`; the comment in `build_ydl_opts`        |
| modify | `tests/test_e2e.py`               | update `test_a_working_cover_is_still_embedded`; add the tests below                        |
| modify | `tests/conftest.py`               | one generic page: `audio-video-m4a`                                                         |
| modify | `tests/test_downloader.py`        | the order pin                                                                               |

No `i18n.py` change, since no message is added or changed. No README change: it doesn't mention covers.

### Lines expected to move (spec 30 merges first)

Spec 30 (`fix/container-choice`, `3a7fdd7`) adds about 30 lines near the top of `downloader.py` and about 75 before `download()`. On `main`, and where they land after the merge:

| What                                                                  | `main` `a30d433`        | After spec 30 (branch `3a7fdd7`) |
| --------------------------------------------------------------------- | ----------------------- | -------------------------------- |
| `# download() adds the cover step (_EmbedCover) and the metadata step.` | `downloader.py:365`     | `downloader.py:396`              |
| `if ydl_opts.get("writethumbnail"):` / `_EmbedCover` / `_TagSource` adds | `downloader.py:667-669` | `downloader.py:783-785`          |
| `test_a_working_cover_is_still_embedded`                              | `test_e2e.py:2655-2663` | `test_e2e.py:2684-2692`          |
| `"audio": …` page                                                     | `conftest.py:236`       | `conftest.py:241`                |

**No conflict with spec 30's design.** Its `_ContainerStep` is added only in video mode (`container = None if opts.audio_only else …`), right above the comment `# Appended after the params' postprocessors`. `_EmbedCover` is added only in audio mode. After this fix, video mode runs container step, then metadata, and audio mode runs `ExtractAudio`, then metadata, then cover. Spec 30's comment that `.m4a` isn't in `_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO` because "yt-dlp's metadata step already strips video from it" stays true, and this item pins it in audio mode (criterion 3). The only overlap is textual: both items touch lines around the same comment. Rebase on the merged `main` before starting, and the two-line move then applies cleanly. If spec 30 were dropped, the move applies to `main`'s lines 667-669 as they are.

## Design

**`download()`**: add `_TagSource` before the `writethumbnail` block, so that in audio mode the post-process order is `ExtractAudio` (from the params), `Metadata`, `EmbedThumbnail`, as in yt-dlp's CLI (`__init__.py:700-713`). `add_post_processor` appends after the params' steps (`YoutubeDL.py:946-949`), so `ExtractAudio` still runs first. On merged `main` this means:

```python
        # Appended after the params' postprocessors: audio extraction comes first.
        # Metadata before the cover, as yt-dlp's CLI does: the metadata step maps m4a with -vn,
        # which drops an attached picture.
        ydl.add_post_processor(_TagSource(ydl, untagged), when="post_process")
        if ydl_opts.get("writethumbnail"):
            ydl.add_post_processor(_EmbedCover(ydl), when="post_process")
```

The comment wording is the developer's call, as long as it says why. Spec 30's container block stays above it, untouched. `_SubtitlesSeen` and `_EmbedSubtitles` stay after it: subtitles are never embedded in audio, and in video mode nothing changes.

**`build_ydl_opts`** comment (`:365`, `:396` after spec 30): "download() adds the metadata step (_TagSource), then the cover step (_EmbedCover)."

**Nothing else changes.** `_EmbedCover`'s temp-file guard still holds. The metadata step's temp file is renamed over the audio (`os.replace`), or removed by `_TagSource` on failure, before the cover step starts, so `temp_was_there` is false there as before. On success, `EmbedThumbnailPP` still deletes the thumbnail itself. On failure, `_EmbedCover` drops it and sets `item_no_cover`. If the metadata step raises first, `process_info`'s `drop_thumbs()` removes the thumbnail (measured above).

### Decisions

**D1: how to keep the m4a cover.**

| Option | What | Trade-off |
| ------ | ---- | --------- |
| **A. Swap the two steps** (yt-dlp's CLI order) | Two lines move | Fixes m4a with all three embed methods (mutagen, AtomicParsley, ffmpeg) and opus with `mutagen`, and still drops a source's video from `.m4a`. mp3's ID3 version moves to 2.3 (D3) |
| B. Keep the order, and override `_TagSource._options` so audio-mode m4a isn't mapped with `-vn` | Re-implements yt-dlp's mapping | Under `-a -f m4a`, an `.m4a` holding h264 would keep its video (`ExtractAudio` skips it), opus with `mutagen` stays untagged, and the result relies on how ffmpeg re-muxes a `covr` atom |
| C. Embed the cover a second time after the metadata step, for m4a only | One more step | Two embeds per file and more code, for what yt-dlp's own order already does |

**Recommended: A.** It is the simplest option, it is the order yt-dlp ships, and every format was measured before and after. Its one side effect is D3.

**D2: check the disk for the cover after the chain, and warn `no_cover` if it's missing.**

| Option | Trade-off |
| ------ | --------- |
| **A. No check** | After the swap, every path that ends with no cover either already says `no_cover` or is one of row 43's listed gaps. No path is known where the embed reports success and the cover is missing |
| B. ffprobe each saved audio for an attached picture | One more ffprobe per item, and it would also start warning on row 43's network case, which is that row's scope |

**Recommended: A.** It keeps this item to one change and leaves the remaining gaps to row 43, where they are listed.

**D3: mp3 tags change from ID3v2.4 to ID3v2.3.** The mp3 cover step writes `-id3v2_version 3 -write_id3v1 1` (`embedthumbnail.py:91-93`), and it now runs last. Before the fix, the metadata step rewrote the file afterwards with ffmpeg's default, v2.4. `title`, `comment`, `purl` and the picture are the same, and the ID3v1 tag was already there. Two things do change, and this text first said "the frames are the same", which was wrong (found by QA, measured again at validation):

- **The date loses its month and day.** v2.4 has `TDRC=20261110`; v2.3 has `TYER=2026`. ffmpeg's v2.3 writer keeps only the year.
- **Text encoding.** v2.4 frames are UTF-8 (encoding byte 3). v2.3 frames are Latin-1 (byte 0) when the text is ASCII, and UTF-16 with a BOM (byte 1) when it isn't. Both decode to the same text.

This is exactly what yt-dlp's own CLI writes: `yt-dlp -x --audio-format mp3 --embed-metadata --embed-thumbnail` on the same page, with the date supplied through `--parse-metadata`, gives the same ID3v2.3 header, the same frames (`TYER=2026` included) and an ID3v1 tag (Validation).

| Option | Trade-off |
| ------ | --------- |
| **A. Accept v2.3** | It's what yt-dlp's CLI produces, and v2.3 is the version more players and file managers read. The cost is the date's month and day |
| B. Force v2.4 through `postprocessor_args` for `EmbedThumbnail` | Overrides yt-dlp's choice to keep a version nobody asked for |

**Recommended: A.** It's on the 0.4.0 review list as an on-disk change, though not a breaking one: `AGENTS.md`'s breaking list covers the tool's own paths and state, not media tags.

**D4: tests for the `mutagen` paths.** `mutagen` isn't installed in the dev venv or in CI.

| Option | Trade-off |
| ------ | --------- |
| **A. Pin the order in a unit test, and record the `mutagen` runs as measured evidence** | The order is what decides both `mutagen` results (m4a and opus). The test-engineer repeats the two `mutagen` runs once, as below |
| B. e2e tests with `pytest.importorskip("mutagen")` | Always skipped in dev and CI, so they would never actually run |
| C. Add `mutagen` to the `dev` extra | Makes the dev suite measure a machine users don't have, and runs into row 48's dependency decision |

**Recommended: A.**

## Cases

| Case | Expected |
| ---- | -------- |
| `-y -a -f m4a`, single URL with a thumbnail | `.m4a` with an attached picture (`covr`), tags kept, `Done.`, no warning |
| `-y -a` (mp3) | Unchanged cover and frames; ID3v2.3, so the date keeps only the year (D3) |
| `-y -a -f opus`, no `mutagen` | Unchanged: no cover, `no_cover` warning |
| `-y -a -f opus`, `mutagen` | Cover and tags, no `untagged` warning |
| `-y -a -f wav` | Unchanged: no thumbnail written, no cover, no warning |
| `-a -f m4a` on an `.m4a` holding video | Audio and cover only; the source's video is still dropped |
| A thumbnail that can't be embedded (junk body, gif, WebP signature) | Unchanged: `no_cover`, no image left (spec 28's tests, which pass with the swap) |
| A thumbnail that doesn't exist on disk (relative `og:image`) | Unchanged: `no_cover` |
| Wizard, audio type, m4a chosen | Same chain as direct mode: cover kept |
| Direct mode with preview and confirmation | Same |
| `-p`, `-P`, `--playlist-items`, carousel or playlist of audio items | Same chain, per item |
| `-l` | No post-processing; unaffected |
| With or without `--cookies` | Unaffected: the change is after the download |
| Rerun into the same folder | One file, one attached picture; recognised by `_source_url` (`comment`) |
| Metadata step fails (single, `-p`) | `Download failed` as today, no `.cover.*` left |
| Cover step fails | Unchanged: the audio is saved with `no_cover` |
| Video mode (`mp4`, `mkv`, `webm`, with an `og:image`) | Unchanged: no thumbnail, no cover step, same files and tags |

## Acceptance criteria

Each one names its test. **[fix]** tests fail on `main` and pass with the fix. Revert the two-line move and run them to prove it. **[pin]** tests pass on both sides.

- [x] `scripts/pre-release.sh --no-live` passes
- [x] **[fix]** `tests/test_e2e.py::test_a_working_cover_is_still_embedded` (`mp3`, `m4a`): drop the mp3-only condition and its comment. For both formats, the saved file holds its audio and exactly one stream with `disposition:attached_pic=1`, `cover_leftovers(out) == []`, and no `no_cover` warning. Add a helper next to `streams()` that counts attached pictures with ffprobe
- [x] **[fix]** `tests/test_e2e.py::test_m4a_holding_video_keeps_its_cover_not_its_video`: new page `audio-video-m4a` in `conftest.py` (`<audio src="clip.m4a">` plus `og_image`), `-y -a -f m4a`: exit 0, one `.m4a`, exactly one video stream and it is the attached picture, an audio stream, no image left
- [x] **[fix]** `tests/test_e2e.py::test_m4a_cover_survives_a_rerun`: `-y -a -f m4a` on `audio.html` twice into the same folder: one file, exactly one attached picture, `_source_url(path)` is the page URL
- [x] **[pin]** `tests/test_e2e.py::test_a_failed_metadata_step_leaves_no_cover` (single, `-p`): monkeypatch `_TagSource.run` to raise `PostProcessingError`, `-y -a -f m4a` on `audio.html`: exit 1, `t("failed", url)` in the output, `cover_leftovers(out)` holds no image
- [x] **[pin]** `tests/test_downloader.py::test_audio_mode_tags_before_the_cover`: with `_Ydl.download` patched to record `[pp.pp_key() for pp in self._pps["post_process"]]` and return 0, `download()` under `-a` (mp3, m4a, opus) gives `ExtractAudio`, `Metadata`, `EmbedThumbnail` in that order, and under `-a -f wav` gives no `EmbedThumbnail`. In video mode, no `EmbedThumbnail`, and `Metadata` keeps its place after spec 30's container step (`VideoRemuxer` or `VideoConvertor`)
- [x] **[pin]** unchanged and still passing: `test_opus_without_mutagen_is_saved_without_a_cover`, `test_mp3_whose_thumbnail_is_renamed_to_webp_leaves_no_image`, `test_video_with_an_og_image_leaves_no_image`, `test_yt_dlp_strips_video_from_m4a_so_reading_it_as_audio_loses_nothing`, `test_audio_extraction`, and spec 30's container tests
- [x] **Measured once by the test-engineer, not a test (D4):** with `mutagen` on `PYTHONPATH` only (`pip install --target <scratch>/mutagen mutagen`), `-y -a -f m4a` and `-y -a -f opus` on a loopback copy of `audio.html` both end with an attached picture. The opus file has `title` and `purl` stream tags and gets no `untagged` warning. The command and the ffprobe output go in the report
- [x] On disk, per run above: the file count matches the output, and no `.cover.*`, `.png` or `.temp.*` is left

## Live check

Not needed. The change is the order of two yt-dlp post-processors that run after the download. The loopback runs use the real yt-dlp, the real ffmpeg and yt-dlp's real generic extractor, a real JPEG and a real WebP thumbnail, with and without `mutagen`. The site only decides which thumbnail arrives, and that comes before the changed code.

## Validation

On `main` at `c8b9f89` (merge `2dc4607`, then `.ai/` commits only: `git diff --stat 2dc4607 HEAD -- src tests` is empty), yt-dlp 2026.10.26, ffmpeg n9.0.2, no `mutagen` or AtomicParsley. GitHub CI on `0c76bcd` hadn't finished: two attempts stopped at "Install ffmpeg" after 5 minutes, before any test, and a third was pending.

- **Gate:** not re-run. The tech-lead's `scripts/pre-release.sh --no-live` on the merge `2dc4607`, with spec 30 and spec 67 already on `main`: `All checks passed.`, 2297 passed, 9 deselected and 4 xfailed in `es` and in `en`. No file outside `.ai/` has changed since.
- **Criteria tests:** a `pytest -k` selection of every test the criteria name, `[fix]` and `[pin]`, ran on `main`: 26 passed. The revert proof (the `[fix]` tests failing without the two-line move) and the 9/9 mutation run are the test-engineer's. The D4 `mutagen` runs are the test-engineer's too; `mutagen` was not installed here.
- **Reproduction:** the Problem's environment, with `main`'s real console script (`.venv/bin/download-media`) under `env -i`: `PATH=/usr/bin:/bin`, a fresh `HOME` and `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` in the scratchpad, `LANG=en_US.UTF-8`, absolute `-o`, no cookies. A scratch `http.server` on `127.0.0.1:18947` served the fixture's media, built with the fixture's ffmpeg commands, and the pages `audio.html`, `audio-video-m4a.html` and a video page with an absolute `og:image`. "Before" is a `git archive` of `2dc4607^1` in the scratchpad, run through `download_media.cli:main` with the same Python and environment. Streams were read with ffprobe, ID3 frames from the file's bytes. Everything was deleted afterwards.

| Run | Before (`2dc4607^1`) | `main` |
| --- | -------------------- | ------ |
| `-y -a -f m4a`, `audio.html` | `[EmbedThumbnail]` then `[Metadata]`, `Done.`, exit 0; aac only, no cover: the bug reproduces | `[Metadata]` then `[EmbedThumbnail]`, `Done.`, no warning, exit 0; one file: aac and mjpeg `attached_pic=1`, `title`, `date=20261110`, `comment`=URL |
| same, run again into the same folder | — | `has already been downloaded`, `Done.`, exit 0; still one file, one attached picture, `comment`=URL |
| `-y -a` (mp3) | ID3v2.4 + ID3v1, cover, `TIT2`, `TDRC=20261110`, `TXXX` `comment` and `purl`, all UTF-8 | ID3v2.3 + ID3v1, cover, `TIT2`, **`TYER=2026`**, `TXXX` `comment` and `purl`, Latin-1; a rerun leaves one file and one picture |
| `-y -a -f opus` | — | `audio (1).opus: saved without a cover image.`, `Done.`, exit 0; opus, no cover, stream tags `title`, `date`, `purl`, `comment` |
| `-y -a -f wav` | — | no thumbnail written, `[Metadata]` only, `Done.`, exit 0; one `.wav`, tags kept, no cover, no warning |
| `-y -a -f m4a`, `audio-video-m4a.html` (h264 + aac source) | — | `Done.`, exit 0; one `.m4a`: aac and one mjpeg `attached_pic=1`, the h264 is gone |
| `-y`, video page with `og:image` | `[Metadata]` only; `video (1).mp4`, h264 + aac, no attached picture | identical: same step lines, streams and tags |
| `-y --video-format mkv`, same page | `[VideoRemuxer]`, `[Metadata]`; `video (1).mkv`, h264 + aac | identical |

Every run left exactly the file the output named: no `.cover.*`, `.png`, `.temp.*` or source `.m4a`.

- **D3, measured:** `yt-dlp --ignore-config --no-cookies -x --audio-format mp3 --embed-metadata --embed-thumbnail --parse-metadata "%(epoch>%Y%m%d)s:%(meta_date)s"` on the same page prints `[Metadata]` then `[EmbedThumbnail]` and writes ID3v2.3 + ID3v1 with the same frames as `main`, `TYER=2026` included. The encoding: `ffmpeg -metadata title=canción` on that mp3 with `-id3v2_version 3` writes `TIT2` as UTF-16 with a BOM (byte 1); with `-id3v2_version 4`, as UTF-8 (byte 3).
- **Cases, tests only:** wizard, confirmed direct mode, `-p`, carousels, the failed metadata step (single, `-p`, carousel), `es` and `en`, webm video. Each is one of the test-engineer's tests (`test_wizard_m4a_keeps_its_cover`, `test_confirmed_direct_m4a_keeps_its_cover`, `test_every_carousel_item_keeps_its_own_cover_in_m4a`, `test_a_failed_metadata_step_on_a_carousel_leaves_no_cover`, `test_a_kept_m4a_cover_warns_in_neither_language`, `test_video_mode_in_every_container_gets_no_cover`, and others), all passing in the gate.
- **Scope:** `0446988..2dc4607` touches exactly the four files in the Files table. The `src/` change is the two-line move and the two comments. No `i18n.py`, README, `AGENTS.md`, `pyproject.toml`, `PKGBUILD` or version change.

### What changed after approval

- **Code:** nothing beyond Design: the move and the comments, as written.
- **Spec text:** D3 said the frames were the same. QA found the date and encoding differences, and they were measured again above. D3, the mp3 rows in Findings and Cases, behaviour change 2 and the release note now say so.

## Open decisions

None blocking. D1 to D4 carry recommendations for approval under the delegation. Each one is listed below for the human's morning review.

## Behaviour changes for the 0.4.0 review

1. `-a -f m4a` now keeps the cover. The `.m4a` gains an attached picture stored as a `covr` atom, which ffprobe shows as a video stream with `attached_pic=1`. A script that treats "has a video stream" as "is a video" sees a difference. A source `.m4a` holding a real video still comes out as audio plus cover (D1).
2. `-a` mp3 tags change from ID3v2.4 to ID3v2.3, which is what yt-dlp's own CLI writes. The date loses its month and day (`TDRC=20261110` becomes `TYER=2026`), and text frames are Latin-1, or UTF-16 when they hold non-ASCII, instead of UTF-8. Title, comment, source URL and cover are the same (D3).
3. On a machine with `python-mutagen`, `-a -f opus` now writes its tags next to the cover, and the `… couldn't write its metadata …` warning no longer appears for it. Row 25 still applies to opus reruns, for another reason (ogg keeps the tags on the stream). This also changes row 48's case: with `mutagen`, opus now gets both cover and tags.
4. The console shows `[Metadata]` before `[EmbedThumbnail]`.

No flag, default, path, exit code or message changes. No new message, so the bump is set by the other 0.4.0 rows.

Release note, for `.ai/releases/0.4.0.md`: "`-a -f m4a` now keeps the cover image. It used to be embedded and then lost when the tags were written, and the run still said `Done.`. With `python-mutagen` installed, `-f opus` also keeps its title and source URL next to the cover. mp3 files now carry ID3v2.3 tags, as yt-dlp's own command writes them, so their date keeps only the year."
