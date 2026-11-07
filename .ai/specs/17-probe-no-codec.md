# 17 — Stop the probe mistaking media without codec info for images

Status: done
Item: `fix/probe-no-codec`

## Problem

The wizard's probe reads a missing codec as "no stream", so it refuses videos and audio that `-y` downloads, and in one case silently saves the wrong thing.

Reproduced on `main` at `b29f86a`, with yt-dlp 2026.10.26, the real console script and the real generic extractor on a loopback server, and `HOME` and `XDG_*` pointed under `tmp/`:

```bash
# <folder> holds tiny.mp4, its copies v1.mp4 to v4.mp4, tiny.jpg, and these pages:
#   multi.html   <video src="v1.mp4"></video> up to v4.mp4
#   poster.html  <video src="tiny.mp4" poster="tiny.jpg"></video>
python -m http.server 18791 --bind 127.0.0.1 --directory <folder> &

printf '\n' | download-media http://127.0.0.1:18791/multi.html
# "No items are downloadable. Cancelling." / "Cancelled." — exit 0, no files
printf '\n' | download-media http://127.0.0.1:18791/poster.html
# "This content is a standalone image, not supported by yt-dlp." — exit 0, no files
download-media -y -o tmp/x http://127.0.0.1:18791/multi.html    # exit 0, 4 files
download-media -y -o tmp/x http://127.0.0.1:18791/poster.html   # exit 0, 1 file
```

- **What happened:** the wizard says the media can't be downloaded, and exits 0.
- **What should happen:** the wizard offers what `-y` saves.

## Goal

The wizard labels a URL by what yt-dlp would do with it. Media without codec info downloads through the wizard as it does through `-y`, and real images stay images.

## Findings

Every row was run through the real console script. The page shapes are the ones the generic extractor produced; "Formats" is what `extract_info` returned to the probe.

| #   | Page                                                 | Formats                                       | Wizard today                                                         | `-y` today     |
| --- | ---------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------- | -------------- |
| F1  | Several `<video src>`                                | `vcodec` and `acodec` `None`                  | Items are `image`, supported: "No items are downloadable", exit 0    | Saves all      |
| F2  | One `<video>` with a poster. Same for `.webm`, `<source>`, HLS without `CODECS` | `vcodec` and `acodec` `None` | "standalone image", exit 0                                           | Saves it       |
| F3  | One `og:video`, with an `og:image`                   | **No `formats` key**, only top-level `url`    | "standalone image", exit 0                                           | Saves it       |
| F3  | Two `og:video`                                       | Same, per entry                               | `supported=False`: "No items are downloadable", exit 0               | Saves both     |
| F4  | `<source type='video/mp4; codecs="avc1…, mp4a…"'>`   | `vcodec` `None`, `acodec` `mp4a.40.2`         | **`Audio`: no type step, saves `Music/*.mp3`, says `Done.`, exit 0** | Saves the mp4  |
| F5  | `<audio src="x.m4a">` with an `og:image`             | `vcodec` `'none'`, `acodec` `None`            | "standalone image", exit 0                                           | Saves it       |
| F5  | Same without an image; direct `.m4a`, `.ogg`, `.wav` | Same                                          | `Unknown`: raw file into `~/Videos`, plus a second confirmation      | Saves it       |
| F5  | Several `<audio src="x.m4a">`                        | Same, per entry                               | "No items are downloadable", exit 0                                  | Saves all      |
| F6  | Entries with `vcodec` `'none'`, `acodec` `mp3`, a duration | Known                                   | Labelled `video`, from `or duration > 0` (`probe.py:155`)            | Saves all      |

F1 and F2 are the reported bug. F3 to F6 are the same rule failing in other ways, found while reproducing, and the plan row doesn't mention them. F4 is the worst one: the wrong file reported as success. Whether a page reaches the "standalone image" branch depends only on it having a thumbnail: the same `<video>` without a poster comes out `Unknown` and downloads.

`probe.py:153` and `:178` are still the right lines. `:156`, `:179` and `supported=bool(formats)` at `:170` share the problem.

### How yt-dlp represents these

Confirmed in the 2026.10.26 source:

- **`'none'` means absent, `None` means unknown.** Every codec selector drops only formats with both set to `'none'` (`filter_f`, `YoutubeDL.py:2608-2609`), and `_fill_sorting_fields` sets `video_ext = ext` for anything but `vcodec == 'none'` (`utils/_utils.py:5630-5635`). So yt-dlp treats an unknown `vcodec` as video.
- **yt-dlp's own image test** is `formats and all(f.get('acodec') == f.get('vcodec') == 'none' ...)`, which warns "only images are available" (`YoutubeDL.py:2930-2933`).
- **A url-only result has no `formats` key after processing:** `_get_formats` returns `[info_dict]` (`:3973`), and `:3028` skips writing it back, to avoid a circular reference. This is F3. `generic.py:1294` builds these for `og:video`, `twitter:player:stream`, JW Player and others.
- **`acodec` is filled from the extension only for `aac`, `opus`, `mp3`, `flac`, `vorbis`** (`YoutubeDL.py:2967`). That's why `<audio src="x.mp3">` works today and `x.m4a` doesn't. `vcodec` is never filled, so `{"url": "x.mp3"}` ends with `vcodec` `None` and `acodec` `mp3`.
- **F4's cause:** `_parse_html5_media_entries` parses the codecs from `type`, then `f.update(formats[0])` overwrites `vcodec` with the `None` from its own plain format (`extractor/common.py:3450`). `acodec` survives.
- **Direct links:** `generic.py:873-878` sets `vcodec` `'none'` for `audio/*` and `None` otherwise. A non-HTML response that isn't audio or video, such as a `.jpg`, becomes url-only with the URL's extension (`:903-913`).

### Real images

| Shape                                                                         | What the probe gets                                                                                              | Today                                                                          |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Instagram carousel image: `formats: []`, no `url`, `thumbnails` (`instagram.py:170-182`) | `raise_no_formats` (`:2960`) raises, `ignoreerrors` makes the entry `None`                            | `_classify_entry(None)`: image, unsupported. Correct                           |
| Lone image, same shape                                                        | `data` is `None`: `failed=True`, `error_message=None`                                                            | "Could not inspect URL: unknown", manual flow, then fails, exit 1              |
| Lone format with both codecs `'none'`                                         | "Requested format is not available", so `data` is `None`                                                         | Same as above                                                                  |
| Direct image URL (`.jpg`, `.png`)                                             | url-only, `ext` `jpg`, no codecs                                                                                 | `Unknown`; the wizard and `-y` **save it**, exit 0. `-a` fails, exit 1, honestly |
| Page with only `<img>`                                                        | Unsupported URL, `data` is `None`                                                                                | "Could not inspect URL: unknown", exit 1                                       |

Two things follow:

1. **Today, `image_only_skipped` never fires on an image yt-dlp has nothing to fetch for.** Those fail the probe first, with `failed=True`. It only fires on results that have a URL, and `-y` saves those unless the URL is dead. Even `<video src="x.jpg" poster="x.jpg">` is refused by the wizard and saved by `-y`. `test_lone_image_is_an_error_not_a_skip` and `FakeSiteIE._image` pin the failing path.
2. **An extension can't mean "refuse".** A direct `.jpg` downloads today, so calling it an image would take away something that works.

The shapes Instagram images use are pinned by `FakeSiteIE._image`, `test_carousel_of_only_images_is_a_failure`, `test_lone_image_is_an_error_not_a_skip`, `test_wizard_with_nothing_supported_prints_no_command`, `test_single_image_cancels`, `test_classify_entry_none_is_image` and `test_classify_single_image`. All pass with the prototype below.

### How far it reaches

Static count over the 877 extractor modules in yt-dlp 2026.10.26 (AST walk, a rough measure of exposure, not of failures):

- 697 (79%) never mention `vcodec` or `acodec`.
- 584 (67%) build a literal format with a `url` and no codec, or call the HLS helpers. HLS media playlists carry no codecs at all (`common.py:2260`), and masters only with `CODECS`.

Instagram itself is exposed: `_extract_product_media` sets `vcodec` only from `video_codec`, and `acodec` only to `'none'` (`instagram.py:159-162`). On a scratch extractor with that shape, the wizard refuses a video post as "a standalone image". How often real posts arrive without `video_codec` or a DASH manifest hasn't been measured: the live check covers it.

## Scope

### In

- One rule in `probe.py` for what a format and an entry are, used by both classifiers: F1 to F6.
- `wizard.py:502`, so a page of only unknown items isn't refused.

### Out

For the planner, all reproduced:

- **A relative `og:image` crashes any `-a` run with a traceback:** yt-dlp's `_write_thumbnails` raises `NoSupportingHandlers`, which isn't a `DownloadError`, so `_download_one` doesn't catch it. An absolute one works. Happens today with `-y -a`. See D4, since this item makes it reachable from the wizard.
- **Nested playlists are still labelled `image`.** An entry that is itself a playlist has no `formats` or `url`. `-y -p` saves its items. It can't be fixed by offering the entry: `--playlist-items 1` also filters the inner playlist, so on an outer 2-entry list with a 2-item inner one it saved one of the two inner files and reported success. All-nested pages keep today's refusal; on a mixed page the nested entry shows as `[image]`, and `all` still reaches it.
- **The container choice has no effect on a single-format source:** `--video-format mkv` and the default mp4 both save a `.webm` as `.webm`, with known codecs too. `merge_output_format` only applies to merges. See D3.
- **All-audio playlists still get the type step, defaulting to video,** so the original file lands in `~/Videos`. Same today for known codecs.
- **`-a` leaves a stray file on failure:** `broken (1).jpg` after a 404, `tiny.jpg` when the source has no audio.
- **An `Unknown` single keeps the extra `Proceed with download?`,** because `playlist_mode` stays `auto`. Today's behavior; this item only shrinks how many pages hit it.
- **`step_select` blames Instagram for any `None` entry** (`ig_image_warning`), on any site.

Handled by other rows: the exit code of a wizard refusal (`cancel-exits`), `ignoreerrors` hiding the real error (`probe-auth`), `-l` opening the wizard (`intent-flags`), `NA` in `-p` file names on single pages (`playlist-items-fix`).

## Files

| Action | File                          |
| ------ | ----------------------------- |
| modify | `src/download_media/probe.py` |
| modify | `src/download_media/wizard.py` |
| modify | `tests/conftest.py`           |
| modify | `tests/test_probe.py`         |
| modify | `tests/test_wizard_flow.py`   |
| modify | `tests/test_e2e.py`           |

`downloader.py`, `cli.py` and `i18n.py` don't change. No new message, flag or dependency. `yt_dlp.utils.MEDIA_EXTENSIONS` exists with the same fields at the `pyproject.toml` floor, checked in the 2024.10.07 source.

## Design

Written for the recommended option of each decision. A prototype of the approved rule, applied at runtime from a scratch folder, produced every "after" result in this spec. One row was added after approval, and it changes the `.ogg` results: see "Changes after approval".

### The rule

One format, in this order:

| `vcodec`  | `acodec`    | `ext`                       | Kind      | Why                                                                                     |
| --------- | ----------- | --------------------------- | --------- | --------------------------------------------------------------------------------------- |
| `'none'`  | `'none'`    | any                         | image     | yt-dlp's own test; no codec selector can pick it                                        |
| `'none'`  | anything else | any                       | audio     | No video, and audio isn't ruled out (F5)                                                |
| named     | any         | any                         | video     |                                                                                         |
| unknown   | any         | `asf`, `m4b`, `ogg`, `ogx`, `wma` | video | They can hold a video yt-dlp keeps. Added after approval (`bec654d`) |
| unknown   | any         | any other in `MEDIA_EXTENSIONS.audio` | audio | `.m4a`, or `.mp3` with `acodec` filled in by yt-dlp. Without this, `{"url": "x.mp3"}` flips from audio to video |
| unknown   | any         | in `MEDIA_EXTENSIONS.thumbnails` | unknown | Offered, never refused; a direct `.jpg` works                                         |
| unknown   | any         | anything else               | video     | What yt-dlp does (F1 to F4)                                                             |

`acodec` deliberately doesn't decide anything once `vcodec` isn't `'none'`: that's F4.

One entry: take its formats, or the entry itself when it's url-only, and return the strongest kind found, in the order video, audio, unknown, image. So a storyboard next to a real format doesn't turn it into an image. No formats and no `url` means nothing to fetch.

```python
def _media_kind(info: dict[str, Any]) -> ContentKind | None:
    """What ``info`` holds, or None if there is nothing to fetch."""
    formats = info.get("formats") or ([info] if info.get("url") else [])
    kinds = {_format_kind(f) for f in formats}
    ...
```

### `probe.py`

- Add `_format_kind` and `_media_kind` next to the classifiers. Use `from yt_dlp.utils import MEDIA_EXTENSIONS`: our own extension list would re-implement yt-dlp's. The five extensions in the rule table are the one exception: `_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO` narrows yt-dlp's list and doesn't copy it.
- `_classify_entry`: `kind = _media_kind(entry) or ContentKind.SINGLE_IMAGE`, and `supported=kind != ContentKind.SINGLE_IMAGE`. Drop `duration` from the decision (F6); keep it for display. The `None` branch stays as it is.
- `_classify_single`: return `_media_kind(data)` when it isn't `None`. Otherwise keep today's tail: thumbnails mean image, nothing means unknown.
- **Keep the thumbnails branch and `image_only_skipped`,** although after this change no real run reaches them. See "`probe-auth`".
- Remove `supported_video_count` and `supported_audio_count`: this change leaves them unused.

### `wizard.py`

`:502-503` becomes `if not any(it.supported for it in probe_result.items):`. Otherwise a page of only `?` items is refused. `step_select` needs nothing: it already prints `?`, and Enter already falls back from videos to any supported item.

### Does it agree with `downloader.py`?

The match filter asks "is there anything to fetch?" (no `formats`, no `url`). They should agree on that exact case, and after this change they do, because `_media_kind` returning `None` is the same test. Before it, the probe read only `formats`, which is why F3's second row disagrees with `-y`.

They shouldn't become one function:

- The filter runs on the raw entry, with `incomplete=True`, before any format processing. The probe runs on processed data and also has to say what the media is.
- The filter has its own conditions: playlist metadata and lone URLs must pass.
- Formats with both codecs `'none'` are unsupported in the probe and pass the filter, so yt-dlp reports them as a failed item. That's an honest failure, and the filter can't see it any earlier without duplicating format selection.

So: two definitions, one shared case, pinned by a test that the wizard's choice on a carousel saves what `-y -p` saves.

### What the wizard shows without codec info

Question by question:

- **Nothing in the wizard reads a codec.** Only the probe does. `step_quality`, `step_container` and `step_aformat` are static lists, and `_print_detection` already omits what it lacks.
- **Quality works when heights are known and does nothing when they aren't.** Measured on `<source res=…>`: 480 picks the 480 source, 1080 the 1080 one. With no height, `[height<=?N]` keeps everything, so the file is the same at any setting. Nothing fails.
- **Container has no effect on a single-format source,** with or without codecs. The summary can say `mp4` and the disk `.webm`. That's already true of `-y`, but this fix sends more pages through that step. See D3.
- **`format_sort`** ranks unknown codecs below `h264`, and that changes nothing when all are unknown.

### Exit codes and messages

- A false "standalone image" stops happening because the refusal no longer fires on those pages, not because the exit code changes. They now end in `Done.` with a file, or in `Download failed`, exit 1, if the media is really gone (`broken.html`).
- A real image-only page keeps today's message and exit code: a carousel of only images says `no_supported_items` and exits 0, a lone one fails the probe and exits 1.
- Whether a real refusal should exit 0 is a separate question, and it belongs to `cancel-exits`, where it's already listed. `test_wizard_with_nothing_supported_prints_no_command` keeps pinning 0 until then.

### `probe-auth` (row 18)

Nothing here makes it harder. The two touch different code: this item changes `_classify_*` (`probe.py:139-186`) and `wizard.py:502`, and `probe-auth` changes `probe()` (`:189-244`) and `wizard.py:483-494`. What that item needs to know:

1. **Dropping `ignoreerrors` alone breaks every carousel.** Measured: `fake.test/carousel` raises "No video formats found!" at the first image. It needs `ignore_no_formats_error=True`.
2. **With that option, images arrive as dicts with `formats: []`, not `None`.** Measured on `carousel` and on the Instagram shape. This design already classifies those as unsupported images, and a unit test here pins that.
3. **The kept thumbnails branch becomes reachable, and true.** An Instagram-shaped lone image then returns `formats: []` with thumbnails, so `image_only_skipped` fires on a real image for the first time. Don't delete it as dead code in between.
4. **`FakeSiteIE._image` has no `thumbnails`; real Instagram images do.** Under row 18, `fake.test/image` would come out `Unknown`, not image. It should add them to the fixture, or decide that `formats: []` alone means image.
5. **`_classify_entry(None)` still says "image".** `None` really means "any extraction error", and this item leaves that alone. Row 18 is where to split them.

## Cases

Only the wizard and `-i` call `probe()` (`wizard.py:482`, `:494`). Direct, `-y`, `-p` and `-P` never classify anything, so they must not change at all.

| Case                                             | Expected                                                                                     |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| wizard, one no-codec video (poster, `og:video`, `.webm`, HLS) | `Video`, usual steps, saved in `~/Videos`, no extra confirmation                |
| wizard, several `<video>`                        | Picker with `[video]`; Enter saves all, in `yes` mode                                        |
| wizard, two `og:video` (url-only entries)        | Same; entries are supported                                                                  |
| wizard, video with a partial codec (F4)          | `Video`, type step shown, saves the `.mp4`                                                   |
| wizard, one `.m4a` or `.wav`, or `.ogg` with `vcodec` `'none'` (`<audio>`, or a direct link served as `audio/*`) | `Audio`, no type step, `aformat`, `.mp3` in `~/Music`. Today: raw file in `~/Videos` |
| wizard, `.ogg`, `.wma`, `.m4b`, `.asf` or `.ogx` with an unknown `vcodec`, such as `<video src>` | `Video`, type step shown, raw file in `~/Videos`. `.ogx` then ends in `Download failed`, exit 1, file kept, as it does with `-y` |
| wizard, direct link to a video served as `audio/*` | `Audio`, `.mp3` in `~/Music`, video lost. Accepted for now: see "Changes after approval" |
| wizard, several audio                            | Picker with `[audio]`; Enter saves all. The type step stays (see Out)                        |
| wizard, video plus audio on one page             | `[video]`, `[audio]`; Enter saves the video, as the prompt says; `all` saves both            |
| wizard, direct `.jpg`                            | Unchanged: `Unknown`, saved                                                                  |
| wizard, `<video src="x.jpg">`, alone             | `Unknown`, saved. Today: refused                                                             |
| wizard, `.jpg` entry next to a video             | `[?]`, `[video]`; Enter saves the video, `all` both                                          |
| wizard, only `.jpg` entries                      | `[?]`, `[?]`; Enter saves both, through the fallback                                         |
| wizard, carousel of images and no-codec videos   | Images stay `[image]`, unsupported; Enter saves the videos                                   |
| wizard, carousel of only images                  | Unchanged: `no_supported_items`, exit 0, no command                                          |
| wizard, lone image without `url`                 | Unchanged: probe fails, manual flow, exit 1                                                  |
| wizard, media file gone (404), single            | `Video`, then `Download failed`, exit 1. Today: "standalone image", exit 0                   |
| wizard, media file gone, one of two entries      | Partial result, `Done.`, exit 0, as `-y -p`. Today: refused                                  |
| wizard, page with a relative `og:image`, type audio | Traceback, exit 1 (D4). Same as `-y -a` today                                             |
| `-i`                                             | As the wizard. `-i -a` on a poster page preselects audio: `.mp3`. `-i -P` on `multi.html` still shows the picker and saves 4 files, as the wizard's own decision overrides `-P` on any multi-entry URL |
| direct with preview, `-y`, `-p`, `-P`            | Unchanged on every page above: same files, same exit code                                    |
| `-y -a`                                          | Unchanged                                                                                    |
| `-l`                                             | `-y -l` is unchanged. `-l` alone opens the wizard (`intent-flags`): on these pages it now walks the steps and lists formats, instead of refusing |
| with / without cookies                           | No difference: the rule reads the result, and `cookiesfrombrowser` is unchanged              |
| single, carousel, playlist                       | Covered above                                                                                |
| rerun                                            | Wizard twice on a poster page: one file, `Done.` both times                                  |
| printed command                                  | `-y <url>` on a single; `-y --playlist-items 1,2,3,4 <url>` on `multi.html`; replay saves the same files |
| history                                          | One row per run that saved something. Today: none on refused pages                           |

## Acceptance criteria

Every criterion marked **[fix]** needs a test that fails on `main`, shown by reverting the fix (`constraints.md`, Verification 5). Those marked **[pin]** protect behavior that must not change, so they pass both before and after: their value is that they keep passing.

Tests that depend on yt-dlp use the real thing:

- **Generic pages:** add them to the `media_url` root in `tests/conftest.py`, along with `tiny.jpg` and `tiny.m4a`. Write them after the server starts, so `og:image` and `og:video` are absolute. They need a run fixture that doesn't use `fake_site`, since that one loads only `FakeSiteIE`, and the existing `home` fixture for `~/Videos` and `~/Music`.
- **`fake_site`:** add pages to `FakeSiteIE` for shapes the generic extractor can't produce. The image entries stay `_image`.
- The `answers` fixture fails on an unscripted prompt, so scripted walks also prove no extra confirmation appears.

Generic extractor, loopback:

- [x] `scripts/pre-release.sh --no-live` passes
- [x] **[fix]** Wizard on four `<video src>`: exit 0, 4 `.mp4` files, `t("done")` in the output, `t("no_supported_items")` not in it, 1 history row
- [x] **[fix]** Wizard on `<video src poster>`, six answers: exit 0, one `.mp4` in `~/Videos`, `t("kind_video")` in the output, `t("image_only_skipped")` not in it
- [x] **[fix]** Wizard on a page with `og:video` and `og:image`: same result
- [x] **[fix]** Wizard on two `og:video`: exit 0, 2 files
- [x] **[fix]** Wizard on `<source type='video/mp4; codecs="avc1.42E01E, mp4a.40.2"'>`: `.mp4` in `~/Videos`, nothing in `~/Music`
- [x] **[fix]** Wizard on `<audio src="tiny.m4a">` with an absolute `og:image`: `.mp3` in `~/Music`, `t("kind_audio")` in the output
- [x] **[fix]** Wizard on two `<audio src="x.m4a">`, Enter: 2 files, both items tagged audio
- [x] **[fix]** Wizard on `<video>` plus `<audio>`, Enter: only the video is saved
- [x] **[fix]** Wizard on `<video src="tiny.jpg">` and a real video: the output has `[?]` and `[video]`; Enter saves the `.mp4` only, `all` saves both
- [x] **[fix]** Wizard on a poster page whose media file is missing: exit 1, `t("failed", url)` and no `t("done")` in the output
- [x] **[fix]** Wizard on one real and one missing `<video>`: exit 0, 1 file, `t("partial_result", ...)` in the output
- [x] **[fix]** `-i -a` on the poster page: one `.mp3`
- [x] **[fix]** Printed command after each of the first two walks: replay saves the same files, no prompt
- [x] **[fix]** Wizard twice on the poster page: one file, exit 0 both times, 2 history rows
- [x] **[pin]** Wizard on a direct `tiny.jpg`: saved, `t("kind_unknown")` in the output
- [x] **[pin]** Parametrized over the poster, `multi`, `og:video` and `<audio>` pages: direct with preview, `-y`, `-y -p`, `-y -P` and `-y -a` give the file names and exit code measured on `main`

`fake_site`:

- [x] **[fix]** Wizard on a carousel of `_image` entries and a video whose format has only `url` and `ext`: `[image]` and `[video]` in the output; Enter saves the video only, and `-y -p` saves the same file
- [x] **[fix]** Wizard on a carousel of url-only entries: all saved
- [x] **[fix]** Wizard on a carousel of only `.jpg` url-only entries: Enter saves all. Fails without the `wizard.py` change
- [x] **[fix]** Wizard on a carousel of known-codec audio with a duration: items tagged audio
- [x] **[pin]** Wizard on `only-images`: `t("no_supported_items")`, exit 0, no command, no files
- [x] **[pin]** Wizard on `fake.test/image`: `t("probe_failed", "unknown")` in the output, exit 1

Unit, `tests/test_probe.py`, one parametrized table:

- [x] **[fix]** `_classify_entry` and `_classify_single` over every row of the rule table, plus url-only entries, a storyboard next to an unknown format, a storyboard next to audio, and audio with a duration
- [x] **[pin]** `_classify_entry({"id": "i", "title": "t", "formats": []}, 1)` is image, unsupported, and with `thumbnails` too. This is the shape `probe-auth` will produce
- [x] **[pin]** `_classify_single({"formats": [], "thumbnails": [...]})` is image, `{}` is unknown
- [x] **[fix]** Wizard flow: a playlist of only `ProbeItem(1, ContentKind.UNKNOWN, True, ...)` isn't refused

No existing test needs to change: the full suite passes with the prototype applied (477 passed, the same as the baseline).

Commit: `fix(probe): classify media without codec info by what yt-dlp would do`, or split by file if the developer prefers, but the rule and `wizard.py:502` belong together.

**SemVer:** patch on its own: no flag, default or on-disk path changes, and no new message. Not breaking: `AGENTS.md`'s triggers are renamed flags, changed defaults and moved state, and the defaults per type stay the same; what changes is which type some pages are detected as. The visible consequence belongs in the release notes: in the wizard, `.m4a` and `.wav` pages, and `.ogg` pages where yt-dlp sets `vcodec` `'none'`, now convert to the audio format in `~/Music`, where they used to land raw in `~/Videos`. A direct link to a video served as `audio/*` is converted too, and loses its video. The next release is already 0.3.0, so this moves nothing. No version bump in this item.

## Live check

Needed for one thing: only a real post can confirm the Instagram shapes. Generic pages need no live run, since the tests use the real extractor.

With `DM_LIVE_IG_CAROUSEL` set to a public post mixing images and videos, and no cookies:

```bash
printf '\n1\n\n\n\n\n\n' | .venv/bin/download-media -i -o tmp/probe-no-codec "$DM_LIVE_IG_CAROUSEL"
.venv/bin/download-media -y -p -o tmp/probe-no-codec-y "$DM_LIVE_IG_CAROUSEL"
```

The answers are URL, cookies (`1`, none), selection, type, quality, container, folder. On disk, the number of files in the first folder equals the `[video]` rows printed, and matches the second folder. Every image shows as `[image]` and none is saved. Run it alone: Instagram rate-limits by IP.

**Result:** run once, during QA at `875030f`, without cookies: 20 items, 4 video, 16 image, the same 4 files through the wizard and `-y -p`, no image saved. Not repeated at validation, to stay clear of the rate limit: `src/` has no changes between `875030f` and the merge.

## Changes after approval

The goal, scope and acceptance criteria didn't change. Results below come from the real console script on a loopback server, with yt-dlp 2026.10.26. "Pre-fix" is `src/` at `2b9eb5c`.

### The rule (`bec654d`)

The approved rule read every extension in `MEDIA_EXTENSIONS.audio` as audio when `vcodec` was unknown. Testing found that `asf`, `m4b`, `ogg`, `ogx` and `wma` can hold a video that yt-dlp keeps. Read as audio, the wizard skipped the type step, converted to `.mp3`, deleted the video and said `Done.`, where `-y` and the pre-fix wizard keep the file. So these five now need `vcodec` `'none'` to be audio. The two mistakes aren't equal: a wrong "audio" loses the video, and a wrong "video" only keeps the raw file and brings the type step back.

`.m4a` stays audio: yt-dlp's metadata step passes `-vn` for it (`postprocessor/ffmpeg.py:675`), so its video is lost on every path.

This narrows what the approval said about `.ogg` in D2, in the Cases table and in the release note, all corrected above:

| Source                                         | `vcodec` | Wizard | Saved                                    |
| ---------------------------------------------- | -------- | ------ | ---------------------------------------- |
| `<audio src="x.ogg">`                          | `'none'` | Audio  | `.mp3` in `~/Music`                      |
| Direct `.ogg` served as `audio/*` (`generic.py:877`) | `'none'` | Audio | `.mp3` in `~/Music`                 |
| `<video src="x.ogg">`, holding video           | `None`   | Video  | Raw `.ogg` in `~/Videos`, video kept     |
| `<video src="x.ogg">`, holding only audio      | `None`   | Video  | Raw `.ogg` in `~/Videos`; the type step can still choose audio |

`.ogx` keeps its video but ends in `Download failed`, exit 1, with the file on disk, because ffmpeg has no muxer for that extension. `-y` and the pre-fix wizard do the same.

### Human decisions after QA

1. **Renamed videos stay as they are.** A video under `oga`, `spx`, `mka`, `weba`, `m4r`, `f4a` or `f4b` reads as audio and is converted. Measured with an `.mp4` renamed to each, in `<video src>`: `.mp3` in `~/Music`, video gone, exit 0. Pre-fix it was `Unknown`, with the raw file kept in `~/Videos`, though all but `mka` then ended in `Download failed`, exit 1, because yt-dlp's post-processing failed on that file. `-y` still does.
2. **Video under `audio/*` is accepted for now,** with a roadmap row for a type step. `generic.py:877` sets `vcodec` `'none'` from the `Content-Type`, and that outranks the extension. Measured on a direct Theora `.ogg` and a `.wma` holding video: `.mp3`, video gone, where pre-fix kept the raw file. `<audio src>` on a file holding video is the same case. `-y` still keeps the file. An audio single has no type step, so the wizard can't keep it today. That row wasn't on the plan at validation.

### Not decided: four more extensions

The first decision names seven extensions, and validation found four more with the same change: `aiff`, `alac`, `ape` and `wav`. A renamed video under them went from a raw file in `~/Videos`, exit 1, to `.mp3`, video gone, exit 0. They aren't containers that normally hold video, which may be why the list left them out, but nobody has decided them, so they aren't recorded as accepted.

The other six extensions the rule reads as audio didn't change: `aac`, `flac`, `mp3`, `opus` and `vorbis` were already audio pre-fix, since yt-dlp fills `acodec` from the extension, and `.m4a` loses its video on every path.

### Removed test

`test_extensions_the_rule_relies_on_are_in_yt_dlp` was added in `d6b83a4` and dropped in `0343274`. It asserted yt-dlp's extension table without running project code, and its `ogg` row no longer matched the rule. It isn't the parametrized table under Acceptance criteria, which stays, and no criterion depended on it. The classification tests still catch a change in yt-dlp's table: taking `m4a`, `mp3` or `jpg` out of it, or adding `mp4` to the audio list, failed 5, 4, 8 and 12 tests in `tests/test_probe.py`.

## Open decisions

Resolved on approval: the human chose the recommended option in D1 (A), D2 (include), D3 (A) and D4 (ship, own row). Later decisions, and the one still open, are under "Changes after approval".

### D1: an entry that really is unknown

| Option                          | Effect                                                                                                                  |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **A. Follow yt-dlp (recommended)** | Unknown `vcodec` is video; the extension can say audio, or "unknown" for `jpg`/`png`/`webp`. Always offered            |
| B. Everything without codecs is unknown | Honest but useless: every `<video>` says `Unknown`, keeps the extra confirmation, and Enter on a mixed carousel skips no-codec videos, silently losing them |
| C. All of it is video           | No extension rule, but a direct `.jpg` says `Video`, Enter includes it among "videos", and `{"url": "x.mp3"}` flips from audio to video |
| D. All of it is image           | Today's bug, plus it would break direct `.jpg` downloads                                                                 |

**Recommendation: A.** Failing towards the attempt holds up against the wizard flow for a real image. An image with a URL downloads, so attempting it is right. An image with nothing to fetch never gets this far: as `None` in a carousel it stays `[image]`, and alone it fails the probe. The worst case is a label: yt-dlp's table only lists `jpg`, `png` and `webp`, so a direct `.gif` or `.jpeg` says `Video`. Both still save, and in the wizard the `.gif` was measured doing so.

### D2: F4 to F6 ride along

The row only names videos read as images. F4 (video saved as mp3), F5 (audio) and F6 (labels) fall out of the same rule.

**Recommendation: include them.** Splitting means writing a worse rule on purpose, and F4 is the worst of the lot. The cost is one behavior change to accept: `.m4a`, `.ogg` and `.wav` pages go through the wizard as audio, so they become `.mp3` in `~/Music`, as `.mp3` pages already do. For `.ogg`, this now holds only when yt-dlp sets `vcodec` `'none'`: see "Changes after approval". The alternative is fixing only `vcodec` `None` here, and opening a row for the rest.

### D3: quality and container steps when they can't have an effect

| Option                                | Effect                                                                                       |
| ------------------------------------- | -------------------------------------------------------------------------------------------- |
| **A. Leave them (recommended)**       | Same steps as any video. Harmless, but the summary can say `mp4` while the disk says `.webm`  |
| B. Skip them when they can't apply    | `Probe` has to carry the heights and format count, `-i` preselection needs rules, more scope  |

**Recommendation: A,** with a new row for the planner. The real gap is wider than this item: the default promises a file that plays everywhere, and any single-format `.webm` breaks that under `-y` too. Fixing it means deciding on a remux or recode, which is a separate decision.

### D4: the relative `og:image` traceback

An `Audio` page with a relative `og:image` moves from a false refusal to a traceback, exit 1 (`audio-m4a-og.html`). Same crash as `-y -a` today, and now reachable by pressing Enter. Video pages aren't affected: only `-a` writes thumbnails. It's a loud failure, not a false success, and it saves nothing either way.

**Recommendation: ship it, and give it its own row** right after this one. A proper fix belongs in `_download_one` or the thumbnail step, not the classifier. The alternative is to widen this item to catch it first.
