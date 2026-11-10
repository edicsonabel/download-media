# 61 — Save lone image posts

Status: draft
Item: `feature/ig-lone-images`

> **For the human's review before the release:** the architect took every decision below under the overnight delegation and recommends an option for each. Four change published behaviour. A lone result with no formats and a thumbnail is now saved as an image, on any site, with a notice saying so (D1, the one to look at). Lone images are always named with their id (D2). Instagram's logged-out sentence `There is no video in this post` now counts as a case browser cookies can fix (D6), which reverses one row of spec 18's table. The direct preview reads pages through the tool's own yt-dlp subclass, which also gives carousel previews their title (D5, row 63). Nothing renames a flag, changes a default or moves a path, so this reads as minor, and 0.4.0 already is one. The logged-in path can only be checked with the human's own cookies: that's the live check, an open item for the morning.

## Problem

A lone Instagram image post can't be saved: logged in, every direct mode fails with yt-dlp's `No video formats found!; please report this issue`, retries without cookies and ends in `Download failed`, and the wizard refuses it as `a standalone image`. Logged out, the wizard walks six questions to the same failure, and nothing tells the user that cookies would help.

## Goal

With browser cookies, a lone image post is saved in every mode that saves video, as `<title> [<id>].<ext>`, through spec 01's thumbnail writer. Without them, the user is told it may be an image that the site only shows when logged in, and that `--cookies` is the way.

## Findings

Run on `main` at `a7f3c5f` (`src/` unchanged at `74ec2c1`), yt-dlp 2026.10.26, curl_cffi 0.17.0, Python 3.14.9, `LANG=C`. Fresh `HOME` and XDG folders, no browser cookies read, no request to instagram.com. Scratch files only, deleted afterwards.

### What yt-dlp's Instagram extractor returns for a lone image

- **Logged in** (`instagram.py:439-445`): `_extract_product(<api>/media/<pk>/info/ ['items'][0])`. A post with no `carousel_media` returns `{**info_dict, **_extract_product_media(product_info)}` (`:224-228`): `formats: []` (no `video_versions`, no DASH manifest, `:153-168`), `thumbnails` from `image_versions2.candidates`, keeping `width`/`height` when the answer has them (`:174-181`; the canned answer below had them, the real one hasn't been seen here, so the live check records the size), title `Video by <user>` (`:190`), no `_type`, no `playlist_index`. Nothing raises: the `There is no video in this post` check sits only on the logged-out path.
- **Logged out** (`:457-530`): the same `_extract_product` on the GraphQL answer, then `if not is_playlist and not info_dict.get('formats'): self.raise_no_formats('There is no video in this post', expected=True)` (`:529-530`). It raises unless `ignore_no_formats_error` is set while extracting, which spec 01 and spec 18 ruled out: the same switch turns age gates, geo blocks and upcoming streams into warnings with metadata (`common.py:1245-1271`).
- With login, then, the entry reaches `YoutubeDL.process_video_result` with no formats, and `raise_no_formats` (`YoutubeDL.py:1188-1196`, called at `:2960`) fails it with `No video formats found!` and yt-dlp's "please report this issue" tail. The tool's image path (`_Ydl.process_video_result`, `downloader.py:199-215`) only takes entries with a `playlist_index`, so a lone one never gets there.
- The human's 0.3.0 live run of spec 18 step 3 already showed the logged-in shape on a real post: anonymous `There is no video in this post`, no files; logged in, `El contenido es una imagen y no es soportado por yt-dlp`, exit 0 (`state.json`, release 0.3.0 history). That refusal only fires on a lone result with no formats and a thumbnail (`probe.py:277-283`).

### Reproduction 1: yt-dlp's real `InstagramIE` with canned answers

A scratch script ran `cli.main` with yt-dlp's real `InstagramIE`. Its network methods were replaced: `_download_json` returned a canned `media/<pk>/info/` answer (logged in) or the `get_ruling_for_content` and GraphQL answers (logged out); `_download_webpage(_handle)` raised; `_real_initialize` set a token; `_is_logged_in` was true when `--cookies` was passed; `_can_impersonate` was `True`. The `_Ydl` subclass popped `cookiesfrombrowser`, as `fake_site` does, so no browser was read. The two candidates (96×120 and 64×64 JPEGs) came from a loopback server, and every proxy variable pointed at a dead port, so nothing else could leave the machine. The URL was `https://www.instagram.com/p/C0abcDEFghi/`.

| Command                         | Today                                                                                                                                                                                                                                                                 |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-y --cookies firefox <url>`    | `ERROR: [Instagram] C0abcDEFghi: No video formats found!; please report this issue on …`, `Failed with firefox cookies. Retrying without cookies...`, `ERROR: … There is no video in this post`, `Download failed`, exit 1, no files. The printed command keeps `--cookies firefox -P` |
| `-y <url>`                      | `ERROR: … There is no video in this post`, `Download failed`, exit 1, no hint                                                                                                                                                                                          |
| `-i --cookies firefox <url>`, Enter | `This content is a standalone image, not supported by yt-dlp. Cancelling.`, `Cancelled.`, exit 0 (1 once cancel-exits merges)                                                                                                                                       |
| `-i <url>`, Enter at every prompt | `Could not inspect URL: There is no video in this post`, manual flow (type, quality, container, folder, cookies), then `Playlist URL detected` (an `/p/` URL looks like one), the preview, and `There is no video in this post`. Exit 1, no files                  |

The same script with the design below applied as scratch monkeypatches (no `src/` edit): `-y --cookies firefox`, `-y -p --cookies firefox`, `-y --video-format mkv --cookies firefox` and the preview with Enter each saved `Video by someone [C0abcDEFghi].jpg`, measured 96×120 by ffprobe (the larger candidate: with `width`/`height` present, `_sort_thumbnails` at `YoutubeDL.py:2724-2730` sorts by size, not by URL as in spec 01's logged-out carousels), one history row, `Done.`, exit 0, and only the logged-in API was requested. `-y -a` printed `C0abcDEFghi: an image, skipping` and `No items are downloadable. Cancelling.`, exit 1. `-y -l` printed the same skip line, exit 0.

### Reproduction 2: every mode on `fake_site`

A scratch test, run with `pytest -p tests.conftest`, added a page `ig-lone-image` that mirrors the two paths: without cookies it calls `self.raise_no_formats("There is no video in this post", expected=True)`, with them it returns `_instagram_image("i1")`. On `main`:

| Mode, with `--cookies firefox` unless said                      | Today                                                                                                                                              |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-y`, `-y -p`, `-y -P`, `-y --playlist-items 1`, `-y -a`        | `No video formats found!; please report…`, `Failed with firefox cookies. Retrying…`, `There is no video in this post`, `Download failed`, exit 1  |
| `-y -l`                                                         | `No video formats found!; please report…`, `Download failed`, exit 1                                                                              |
| preview (`--cookies firefox`, Enter)                            | An empty `Preview` block, yt-dlp's `No video formats found!` line from the preview's own extraction, then the same failure as `-y`, exit 1          |
| `-y` without cookies, `-y -l` without cookies                   | `There is no video in this post`, `Download failed`, exit 1, no hint                                                                               |
| wizard `-i --cookies firefox`                                   | `image_only_skipped`, `Cancelled.`, exit 0                                                                                                         |
| wizard `-i` without cookies                                     | `probe_failed` with the sentence, manual flow, preview, `Download failed`, exit 1. 7 prompts                                                       |
| wizard `-i --cookies firefox <video> <ig-lone-image>`           | The video saved, the image refused, `Skipped, nothing to download: …/ig-lone-image`, exit 0                                                        |

With the design applied as scratch monkeypatches, on the same pages:

- Saved as `Clip [i1].jpg`, exit 0: `-y`, `-y -p`, `-y --playlist-items 2`, `-y -s` (plus spec 21's `subs_none`, as for any item without subtitles), the preview, `-y` on `thumbnail-image`. A rerun printed `Video thumbnail is already present`, kept the mtime, `Done.`, exit 0, in `-y` and then in `-y -p`.
- `-y ig-image ig-lone-image-b` (two posts, one title) saved `Clip [i1].jpg` and `Clip [i9].jpg`. `-y video ig-image` saved `Clip.mp4` and `Clip [i1].jpg`.
- Unchanged, exit 1, no files: `image` (no thumbnail: `No video formats found!`), `image-format` (`Requested format is not available`), `upcoming`, `drm`, `no-url-format`, and `ig-lone-image` without cookies.
- A lone image whose only thumbnail is a 404: `-y` and `-y -p` gave `i1: couldn't download the image`, `Download failed`, exit 1; with `--cookies firefox`, the cookieless retry and the same failure.
- The preview through `_Ydl` with images enabled (D5) printed `Title: Clip` on the lone image and `Title: Post` on `ig-carousel`, with no yt-dlp `ERROR:` line. Declining wrote nothing on either.

## Scope

### In

- **Lone results** that `is_saveable_image` accepts (no formats, no `url`, a thumbnail; `downloader.py:104-111`) are saved through spec 01's scoped params, in single mode (`-y`, `-P`, the preview), `-p`, `--playlist-items` and the wizard, with or without cookies: whatever yt-dlp returns. On Instagram that means with login (D1).
- Named `<title> [<id>].<ext>` in every mode (D2), with a notice after the save (D1).
- `-a` skips a lone image, named as an image, and fails the URL (D3). `-l` names it as an image and lists nothing (D4).
- The direct preview reads the page through `_Ydl` with images enabled (D5).
- Instagram's logged-out sentence becomes a case cookies can fix: the wizard asks for cookies with its own message, and direct mode prints a hint (D6).
- The wizard walks a saveable lone image like spec 01's images-only selection: no type, quality, container or audio format, and `-i -a` saves it as an image (D7).
- Three messages in both blocks; the `AGENTS.md` multi-entry paragraph; the README carousel line; `DM_LIVE_IG_IMAGE` in `.ai/live.env.example`.

### Out

- **The manual flow after cookies are declined or don't help** (row 38, `probe-fail-fast`). This item settles row 38's open question: a lone image without login **is** a failure cookies can fix (`needs_cookies`), so the wizard asks for cookies right after the probe. If the user declines, or the cookies don't log in, the manual flow still follows, and it can never save the post: it downloads with the same cookies and gets the same sentence. That leftover walk is row 38's to cut.
- **Saving without login.** yt-dlp only reaches the image's metadata past `ignore_no_formats_error`, which can't be scoped to one extractor without per-site code and hides other sites' login and geo errors (spec 18, D4 of spec 01).
- **Carousel items that are really broken videos.** Spec 01's list items carry the same risk D1 weighs for lone results (a video whose formats all failed, saved as its thumbnail), with no notice. Noted for the planner, not changed here.
- **The rest of the preview** (item counts, sizes for carousels): row 63 (D5 only stops the preview failing on images).
- **Image conversion, a Pictures folder, history kind:** as spec 01 D1, D6 and D8. A lone image goes in the run's folder, keeps yt-dlp's extension, and logs one row of kind `video`.
- **`image_only_skipped`** stays for lone results whose only formats are images (`image-format`); its wording is cancel-exits'. `no_supported_items` and `item_image_skipped` are reused as they are.
- **The hardcoded English `→ try again with --cookies` hint** (`cli.py:461`): row 02. This item adds its own hint through `t()` for the image case only.

## Files

Line numbers on `main` at `74ec2c1`.

| Action | File                               | What, and where                                                                                                                                                                                         |
| ------ | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| modify | `src/download_media/downloader.py` | `DownloadResult` (`:68-91`): one field. `_Ydl`: the `save_images` comment (`:159-162`), `process_video_result` (`:199-215`), a flag for a lone image. `_AvoidNameClash.run` (`:484-501`). `_skip_items_without_media` (`:737-764`). `download()`'s result (`:821-839`) |
| modify | `src/download_media/probe.py`      | `Probe` (`:49-70`): one field. A phrase tuple and `is_image_post_error` next to the auth hints (`:125-173`); `is_auth_error` (`:166-173`). `_read_result`'s single branch (`:330-341`)                     |
| modify | `src/download_media/wizard.py`     | `_build_post_probe_plan` (`:386-400`), the cookies retry message (`:591-592`), the refusal and the type default after the probe (`:610-623`)                                                             |
| modify | `src/download_media/cli.py`        | `_show_preview` (`:233-264`), the warning block (`:388-399`): one loop, `_hint_for_download_error` (`:455-463`)                                                                                           |
| modify | `src/download_media/i18n.py`       | three keys after `auth_still_needed`, both blocks (`:103`, `:205`)                                                                                                                                     |
| modify | `AGENTS.md`                        | the "Multi-entry URLs" paragraph (`:121-128`)                                                                                                                                                          |
| modify | `README.md`                        | the Carousels line (`:17`)                                                                                                                                                                              |
| modify | `.ai/live.env.example`             | a `DM_LIVE_IG_IMAGE=` line after `DM_LIVE_IG_CAROUSEL` (`:2`), with a placeholder post like the others; row 55 turns it into a live test                                                               |
| modify | `tests/conftest.py`                | `FakeSiteIE._codec_shape` (`:556-632`): pages `ig-lone-image`, `ig-lone-image-b`, `ig-lone-broken`                                                                                                       |
| modify | `tests/test_e2e.py`                | the criteria below; `ig-image` and `thumbnail-image` leave the two refusal tests (`:3546`, `:4786`); `DIRECT_OUTCOMES` (`:4840`) gains `thumbnail-image`; `IMAGE` (`:5268`) becomes `image-format`       |
| modify | `tests/test_probe.py`              | the Instagram sentence moves from the not-auth list (`:588`) to the auth one; `no-video-post` leaves `test_probe_does_not_ask_for_cookies_when_they_cannot_help` (`:894`); new probe and canary tests     |
| modify | `tests/test_downloader.py`         | `match_filter` on lone images                                                                                                                                                                          |
| modify | `tests/test_wizard_flow.py`        | the plan and the refusal for `saveable_image`                                                                                                                                                          |
| modify | `tests/test_cli.py`                | `_hint_for_download_error` on the image sentence, with and without cookies                                                                                                                             |

`equivalent.py`, `history.py`, `config.py`, `cookies.py` and `selection.py` don't change. If one has to, say so in the report.

**Items in flight.** Develop on `main` after `cancel-exits`, `m4a-cover`, `subs-failures` and `kind-edges` merge; the planner already puts this item after them. What meets:

- **`fix/cancel-exits`** (development) reshapes `main` and `_download_one`'s `except` chain in `cli.py` and rewords `image_only_skipped` and `no_supported_items`. This item touches neither. Its new refusal exit code (1) still applies to what stays refused (`image-format`), and stops applying to a lone image yt-dlp returns with a thumbnail, which is now saved (exit 0). The refusal tests this item edits (`test_a_lone_image_is_refused_at_once`, `test_wizard_on_a_lone_format_without_streams_is_refused_as_an_image`, and the multi-URL tests on `IMAGE`) are the ones cancel-exits flips from 0 to 1: edit them on top of its version. `-a` on a lone image ends in `no_supported_items`, exit 1, which is the download path's, not a refusal.
- **`fix/kind-edges`** (spec 37, approved) edits `_ProbeYdl.process_video_result` (`probe.py:293-298`) and the `may_hold_video` line of the same `Probe(...)` call this item adds a field to (`:339-340`). Textual. It also removes `storyboard-only` from the two refusal tests this item removes `ig-image` and `thumbnail-image` from; `image-format` is left in both.
- **`fix/subs-failures`** (spec 36, approved) adds fields to `DownloadResult` and its construction in `download()`, edits `_Ydl.process_info` (`:217-235`) and adds two loops to the warning block in `cli.py`. This item adds one field and one loop next to them. Keep the lone-image flag out of `process_info` (see Design) to stay off its lines.
- **`fix/m4a-cover`** (QA) changes `build_ydl_opts`' audio block and the post-processor order in `download()` (`:787-797`). No shared lines.

## Design

### Downloader: the image path takes lone results

- **`_Ydl.process_video_result`:** the image test becomes `self.save_images and is_saveable_image(info_dict)`, without `playlist_index`. The scoped params, the restore in `finally` and `_write_thumbnails`' image branch stay as spec 01 made them. Update the comment above `save_images` (`:159-162`): a lone result reaches the `match_filter` only after yt-dlp's no-formats check too.
- **Why it holds:** a lone result has no `:2157` filter call; yt-dlp's only filter call for it is at `:3037`, after `raise_no_formats` at `:2960`. With the params scoped, `:2960` only warns (hidden by `no_warnings`), and the filter at `:3037` then decides, as it does for linked carousel items. Measured: `-y -a` and `-y -l` each printed one skip line.
- **`_skip_items_without_media`:** a lone result (`incomplete` truthy, an `id`, no `playlist_index`) that `is_saveable_image` accepts is skipped with `item_image_skipped` and counted in `skipped` when `keep_images` is false (`-a`, `-l`), and passes otherwise. A lone result with nothing to fetch still passes, so yt-dlp's own error still ends it. Update the docstring.
- **`_AvoidNameClash.run`:** an item `is_saveable_image` accepts gets ` [<id>]`, as list items do (D2). By the time the `video` pre-processors run, the entry's `formats` is `[]`, so the test still holds; a video's isn't, and a URL-only result has a `url`.
- **The notice (D1):** `DownloadResult` gets `lone_images: tuple[str, ...] = ()`, the file names of images saved, or found already saved, for a lone result. One way that stays off `process_info`: `process_video_result` sets a flag on `_Ydl` when it takes the image path for an entry with no `playlist_index`; a lone URL has one entry, so `download()` names `ydl.images` when the flag is set. The developer may choose another way, as long as only lone images are named and only when saved.
- **Nothing else moves.** Failure (`image_not_saved`), rerun (`overwrites=False`), the `_WholeBody` check, the partial-file cleanup and the image count all come from spec 01's branch unchanged. No post-processor runs on an image (`skip_download`), so the container step, `_TagSource` and the subtitle steps never see it (spec 30's rule that images are left alone holds).

### Probe

- **`Probe.saveable_image: bool = False`:** set in `_read_result`'s single branch to `kind == ContentKind.SINGLE_IMAGE and is_saveable_image(data)`. True for the logged-in Instagram shape and `thumbnail-image`; false for `image-format`, where yt-dlp only has image formats. The default keeps every hand-built `Probe` in `test_wizard_flow.py` refused, as today.
- **`is_image_post_error(message) -> bool`:** true when `_site_words(message)` holds a phrase from a new `_IMAGE_POST_HINTS = ("there is no video in this post",)`, with a comment pointing at `instagram.py:530` and saying why: logged in, yt-dlp returns that post, and the tool saves it. `is_auth_error` returns true for it before `_NOT_AUTH_ERRORS` is read. So the probe sets `needs_cookies`, the wizard asks for cookies and probes again (spec 18's path), and a partly gated list holding such an entry asks too.

### Wizard

- **Retry message (`:591-592`):** when `is_image_post_error(probe_result.error_message or "")`, warn `image_post_needs_login` instead of `auth_needed_retry`. The cookies prompt and the second probe are unchanged.
- **Refusal (`:611-613`):** only `SINGLE_IMAGE` without `saveable_image` is refused with `image_only_skipped`.
- **After the banner (`:620-623`):** a saveable lone image sets `opts.audio_only = False` (D7). `opts.playlist_mode = "no"` already follows (`:633-638`).
- **`_build_post_probe_plan`:** for `SINGLE_IMAGE` the plan is `output`, plus `cookies` when not asked yet. No `type`, `quality`, `container` or `aformat`.
- **When cookies don't help:** the second probe fails again, and today's `probe_failed`, `auth_still_needed` and manual flow follow (row 38).

### CLI

- **`_show_preview` (D5):** build the preview's extractor from `downloader._Ydl`, looked up through the module at call time (the `fake_site` fixture patches `downloader._Ydl`, not `cli`), and set `save_images = True` after `load_browser_cookies`, on both the first instance and the cookieless one. `extract_info(url, download=False)` then returns the image's metadata instead of raising, and never writes (measured: declining leaves the folder empty). The `noplaylist`, `quiet` and `skip_download` params stay.
- **Warning block:** `for name in result.lone_images: warn(t("image_alone_saved", name))`, next to `not_converted` and `no_cover`, before `Done.`.
- **`_hint_for_download_error`:** after the `secretstorage` branch, when `is_image_post_error(message)`: without cookies, `warn(t("image_post_hint"))`; with cookies, `warn(t("auth_still_needed", browser_of(opts.cookies_browser)))`, since the run they were sent with still got the logged-out answer. The generic English hint is not printed for this case. The keyring hint keeps its own rule.

### Messages (both blocks)

| Key                       | es                                                                                                                                         | en                                                                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `image_post_needs_login`  | `Esta publicación no tiene video: puede ser una sola imagen, que el sitio solo muestra con la sesión iniciada. Con las cookies del navegador, download-media puede guardarla.` | `This post has no video: it may be a single image, which the site only shows to logged-in users. With browser cookies, download-media can save it.` |
| `image_post_hint`         | `  → puede ser una sola imagen, que el sitio solo muestra con la sesión iniciada: prueba de nuevo con --cookies <navegador> (firefox, brave, chrome, ...)` | `  → it may be a single image, which the site only shows to logged-in users: try again with --cookies <browser> (firefox, brave, chrome, ...)` |
| `image_alone_saved`       | `{}: yt-dlp no encontró video en esta URL, solo esta imagen, y la guardó.`                                                                 | `{}: yt-dlp found no video at this URL, only this image, and saved it.`                                                         |

The wording is a proposal. The developer may adjust it, keeping both blocks in step and the tests on `t()`.

### Docs

- **`AGENTS.md`:** the multi-entry paragraph says images are saved whether they're carousel items or the URL's only result, and that a lone one is always named with its id.
- **`README.md:17`:** carousels and single-image posts save their images; Instagram shows single-image posts only to logged-in users, so those need `--cookies`.

## Cases

"With cookies" means `--cookies firefox` on `fake_site` (never read), i.e. logged in. `ig-lone-image` is the page above.

| Case                                                     | Expected                                                                                                                                                       |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-y`, `-y -P`, with cookies                              | `Clip [i1].jpg`, `image_alone_saved`, `Done.`, exit 0, one history row (`video`). The printed command keeps `--cookies firefox`                                 |
| `-y -p`, `-y --playlist-items 1` (or `2`), with cookies  | Same file: a lone result keeps its single-mode name. No `items_not_found`, as for a lone video                                                                  |
| `--video-format mkv` or `webm`, `-s`                     | Same file; no container step on it. `-s` adds `subs_none`, as for any item without subtitles                                                                   |
| direct with preview, with cookies                        | `Title: Clip` in the preview, no yt-dlp `ERROR:` line, then the file. Declined: no files, exit 1                                                                |
| `-y -a`, `-y -a -p`, with cookies                        | `item_image_skipped`, `no_supported_items`, exit 1, no `.jpg`, no history row. The printed command is shown, as for any URL that reached the download             |
| `-y -l`, with cookies                                    | `item_image_skipped`, exit 0, no files                                                                                                                         |
| `-y` / `-y -p` / `-y -l` without cookies                 | yt-dlp's `There is no video in this post`, `Download failed`, `image_post_hint`, exit 1                                                                        |
| `-y` with cookies that don't log in (`no-video-post`)    | `cookies_retry`, `Download failed`, `auth_still_needed`, exit 1                                                                                                |
| wizard, no cookies, picks a browser at the prompt        | `image_post_needs_login`, cookies prompt, second probe, `Detected: generic / Image`, folder, save. 3 prompts (URL, cookies, folder). Exit 0                     |
| wizard `-i --cookies firefox`                            | Banner, folder, cookies (default firefox), save. 3 prompts                                                                                                     |
| wizard `-i -a --cookies firefox`                         | Saved as an image; the printed command has no `-a`                                                                                                             |
| wizard, declines cookies                                 | As today after that answer: `probe_failed`, manual flow, `Download failed`, now with `image_post_hint`. Exit 1 (row 38)                                        |
| wizard `-i -l --cookies firefox`                         | Folder, cookies, then `item_image_skipped`, exit 0                                                                                                             |
| multi-URL wizard, a video and the image, with cookies    | Both saved, one printed command naming both URLs, exit 0                                                                                                       |
| two lone images with one title (`ig-image`, `ig-lone-image-b`) | `Clip [i1].jpg` and `Clip [i9].jpg`                                                                                                                      |
| a lone image next to a video of the same title           | `Clip.mp4` and `Clip [i1].jpg`                                                                                                                                 |
| rerun, same or other mode                                | `Video thumbnail is already present`, no new file, same mtime, `image_alone_saved` again, `Done.`, exit 0                                                       |
| lone image whose thumbnail fails (`ig-lone-broken`)      | `image_not_saved`, `Download failed`, exit 1, no files, in `-y` and `-y -p`. With cookies, `cookies_retry` first                                                |
| other sites' lone results with no formats and a thumbnail (`thumbnail-image`, `ig-image`) | Saved, with `image_alone_saved` (D1). The wizard shows `Image` and walks the folder step                     |
| lone results that stay failures                          | `image` (no thumbnail), `no-url-format`, `drm`, `upcoming`, `no-video-post`: exit 1, no files, as today. `image-format`: `image_only_skipped` in the wizard   |
| carousel images                                          | Unchanged (spec 01)                                                                                                                                            |
| history                                                  | One row per saved URL, kind `video` (spec 01 D8)                                                                                                               |

## Acceptance criteria

**[fix]** fails on `main` without this item; **[pin]** holds on `main` and must keep holding.

New `fake_site` pages (`tests/conftest.py`):

- `ig-lone-image`: without cookies, `self.raise_no_formats("There is no video in this post", expected=True)`, `instagram.py:530` word for word; with cookies, `self._instagram_image("i1")`.
- `ig-lone-image-b`: `self._instagram_image("i9")`, with or without cookies.
- `ig-lone-broken`: `self._instagram_image("i1")` whose only thumbnail is `{media_url}/status/404/i1.jpg`.

Criteria:

- [ ] `scripts/pre-release.sh --no-live` passes.
- [ ] **[fix]** e2e `test_a_lone_image_with_cookies_is_saved_in_every_direct_mode`, parametrized over `-y`, `-y -P`, `-y -p`, `-y --playlist-items 1`, `-y --video-format mkv`: with `--cookies firefox` on `ig-lone-image`, the folder holds exactly `Clip [i1].jpg`, byte-identical to the fixture's `tiny.jpg`; exit 0; `t("done")`, `t("image_alone_saved", "Clip [i1].jpg")`; no `t("cookies_retry", …)`, no `partial_result`, no `No video formats found`; one history row of kind `video`.
- [ ] **[fix]** e2e `test_a_lone_image_rerun_adds_nothing`: `-y --cookies firefox` twice, then `-y -p --cookies firefox`: one file, unchanged mtime, exit 0 each time.
- [ ] **[fix]** e2e `test_the_preview_reads_a_lone_image`: `--cookies firefox`, Enter: `f"{t('p_title')}: Clip"` in the output, no `ERROR:` line, `Clip [i1].jpg` saved. Answering `n`: no files, exit 1.
- [ ] **[fix]** e2e `test_the_preview_reads_a_carousel`: on `ig-carousel`, `f"{t('p_title')}: Post"` and no `ERROR:` line before the confirmation. The four files of spec 01 follow.
- [ ] **[fix]** e2e `test_audio_mode_skips_a_lone_image`, over `-y -a` and `-y -a -p`, with cookies: `t("item_image_skipped", "i1")`, `t("no_supported_items")`, exit 1, no file, no history row.
- [ ] **[fix]** e2e `test_list_mode_names_a_lone_image`: `-y -l --cookies firefox`: `t("item_image_skipped", "i1")`, exit 0, no files.
- [ ] **[fix]** e2e `test_a_lone_image_without_cookies_says_cookies_may_save_it`, over `-y`, `-y -p`, `-y -l`: `t("failed", url)` (`_list_formats` prints it too), `t("image_post_hint")`, no `→ try again with --cookies <browser>` line, exit 1, no files.
- [ ] **[fix]** e2e `test_cookies_that_do_not_log_in_say_so_on_an_image_post`: `-y --cookies firefox` on `no-video-post`: `t("cookies_retry", "firefox")`, `t("auth_still_needed", "firefox")`, no `t("image_post_hint")`, exit 1.
- [ ] **[fix]** e2e `test_wizard_asks_for_cookies_on_a_lone_image_and_saves_it`: `-i` on `ig-lone-image`, replies URL `""`, browser `str(BROWSERS.index("firefox") + 2)`, folder `""`: exactly 3 prompts, `t("image_post_needs_login")`, `detected("kind_image")`, no `t("ask_type")`, `t("ask_quality")` or `t("ask_vformat")`; `Clip [i1].jpg`; exit 0; the printed command is `download-media -y -o <out> --cookies firefox https://fake.test/ig-lone-image`, and replayed into the same folder it adds nothing.
- [ ] **[fix]** e2e `test_wizard_with_cookies_walks_only_the_folder_for_a_lone_image`: `-i --cookies firefox`, 3 prompts (URL, folder, cookies), saved. With `-i -a --cookies firefox`: saved as `.jpg`, and the printed command has no `-a`.
- [ ] **[fix]** e2e `test_wizard_saves_a_lone_image_among_other_urls`: `-i --cookies firefox -o <out> <video> <ig-lone-image>`: `Clip.mp4` and `Clip [i1].jpg`, no `wizard_skipped`, exit 0, one printed command naming both URLs.
- [ ] **[pin]** e2e: the wizard declining cookies on `ig-lone-image` still walks the manual flow to `Download failed`, exit 1, no files, now with `t("image_post_hint")`. It pins today's walk for row 38 to change.
- [ ] **[fix]** e2e `test_lone_images_are_named_by_their_id`: `-y ig-image ig-lone-image-b` saves `Clip [i1].jpg` and `Clip [i9].jpg`; `-y video ig-image` saves `Clip.mp4` and `Clip [i1].jpg`.
- [ ] **[fix]** e2e: `-y` and `-y -p` on `ig-lone-broken`: `t("image_not_saved", "i1")`, exit 1, no files. With `--cookies firefox`: `t("cookies_retry", "firefox")` too.
- [ ] **[fix]** `DIRECT_OUTCOMES["thumbnail-image"]`: `""`, `-p`, `-P`: `(0, ["Clip [i1].jpg"])`; `-a`: `(1, [])`; `-l`: `(0, [])`.
- [ ] **[pin]** e2e: `-y` on `image`, `no-url-format`, `drm`, `upcoming` and `no-video-post` (no cookies): exit 1, no files. `image-format` stays in the two refusal tests, `ig-image` and `thumbnail-image` leave them, and the multi-URL refusal tests use `IMAGE = "https://fake.test/image-format"` with their prompt counts unchanged.
- [ ] **[fix]** `test_probe.py`: `probe(ig-lone-image)` without cookies: `failed`, `needs_cookies`, `error_message == "There is no video in this post"`. With cookies: `SINGLE_IMAGE`, `saveable_image`. `thumbnail-image`: `saveable_image`. `image-format`: `SINGLE_IMAGE`, not `saveable_image`.
- [ ] **[fix]** `test_probe.py`: `is_auth_error` and `is_image_post_error` are true for `"[Instagram] x: There is no video in this post"`, which moves out of the not-auth list; `no-video-post` leaves `test_probe_does_not_ask_for_cookies_when_they_cannot_help`; `test_a_failure_with_metadata_never_reads_as_an_image[no-video-post]` stays.
- [ ] **[pin]** `test_probe.py::test_instagram_still_says_there_is_no_video_in_this_post`: `inspect.getsource(yt_dlp.extractor.instagram.InstagramIE._real_extract)` contains `There is no video in this post`. A reworded yt-dlp fails the gate instead of silently dropping the hint.
- [ ] **[fix]** `test_downloader.py`: the `match_filter` skips a lone `is_saveable_image` entry with `item_image_skipped` and counts it once when `keep_images=False`, passes it when `True`, and passes a lone entry with nothing to fetch in both.
- [ ] **[fix]** `test_wizard_flow.py`: a `Probe(SINGLE_IMAGE, saveable_image=True)` plans `output` and `cookies`, and turns off `audio_only`; `Probe(SINGLE_IMAGE)` is still refused.
- [ ] **[fix]** `test_cli.py`: `_hint_for_download_error` on the sentence prints `image_post_hint` without cookies and `auth_still_needed` with them, and never the generic line.
- [ ] `test_i18n.py` passes: the three keys are in both blocks.
- [ ] `AGENTS.md`, `README.md` and `.ai/live.env.example` carry the lines in Design.

## Live check

Needs the human's Instagram session, so it can't run overnight: an open item for the morning. Run it in the validation stage, through the tool only, one command at a time, with no other live run in progress (Instagram rate-limits by IP). `DM_LIVE_IG_IMAGE` is a public post holding a single image, which spec 18's step 3 already used in 0.3.0.

```bash
set -a; . .ai/live.env; set +a     # DM_LIVE_IG_IMAGE, DM_LIVE_BROWSER
# 1. Without cookies: the hint, nothing saved.
.venv/bin/download-media -y -o tmp/ig-lone "$DM_LIVE_IG_IMAGE"; echo "exit $?"
ls -A tmp/ig-lone | wc -l                                            # 0
# 2. With cookies: one image.
.venv/bin/download-media -y --cookies "$DM_LIVE_BROWSER" -o tmp/ig-lone "$DM_LIVE_IG_IMAGE"; echo "exit $?"
ls tmp/ig-lone                                                       # Video by <user> [<id>].jpg
ffprobe -v error -show_entries stream=width,height -of csv=p=0 tmp/ig-lone/*.jpg
# 3. Rerun: nothing new.
.venv/bin/download-media -y --cookies "$DM_LIVE_BROWSER" -o tmp/ig-lone "$DM_LIVE_IG_IMAGE"
ls -A tmp/ig-lone | wc -l                                            # 1
# 4. Wizard, by hand: Enter at the URL, pick the browser at the cookies prompt, Enter at the folder.
.venv/bin/download-media -i -o tmp/ig-lone-wizard "$DM_LIVE_IG_IMAGE"
```

What must hold:

1. Step 1: `There is no video in this post`, `Download failed`, the `image_post_hint` line, exit 1, no files.
2. Step 2: `image_alone_saved`, `Done.`, exit 0, one `.jpg`. Record its size: with login the candidates carry sizes, so it should be the post's largest, not a 1080×1080 square crop (unless the post is square).
3. Step 3: `already present`, still one file.
4. Step 4: `image_post_needs_login`, one cookies prompt, `Detected: instagram / Image` with the title, the folder prompt, no type or quality step, one `.jpg` with the same name as step 2.
5. If the logged-in run prints anything else (yt-dlp's `No video formats found!`, a refusal, a carousel), record it: it corrects this spec's Findings, which rest on `instagram.py` and the 0.3.0 run.

## Open decisions

All recommended options are what this spec describes. **(published behaviour)** marks the ones for the 0.4.0 review.

- **D1, which lone results are saved (published behaviour).** **A (recommended):** any lone result yt-dlp returns with no formats, no `url` and a thumbnail (spec 01's one predicate), followed by `image_alone_saved`. It's the wrapper rule: no site name in the code, and Instagram's logged-in shape is exactly that. Its cost: an extractor that gives up on a video without raising (formats from a manifest fetched with `fatal=False`, as `banbye.py:146` and `ebay.py:26-28` do) and still sets a thumbnail now saves that thumbnail, exit 0, where yt-dlp failed with `No video formats found!; please report this issue`. The wizard's `Image` banner and the notice make it visible; a script sees exit 0. **B:** the same, without the notice: simpler, and that case passes silently. **C:** only results whose `extractor_key` is `Instagram`: no false success elsewhere, but it's per-site code, which `project.md` rules out.
- **D2, the name (published behaviour).** **A (recommended):** always `<title> [<id>].<ext>`. A JPEG carries no source URL (no post-processor runs on it), so `_AvoidNameClash` can't tell its own file from another post's, and Instagram titles every post `Video by <user>`: without the id, a second post by the same user would be taken as "already present" and never saved. **B:** `<title>.<ext>` with the id only on a clash. A rerun would then see a clash with its own file and save a second copy.
- **D3, `-a` (published behaviour, small).** **A (recommended):** skip it with `item_image_skipped` and fail the URL with `no_supported_items`, exit 1, as spec 01 D7 skips carousel images. **B:** save the image anyway, into the audio folder.
- **D4, `-l`.** **A (recommended):** `item_image_skipped`, exit 0, as `-l` treats carousel images; it falls out of D3's filter. Today: yt-dlp's `No video formats found!`, exit 1. **B:** keep today's failure, which needs code to undo the filter.
- **D5, the preview (published behaviour).** **A (recommended):** read the page through `_Ydl` with images enabled. Without it, a lone image's preview prints yt-dlp's `ERROR: … No video formats found!; please report this issue` and an empty block, then the download succeeds. Side effect: a carousel's preview shows its title and loses the same `ERROR:` line, which is row 63's symptom; the planner should re-scope or close row 63. **B:** leave the preview; row 63 then covers lone images too.
- **D6, without login (published behaviour).** **A (recommended):** recognise yt-dlp's sentence `There is no video in this post` as a case cookies can fix: the wizard asks for cookies with `image_post_needs_login`, direct mode prints `image_post_hint`, and cookies that still get the sentence get `auth_still_needed`. It reverses spec 18's "No" for this row, because images couldn't be saved then and can now. The sentence is one extractor's, so a canary test fails the gate if yt-dlp rewords it. **B:** leave it: the user sees `There is no video in this post`, then `Download failed`, and never learns that cookies would save it. **C:** add it to `_AUTH_ERROR_HINTS`, reusing `auth_needed_retry`: `This content needs a login: There is no video in this post.` reads as a contradiction.
- **D7, wizard steps.** **A (recommended):** as spec 01 D3: folder (and cookies) only, and `-i -a` saves the image, with no `-a` in the printed command. **B:** ask type, quality and container, which do nothing for an image.

**Open items for the morning, not blockers:** the live check (the human's cookies and `DM_LIVE_IG_IMAGE`), and the review of D1, D2, D5 and D6.

## SemVer

A feature, so minor: three new messages, and images saved that weren't. No flag, default, path or state format changes. 0.4.0 is already a minor bump, so it rides along. For the 0.4.0 review list:

1. D1: a lone image post saves with cookies, in every mode but `-a` and `-l`, followed by `<file>: yt-dlp found no video at this URL, only this image, and saved it.` On any site, a lone result with no formats and a thumbnail is saved the same way, where `-y` failed and the wizard refused it.
2. D2: lone images are named `<title> [<id>].<ext>`.
3. D3, D4: `-a` on a lone image names it and fails with `No items are downloadable`; `-l` names it and exits 0 instead of 1.
4. D5: the direct preview of a lone image or a carousel shows its title and no yt-dlp `ERROR:` line.
5. D6: without login, Instagram's `There is no video in this post` now asks for cookies in the wizard, with a new message, and adds a hint in direct mode. New messages: `image_post_needs_login`, `image_post_hint`, `image_alone_saved`.
6. A lone image post that the wizard used to refuse (exit 0, then 1 under cancel-exits) is now saved, exit 0.

Release note, for `.ai/releases/0.4.0.md`: "Single-image posts are saved too, as `<title> [<id>].jpg`, when the site returns them. Instagram only does with browser cookies (`--cookies <browser>`), and the tool now says so when you try without them."
