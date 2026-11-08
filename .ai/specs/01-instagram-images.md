# 01 — Save carousel images

Status: done
Item: `feature/instagram-images`

> **For the human's review before the release:** the architect took every decision below under the human's delegation, and recommends an option for each. Three of them change published behaviour. Carousels now save their images in every mode, and today `-y`, `-p` and the picker skip them (Goal). The picker's Enter now means "everything downloadable", where it meant "videos only" (D2). A selection made only of images skips the type, quality and container steps (D3). None renames a flag, moves a path or changes a state format, so the architect reads this as a minor change, not a breaking one. D2 is the one to look at if you count the picker's Enter as a default.

## Problem

A public Instagram post holding 4 videos and 16 images saves 4 files out of 20. The picker marks the images `[imagen] [no soportado]`, and `-y -p` skips them with `sin video ni audio descargable (¿imagen?), se omite`. yt-dlp's metadata already carries every image's full-size link.

## Goal

A carousel saves its images next to its videos, as `NN - <title> [<id>].jpg`, in every mode that saves video. The picker offers them, the count of saved items includes them, and a rerun adds nothing.

## Findings

All of this was run on `main` at `ad5a358`, with yt-dlp 2026.10.26, curl_cffi 0.17.0 and Python 3.14, without cookies, through yt-dlp as a library. The post is `$DM_LIVE_IG_CAROUSEL`. Scratch output only, no `src/` changes.

### What an image entry carries

`extract_info(url, download=False, process=False)` returns a playlist of 20 entries. The 16 image entries have `formats: []`, no `url` and no `width`/`height`, and each has 14 `thumbnails`. The 4 videos have 7 or 8 formats and 12 thumbnails. Every entry is titled `Video by <user>`, images included.

yt-dlp builds both from `_extract_product_media` (`extractor/instagram.py:150-182`). `formats` come from `video_versions` and the DASH manifest, so an image gets none. `thumbnails` are `image_versions2.candidates`, reversed. On an image without login, the candidates carry no `width`/`height`, so the dict holds only `url`. The candidate list ends with the uncropped original: its `stp=` query parameter has no crop (`c0.504.3024.3024a`) and no size (`s1080x1080`, `p720x720`).

A carousel processed normally stops at the first image: `ERROR: [Instagram] <id>: No video formats found!`. That is `raise_no_formats` at `YoutubeDL.py:2960`. Today the tool's `match_filter` (`downloader.py:460-479`) skips those entries before that point.

### Which thumbnail yt-dlp picks

`_sort_thumbnails` (`YoutubeDL.py:2724-2730`) sorts on `(preference, width, height, id, url)`, and the last one wins (`:2895`, `_write_thumbnails` walks the list from the end). With no width, height or preference, the order comes down to the URL string. On this post, the candidate that sorts last is the uncropped original for all 16 images: `stp` is the first query parameter, and `dst-jpg_e35_tt6` sorts after every `c0.…` and `dst-jpg_e35_p…`/`_s…` value. That is luck in Instagram's URL format, not a rule (see Risks). Each saved image is its post's uncropped original (ffprobe): 15 measure **3024×4032** and one **1653×2204**, the size its crop token (`c0.275.1653.1653a`) points to. The square crops top out at 1080×1080. (Measured at validation; this line first said all 16 were 3024×4032.)

### How yt-dlp saves a thumbnail for an entry with no formats

Run on one image entry from the saved JSON, through `YoutubeDL.process_ie_result(entry, download=True)`:

| Params                                                        | Result                                                                                                                                                         |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `writethumbnail`                                              | `No video formats found!` (`:2960`), nothing saved                                                                                                              |
| `writethumbnail`, `ignore_no_formats_error`                   | `<title> [<id>].jpg` written (`process_info`, `:3392`), then `dl()` raises `No video formats found!` (`:3286`, `raise_no_formats(info, forced=True)`), so the item is an error |
| `writethumbnail`, `ignore_no_formats_error`, `skip_download`  | `<title> [<id>].jpg` written and moved by `MoveFilesAfterDownloadPP` (`:3457-3462`), return code 0, no error                                                    |

With no `ext` on the entry, yt-dlp names the file after the thumbnail link's extension: `replace_extension(..., 'jpg', None)` turns `<name>.NA` into `<name>.jpg`. The file is a real JPEG.

All three params are global. Here is why each one can't be set globally:

- `ignore_no_formats_error` is read by extractors too. `raise_no_formats(expected=True)` then turns a site's own "Sign in", "private", "geo" or "upcoming" into a warning, which `no_warnings` hides, and only `No video formats found!` is left. That is why `_ProbeYdl` avoids it (`probe.py:287-299`, spec 18).
- `skip_download` would skip the videos.
- `writethumbnail` would leave a `.jpg` next to every video.

### Prototype: the three params scoped to image entries

A scratch subclass of the tool's own `_Ydl` sets `ignore_no_formats_error`, `skip_download`, `writethumbnail` and `overwrites: False` only around `process_video_result` for an entry with a `playlist_index`, no formats, no `url` and thumbnails, then restores them. It used the real `build_ydl_opts` of `-p` mode and `_AvoidNameClash`, on items 1 to 3 (image, video, image):

```
01 - Video by <user> [<id1>].jpg     3024x4032
02 - Video by <user> [<id2>].mp4
03 - Video by <user> [<id3>].jpg     3024x4032
errors: []   return code: 0   post hooks: only the .mp4
```

- No `.jpg` was left next to the video: its params were the normal ones.
- A rerun printed `[info] Video thumbnail is already present` for both images, and `has already been downloaded` for the video. Still 3 files, no error.
- yt-dlp's `No video formats found!` and `Requested format is not available` warnings appear only without `no_warnings`, and the tool always sets it (`downloader.py:175`).
- yt-dlp doesn't run post hooks in the `skip_download` branch, so the tool's `keep` hook (`downloader.py:502`) never sees an image. Images need their own count.

### The wrapper rule

Settled, and not blocking. The image is fetched, named, checked for "already present" and moved by yt-dlp's own `_write_thumbnails` and `MoveFilesAfterDownloadPP`, from the candidate yt-dlp's own sort picks. It's what `yt-dlp --ignore-no-formats-error --skip-download --write-thumbnail` does to those entries. The tool adds no HTTP request and doesn't pick a URL. It only scopes three yt-dlp params to the entries that are images. That's the same kind of override as `_ProbeYdl.process_video_result`, which already ships.

### Lone image posts

Without login, the extractor itself raises `There is no video in this post` (`instagram.py:530`) unless `ignore_no_formats_error` is set at extraction time. That is global, and ruled out above. With login the API path returns the entry (`:439-445`), so the same mechanism could save it. A lone image would then save with cookies and fail without them. That's D4, kept out of this item.

## Scope

### In

- Carousel and list items that are images (no formats, no `url`, at least one thumbnail) are saved through yt-dlp's thumbnail writer, in single mode (`-y`, `-P`, the direct preview), `-p` and `--playlist-items`, with and without cookies.
- The probe marks those items as supported images, and the picker offers them.
- The picker's Enter selects every downloadable item (D2).
- A selection made only of images skips type, quality, container and audio format (D3).
- In `-a` and `-l`, images are skipped and named as images.
- An image that can't be fetched counts as a failed item.
- Message changes in both blocks, the `AGENTS.md` paragraph on multi-entry URLs, the README line on carousels, and the live tests.

### Out

- **Lone image posts** (D4): `image_only_skipped` and its refusal stay as they are. A new roadmap row covers them.
- **A different title for images:** yt-dlp names them `Video by <user>`. Rewriting it would re-implement extractor metadata (D5).
- **Image conversion** (D6): the file keeps the bytes and extension yt-dlp writes.
- **A different folder for images** (D1).
- **Which thumbnail is the full image:** that's yt-dlp's sort. If Instagram's URLs change and a crop wins, it's an upstream issue (Risks).
- **Items yt-dlp sees as image *formats*** (`vcodec`/`acodec` `none`, storyboards, row 37) and `{url, formats: []}` entries with no thumbnail: unchanged, still unsupported.
- **The hardcoded `[no soportado]`** that items which still can't be saved keep printing: row 02. **`unsupported_count`** and the rest of the picker wording: row 31.
- **History format:** one row per URL, as today (D8).

## Files

| Action | File                              | What                                                                                  |
| ------ | --------------------------------- | ------------------------------------------------------------------------------------- |
| modify | `src/download_media/downloader.py` | image predicate, `_Ydl` override, image count, failure, `match_filter`, `-l`          |
| modify | `src/download_media/probe.py`      | `_classify_entry`: images with a thumbnail are supported                              |
| modify | `src/download_media/wizard.py`     | picker tags, Enter, drop `ig_image_warning`, images-only selection, banner count      |
| modify | `src/download_media/i18n.py`       | messages, both blocks                                                                 |
| modify | `AGENTS.md`                        | the "Multi-entry URLs" paragraph                                                      |
| modify | `README.md`                        | the Carousels line (line 16)                                                          |
| modify | `tests/conftest.py`                | new `fake_site` pages (see Acceptance criteria)                                       |
| modify | `tests/test_e2e.py`                | new tests; update the ig/mixed/no-video carousel tests whose expected files change    |
| modify | `tests/test_downloader.py`         | `match_filter` unit tests for images, with and without audio mode                     |
| modify | `tests/test_probe.py`              | `supported` for image entries with and without thumbnails                             |
| modify | `tests/test_wizard_flow.py`        | Enter default, images-only selection skipping steps                                   |
| modify | `tests/live/test_live.py`          | the carousel tests also check that images were saved                                  |

`cli.py`, `history.py`, `equivalent.py` and `config.py` should need no change. If one does, say so in the report.

## Design

### One predicate

`downloader.py` gets `is_saveable_image(info) -> bool`, used by the `match_filter`, the `_Ydl` override and the probe (`probe.py` already imports from `downloader.py`, not the other way). It is true when:

- `info.get("_type", "video") == "video"`;
- yt-dlp would find no formats: `formats` is an empty list, or it's absent and there's no `url` (the rule in `probe._formats`, i.e. yt-dlp's `_get_formats`);
- the entry has `thumbnails` or `thumbnail`.

Only list items take the image path: the override and the `match_filter` also require `playlist_index`. A lone URL behaves as today (D4).

### Saving: `_Ydl.process_video_result`

The override runs only for `is_saveable_image(info)`, `playlist_index` set and images enabled (not `-a`). It sets these params on `self.params` for the duration of `super().process_video_result(...)`, then restores the previous values in a `finally`, popping the keys that weren't there:

| Param                     | Value   | Why                                                                                            |
| ------------------------- | ------- | ---------------------------------------------------------------------------------------------- |
| `ignore_no_formats_error` | `True`  | Gets past `:2960` and `:3091`. Extraction is already over, so no extractor reads it here       |
| `skip_download`           | `True`  | No `dl()`, so no forced raise at `:3286`. The thumbnail is moved by `MoveFilesAfterDownloadPP` |
| `writethumbnail`          | `True`  | yt-dlp's `_write_thumbnails` saves the best candidate                                          |
| `overwrites`              | `False` | A rerun reuses the file (`existing_file`, "already present") instead of deleting and refetching it. A refetch that failed would lose the earlier copy |

While the override runs, `_Ydl._write_thumbnails` must call yt-dlp's directly, without the `.cover` renaming (`_cover_name`) and without catching errors as `item_no_cover`.

- **Count:** `_Ydl` records each image file it wrote or found already present (the final path, the second element of what `_write_thumbnails` returns). `download()` adds those to `saved`, including in the `returncode != 0 and not saved` check, because post hooks don't run for them.
- **Failure:** if `_write_thumbnails` returns nothing for an image (every candidate failed), or raises a `RequestError`/`_UnsafeExtensionError`, the tool calls `report_error(t("image_not_saved", id))`. Under `-p` that's one failed item (`partial_result`). In single mode it stops the run, as a broken video does today (`broken-item`, row 08).
- **Never delete what was there:** an image found already present never goes in `item_thumbs`, so `drop_thumbs` can't remove it.
- **No post-processing:** the `skip_download` branch runs no `post_process` step, so `_TagSource`, `_EmbedSubtitles` and `_SubtitlesSeen` never run on an image. Leave it that way: ffmpeg has no business tagging a JPEG.

### Naming and folder

Names come from the existing templates (`build_ydl_opts`) and `_AvoidNameClash`, which already adds ` [<id>]` to every item with a `playlist_index`:

| Mode                          | Image file                                              |
| ----------------------------- | ------------------------------------------------------- |
| `-p`, `--playlist-items`, wizard | `NN - <title> [<id>].<ext>`                          |
| single (`-y`, `-P`, preview)  | `<title> [<id>].<ext>` (carousels download whole in single mode) |

`<ext>` is what yt-dlp takes from the link: `jpg` on Instagram today (D6). The folder is the run's folder (`-o`, or the default `xdg_dir("VIDEOS", "Videos")`), the same as the videos of the same post (D1). There is no new template: yt-dlp's default thumbnail name is the media name with the image's extension, as the prototype shows.

### `match_filter` (`_skip_items_without_media`)

It gets a `keep_images: bool` argument:

- **Video runs** (`download()` without `-a`): an item with `is_saveable_image` passes. An item with nothing at all (no formats, no `url`, no thumbnail, like the `carousel` page's `_image`) is skipped as today, with the reworded `item_no_media`.
- **`-a` and `-l`** (`keep_images=False`): an image is skipped with the new `item_image_skipped`, and counts in `skipped`. The rest is as today.

yt-dlp calls the filter twice for a list item (`:2157` with `incomplete=True`, then `:3037` inside `process_video_result`). Both calls must agree, and an item must land in `skipped` only once.

### Probe

`_classify_entry`: `kind` stays `SINGLE_IMAGE` for an entry with no formats, and `supported` becomes `is_saveable_image(entry)`. So an image with a thumbnail is supported, and one without stays unsupported. `None` entries, image formats and storyboards are unchanged.

### Wizard

- **Picker lines:** a supported image prints `[imagen]`/`[image]` in a colour of its own (not `C.DIM`), with no `[no soportado]`. Unsupported items keep today's line.
- **`ig_image_warning`:** removed, both the call (`wizard.py:279-283`) and the key. It's no longer true.
- **Enter with no preset** (D2): every supported item, which is today's fallback (`wizard.py:316`), now the rule. The prompt becomes `ask_selection_default`. A preset (`--playlist-items`) keeps today's behaviour.
- **Images-only selection** (D3): when every selected item is a supported image, `type`, `quality`, `container` and `aformat` don't apply, and `audio_only` becomes `False`. `_walk_plan` knows the items. `_step_applies`, or a check next to it, reads the selection from `opts.playlist_items` (`""` is all). `b` from the folder step then goes back to the picker.
- **Banner:** `items_count` says "downloadable", not "downloadable videos".

### Messages (both blocks)

| Key                              | Change                                 | es                                                                                   | en                                                                                 |
| -------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| `item_no_media`                  | reworded: images aren't the guess now  | `{}: nada que descargar, se omite`                                                   | `{}: nothing to download, skipping`                                                |
| `item_image_skipped`             | new (`-a`, `-l`)                       | `{}: es una imagen, se omite`                                                        | `{}: an image, skipping`                                                           |
| `image_not_saved`                | new                                    | `{}: no se pudo descargar la imagen`                                                 | `{}: couldn't download the image`                                                  |
| `items_count`                    | reworded                               | `{} items: {} descargables, {} no descargables`                                      | `{} items: {} downloadable, {} not downloadable`                                   |
| `ask_selection_default_videos`   | renamed `ask_selection_default`, reworded | `Selecciona items (Enter = todo lo descargable, 'all' = todo intentable, c = cancelar): ` | `Pick items (Enter = everything downloadable, 'all' = try everything, c = cancel): ` |
| `ig_image_warning`               | removed                                | —                                                                                    | —                                                                                  |
| `image_only_skipped`             | unchanged (lone images, D4)            |                                                                                      |                                                                                    |
| `partial_result`, `no_supported_items`, `selection_warning_unsupported` | unchanged |                                                                      |                                                                                    |

The wording is a proposal. The developer may adjust it, as long as both blocks stay in step and the tests use `t()`.

### Docs

- **`AGENTS.md`:** "items with no formats (Instagram images) are skipped by a `match_filter`" becomes: images (no formats, a thumbnail) are saved through yt-dlp's thumbnail writer with three params scoped to them. Items with nothing to fetch are still skipped by the `match_filter`, so a single-mode failure still raises.
- **`README.md:16`:** carousels save their images and videos, and report what was skipped.

## Cases

"ig-shaped" means a carousel of `_instagram_image` and video entries (`fake_site` page `ig-carousel`: i1, v1, v2, i2).

| Case                                    | Expected                                                                                                                                     |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| wizard, Enter at every step             | All 4 saved: `01 - Clip [i1].jpg`, `02 - Clip [v1].mp4`, `03 - Clip [v2].mp4`, `04 - Clip [i2].jpg`. No `[no soportado]` on images. `Done.` |
| wizard, `all`                           | Same as Enter on this page. On `mixed-carousel`, 4 of 4 saved, where it was 3 of 4                                                          |
| wizard, numbers naming only images      | No type, quality or container step. The images are saved in the folder shown                                                                |
| wizard, `-i -a`, images-only selection  | Same: saved as images. The printed command has no `-a`                                                                                       |
| wizard, `-i -a`, mixed selection        | Audio of the videos. Images skipped with `item_image_skipped`, in `partial_result`                                                          |
| `-y` (single mode), `-P`                | `Clip [i1].jpg`, `Clip [i2].jpg`, `Clip [v1].mp4`, `Clip [v2].mp4`                                                                           |
| direct with preview, no `-y`            | Same files after the confirmation                                                                                                            |
| `-p`                                    | The 4 numbered names. No `partial_result` line                                                                                               |
| `--playlist-items 1,3`                  | `01 - Clip [i1].jpg`, `03 - Clip [v2].mp4`                                                                                                   |
| `-a`, `-a -p`                           | Only the videos' audio. `item_image_skipped` for i1 and i2. `partial_result(2, 4, 2, 0)`. No `.jpg` left in the folder                       |
| `-l`                                    | Formats of v1 and v2. `item_image_skipped` for the images. No files                                                                          |
| with / without cookies                  | Identical (fake cookies as in the existing `--cookies firefox` tests)                                                                        |
| rerun, same mode                        | No new file. Image files not rewritten (same mtime). `Done.`, exit 0                                                                         |
| image whose every thumbnail fails, `-p` | Other items saved. `image_not_saved` for it. `partial_result(saved, total, 0, 1)`, exit 0                                                   |
| same, single mode                       | Same outcome as `broken-item` in single mode today: exit 1, no `Done.`                                                                       |
| images without thumbnails (`carousel`)  | As today: skipped, with the reworded `item_no_media`, `partial_result(2, 4, 2, 0)`                                                           |
| lone image (`ig-image`)                 | As today: `image_only_skipped`, no files                                                                                                     |
| history                                 | One row per URL that saved something, kind `video` (D8)                                                                                      |
| printed command                         | No new flag. The replay saves the same file names                                                                                            |
| `-s`                                    | Images saved, no error from the subtitle steps                                                                                               |

## Acceptance criteria

New `fake_site` pages in `tests/conftest.py`:

- `ig-broken-image`: `_instagram_image("i1")` whose only thumbnail is a 404, then `_video("v1")`.
- `ig-two-thumbs`: one `_instagram_image("i1")` with two candidates and no `width`/`height`. The first points to a different, smaller JPEG; the last to `tiny.jpg`. It pins that the tool saves yt-dlp's last-sorted candidate and doesn't pick one itself.

Criteria:

- [x] `scripts/pre-release.sh --no-live` passes.
- [x] e2e: `-y -p` on `ig-carousel` saves exactly the 4 numbered names above, and each `.jpg` is byte-identical to the fixture's `tiny.jpg`. Exit 0, `t("done")`, no `partial_result` line, one history row.
- [x] e2e: `-y` on `ig-carousel` saves exactly `Clip [i1].jpg`, `Clip [i2].jpg`, `Clip [v1].mp4`, `Clip [v2].mp4`. No other `.jpg` (no thumbnail next to a video).
- [x] e2e: the same `-y -p` run twice adds no file and leaves the images' mtimes unchanged. Exit 0 both times.
- [x] e2e: `-y -p --playlist-items 1,3` on `ig-carousel` saves `01 - Clip [i1].jpg`, `03 - Clip [v2].mp4`.
- [x] e2e: `-y -a -p` on `ig-carousel` saves only the 2 audio files, prints `t("item_image_skipped", "i1")` and `t("partial_result", 2, 4, 2, 0)`, and leaves no `.jpg`.
- [x] e2e: `-y -l` on `ig-carousel` lists v1 and v2, prints `t("item_image_skipped", "i1")`, saves nothing.
- [x] e2e: `-y -p` on `ig-broken-image` saves `02 - Clip [v1].mp4`, prints `t("image_not_saved", "i1")` and `t("partial_result", 1, 2, 0, 1)`, exit 0. `-y` on it matches `test_single_mode_stops_at_a_broken_item`: exit 1, no `t("done")`.
- [x] e2e: `-y -p` on `ig-two-thumbs` saves one `.jpg` byte-identical to `tiny.jpg`.
- [x] e2e: `-y` and `-y -p` on `carousel` (images without thumbnails) keep today's files, with `t("item_no_media", "i1")` and `t("partial_result", 2, 4, 2, 0)`.
- [x] e2e: wizard on `ig-carousel` with `PICKER_WALK` saves the 4 numbered files. The output has two `[image]` tags and no `[no soportado]`. The printed command, replayed into another folder, saves the same names.
- [x] e2e: wizard on `ig-carousel` answering `1,4` at the picker saves only the two images, without reading answers for type, quality or container. With `-i -a`, the same, and the printed command has no `-a`.
- [x] e2e: the existing tests on `mixed-carousel` and `no-video-carousel` are updated to the new file lists (i1 saved as `01 - Clip [i1].jpg`), with and without `--cookies firefox`, and their replays still match.
- [x] e2e: the lone-image tests (`ig-image`, `image-format`, `storyboard-only`, `thumbnail-image`) still pass unchanged.
- [x] unit (`test_downloader.py`): the `match_filter` passes an image item with thumbnails when `keep_images=True`, and skips it with `item_image_skipped` when `False`. An item with nothing at all is skipped with `item_no_media` in both. Lone URLs and the playlist's own metadata pass.
- [x] unit (`test_probe.py`): on `ig-carousel` every item is `supported`, and on `carousel` the images aren't.
- [x] unit (`test_wizard_flow.py`): Enter with no preset selects every supported item, images included. A selection of only images walks no type, quality, container or aformat step.
- [x] `test_i18n.py` passes: `item_image_skipped`, `image_not_saved` and `ask_selection_default` are in both blocks, and `ig_image_warning` and `ask_selection_default_videos` are in neither. `grep -rn "ig_image_warning\|ask_selection_default_videos" src tests` prints nothing.
- [x] `tests/live/test_live.py`: the carousel tests (with and without cookies, `-y` and `-p`) also assert that at least one `.jpg` was saved, and that no `N of M items downloaded` line appears.

## Live check

Run in the validation stage, one command at a time (Instagram rate-limits by IP), with `.ai/live.env` loaded:

```bash
set -a; . .ai/live.env; set +a
.venv/bin/download-media -y -p -o tmp/ig-images "$DM_LIVE_IG_CAROUSEL"
ls tmp/ig-images | wc -l                      # 20
ls tmp/ig-images/*.jpg | wc -l                # 16
ls tmp/ig-images/*.mp4 | wc -l                # 4
for f in tmp/ig-images/*.jpg; do ffprobe -v error -show_entries stream=width,height -of csv=p=0 "$f"; done
.venv/bin/download-media -y -p -o tmp/ig-images "$DM_LIVE_IG_CAROUSEL"   # rerun
ls tmp/ig-images | wc -l                      # still 20
.venv/bin/download-media -y -o tmp/ig-images-single "$DM_LIVE_IG_CAROUSEL"
ls tmp/ig-images-single | wc -l               # 20
```

What must hold:

1. The first run ends in `Done.`, exit 0, with no `items downloaded` line.
2. No image is a square 1080×1080 or smaller: the planning run measured 3024×4032. Record the sizes in the report.
3. The rerun prints `already present` for the images and `has already been downloaded` for the videos.
4. Then the full gate: `scripts/pre-release.sh` with `DM_LIVE_IG_CAROUSEL` and `DM_LIVE_BROWSER`. That runs the cookie variants through the tool. Never read cookies any other way.

## Risks

- **The full-size pick rests on URL order.** If Instagram moves `stp` or renames its values, yt-dlp may save a square crop, and the tool will report it as saved. The live check is the guard. The real fix belongs upstream: the extractor should keep the candidates' `width`/`height` or set a `preference`. Worth an upstream issue if it ever happens.
- **A 404 on the original falls back to a smaller candidate,** silently. That's `_write_thumbnails` walking the list.
- **The override flips params on a live `YoutubeDL`.** It's scoped to one entry, restored in `finally`, and no extractor runs inside `process_video_result` for an already-extracted entry. If a future yt-dlp reads these params elsewhere in that call, the e2e tests on `fake_site` are what catch it.

## Open decisions

All recommended options are what this spec describes. The ones marked **(published behaviour)** need the human's review before the release.

- **D1, folder.** **A (recommended):** the run's folder, `~/Videos` by default, next to the post's videos. The `NN -` order stays in one place, the tool already saves direct `.jpg` items there (`mixed-carousel`'s `02 - Clip [p1].jpg`), and `-o` covers anyone who wants elsewhere. **B:** `xdg_dir("PICTURES", "Pictures")` when there's no `-o`. That splits one post across two folders, and needs a separate thumbnail template that must not catch `-a` covers.
- **D2, picker Enter (published behaviour).** **A (recommended):** every downloadable item, images included. It matches what `-y` and `-p` now save, and a post is usually wanted whole. **B:** videos only, as today, with images through `all` or numbers.
- **D3, images-only selection (published behaviour).** **A (recommended):** skip type, quality, container and audio format. Asking "1080p? mp4?" for photos is noise, and audio would save nothing. **B:** ask as today.
- **D4, lone image posts.** **A (recommended):** out of this item, with a new roadmap row. Without login yt-dlp's extractor refuses them before any metadata, and the only yt-dlp way past that is the global `ignore_no_formats_error` that spec 18 ruled out. In scope they would save with cookies and fail without. **B:** in scope, cookies only.
- **D5, title.** **A (recommended):** yt-dlp's `Video by <user>`, even for images. **B:** a title of our own, which would re-implement extractor metadata.
- **D6, extension.** **A (recommended):** yt-dlp's, from the link (`jpg` on Instagram), no conversion. **B:** convert through yt-dlp's `FFmpegThumbnailsConvertorPP` to a fixed format. That only matters if a site serves webp or heic.
- **D7, `-a`.** **A (recommended):** skip images, named with `item_image_skipped`. **B:** save them in the Music folder.
- **D8, history.** **A (recommended):** unchanged, one row per URL, kind `video`. **B:** a new kind `image` when only images were saved, which changes what the log holds.
