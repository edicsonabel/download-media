# 28 — Never fail an audio download over its cover image

Status: draft
Item: `fix/audio-thumbnail`

> **For the human's review:** every decision below was taken by the architect under the human's delegation, on the option that loses no data and is easiest to undo. D1 changes shipped behaviour on purpose: an audio file saved without its cover now ends in `Done.`, exit 0, with a warning, where it ended in a traceback or `Download failed`, exit 1. D5 drops the cover request for `-f wav`, which can't hold one.

## Problem

With `-a`, anything that goes wrong with the cover image fails the whole download, though the cover is cosmetic and the audio is what the user asked for. The worst case is a page whose `og:image` is a relative URL: the run ends in a Python traceback, exit 1, and nothing is saved.

Reproduced on `main` at `543ee86`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. The real console script (`.venv/bin/download-media`) ran under `env -i` against a scratch `http.server` on `127.0.0.1:18928`, with `HOME` and `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` in a scratch folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, `LANG=en_US.UTF-8`, an absolute `-o`, no clipboard and no cookies. Pages hold `<audio>`/`<video>` tags and an `og:image`; the media are made with ffmpeg.

```bash
# rel.html: <audio src="tiny.m4a"> and <meta property="og:image" content="tiny.jpg">
download-media -y -a -o "$OUT" http://127.0.0.1:18928/rel.html
#   File ".../yt_dlp/YoutubeDL.py", line 3392, in process_info
#     thumb_files = self._write_thumbnails(
#   ...
# yt_dlp.networking.exceptions.NoSupportingHandlers: Unable to handle request: Unsupported url scheme: "" (urllib, curl_cffi)
# exit 1. On disk: nothing. No history row.
```

- **What happened:** a traceback, exit 1, no file. The wizard reaches the same traceback when the user presses Enter at every step.
- **What should happen:** the audio is saved and converted as usual, the run says it has no cover, `Done.`, exit 0. Any thumbnail the tool wrote is gone after the run, whether the download worked or not.

## Findings

"Real" means the console script as above. "Prototype" means the design below applied to a scratch copy of `src/download_media` (67 changed lines in `downloader.py`, plus `cli.py` and `i18n.py`) and run the same way; `src/` itself wasn't touched.

### Why the traceback

`_write_thumbnails` (`YoutubeDL.py:4501-4553`) only catches `network_exceptions` (`HTTPError`, `TransportError`, `networking/exceptions.py:103`) around `urlopen`. A URL with no scheme makes the request director raise `NoSupportingHandlers` (`networking/common.py:130`), a `RequestError` that isn't a network error, so it leaves `process_info`. Without `ignoreerrors`, `_handle_extraction_exceptions` re-raises anything that isn't an `ExtractorError` (`YoutubeDL.py:1747-1751`). It isn't a `DownloadError`, so `_download_one` (`cli.py:276-284`) doesn't catch it. Under `-p`, `ignoreerrors` reports it instead, and the whole URL fails.

The generic extractor stores `og:image` without resolving it against the page (`extractor/generic.py:974`), and `_sanitize_thumbnails` doesn't resolve it either (`YoutubeDL.py:2757`). Thumbnails are written before the media is downloaded (`YoutubeDL.py:3392`, then the download), so nothing is saved.

### Every way the cover fails an audio run today (real, `main`)

| Command (`-y -o "$OUT"` on each)                                              | Output                                                                             | On disk                                                     | Exit |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- | ---- |
| `-a` on `rel.html`                                                            | Traceback, `NoSupportingHandlers`                                                  | nothing                                                     | 1    |
| `-a -p` on `rel.html`                                                         | `ERROR: Unable to handle request …`, `Download failed`                             | nothing                                                     | 1    |
| `-a -P` on `tworel.html` (two `<audio>`, relative `og:image`)                 | Traceback, from the **playlist** thumbnail (`YoutubeDL.py:2119`)                   | nothing                                                     | 1    |
| `-a -p` on `tworel.html`                                                      | `[info] Downloading playlist thumbnail 0 ...`, `ERROR: …`, `Download failed`        | nothing                                                     | 1    |
| `-a` on `badthumb.html` (absolute `og:image` to a text file served as `.jpg`) | `[EmbedThumbnail] …`, `ERROR: Postprocessing: Conversion failed!`, `Download failed` | `badthumb (1).mp3`, **`badthumb (1).jpg`**, **`badthumb (1).temp.mp3`** | 1    |
| `-a -f wav` on `abs.html` (a valid absolute `og:image`)                       | `ERROR: Postprocessing: Supported filetypes for thumbnail embedding are: mp3, mkv/mka, ogg/opus/flac, m4a/mp4/m4v/mov`, `Download failed` | `abs (1).wav`, **`abs (1).jpg`** | 1    |
| `-a -f opus` on `abs.html`                                                    | `ERROR: Postprocessing: module mutagen was not found …`, `Download failed`         | `abs (1).opus`, **`abs (1).jpg`**                           | 1    |
| `-a -f opus` on a page whose `og:image` is a `.gif`                           | same                                                                               | the `.opus`, **the `.gif`, and the `.png`** yt-dlp converted it to | 1 |
| `-a -f opus` on a page whose `og:image` is WebP served as `.jpg`              | same                                                                               | the `.opus`, **the `.webp`** (renamed by yt-dlp) **and the `.png`** | 1 |
| `-a` (mp3) on `fakewebp.html`: the `og:image` is the 12 bytes `RIFF\0\0\0\0WEBP`, served as `.jpg` | `Correcting thumbnail … extension to webp`, `Converting thumbnail … to png`, `ERROR: Postprocessing: Conversion failed!`, `Download failed` | `fakewebp (1).mp3`, **`fakewebp (1).webp`** | 1 |
| Wizard, Enter at every step, on `rel.html`                                    | Traceback                                                                          | nothing                                                     | 1    |
| Wizard, Enter at every step, on `badthumb.html`                               | `Download failed`                                                                  | `Music/`: the `.mp3`, the `.jpg`, the `.temp.mp3`           | 1    |
| `-a -f m4a` on `abs.html`                                                     | `[EmbedThumbnail] Neither mutagen nor AtomicParsley was found. Falling back to ffmpeg`, `Done.` | `abs (1).m4a` with its cover        | 0    |
| `-a` (mp3) on `abs.html`                                                      | `Done.`                                                                            | `abs (1).mp3` with its cover                                | 0    |

- **wav can never hold a cover** through yt-dlp's step (`postprocessor/embedthumbnail.py:222`). So `-a -f wav` fails on **every** source that has a thumbnail, which on YouTube is every video.
- **opus needs `mutagen`** for the cover (`embedthumbnail.py:199-200`). It isn't a dependency of this project, isn't in the dev venv, and isn't installed with Arch's `yt-dlp` (it's an optional dependency there). So `-a -f opus` fails on every source with a thumbnail on such a machine. m4a falls back to ffmpeg and works; mp3 uses ffmpeg.
- In the failing rows the metadata step never runs, because it comes after the cover step. The `.mp3` of the `badthumb` row is untagged, so a rerun can't recognise it and saves a copy (spec 29, D5).

### Thumbnails left behind on a failure that isn't the cover's (real, `main`)

| Command                                                                   | Output                                                              | On disk                                   | Exit |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------------- | ---- |
| `-y -a` on `gone.html` (`<audio src="missing.m4a">`, absolute `og:image`) | `ERROR: unable to download video data: HTTP Error 404`, `Download failed` | **`gone (1).jpg`**                  | 1    |
| `-y -a` on `noaudio.html` (a video-only mp4, absolute `og:image`)         | `ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe`, `Download failed` | **`noaudio (1).jpg`**, `noaudio (1).mp4` | 1 |
| `-y -a` on `twoabs.html` (two `<audio>`, absolute `og:image`), a **success** | `Done.`                                                          | both `.mp3`, and **`twoabs.jpg`**         | 0    |

- **The playlist thumbnail.** On a multi-entry result, yt-dlp writes the list's own thumbnail, named after the list (`YoutubeDL.py:2107-2119`). Nothing embeds or deletes it, so every `-a` run on a list with a thumbnail leaves `<list title>.jpg`. This happens in single mode too, since the generic extractor returns a list. yt-dlp's own command line avoids it: `--embed-thumbnail` without `--write-thumbnail` sets `outtmpl['pl_thumbnail'] = ''` (`yt_dlp/__init__.py:707-714`). By the same reading of the code, a YouTube playlist under `-p -a` leaves one too. That wasn't run.
- The leftover `noaudio (1).mp4` is the downloaded source that audio extraction couldn't read. It isn't a thumbnail (see Out).

### A same-name image in the output folder (real, `main`)

yt-dlp **deletes** a file at the thumbnail's path before writing the thumbnail: `Deleting existing file …/badthumb (1).jpg`. `existing_file` keeps an existing file only when `overwrites` is false (`YoutubeDL.py:3321-3329`). The tool never sets `overwrites`, and yt-dlp's default overwrites everything but videos (`YoutubeDL.py:296-298`). So a user's own `<title>.jpg` is gone before any code of this project runs: on a success it's replaced and then deleted by the embed step, and on the `badthumb` page it's replaced by the junk body. This is yt-dlp's documented default, and it's the same today. It does decide one thing for the design: **every path `_write_thumbnails` returns was written by this run.**

### The prototype (real console script, scratch copy of `src`)

| Command                                                   | Result                                                                                          |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `-y -a` on `rel.html`; also `-p`; also the wizard, Enter at every step | `[EmbedThumbnail] There are no thumbnails on disk`, `rel (1).mp3: saved without a cover image.`, `Done.`, exit 0, one `.mp3`, one history row |
| `-y -a` on `tworel.html` and on `twoabs.html`            | `Done.`, exit 0, both `.mp3`, **no `.jpg`**, no warning (the list's thumbnail is never fetched) |
| `-y -a` on `badthumb.html`                                | The warning, `Done.`, exit 0, one `.mp3`, no `.jpg`, no `.temp.mp3`. Run twice: still one file, since the metadata step now runs |
| `-y -a -f opus` on `abs.html`, on the `.gif` page, on the WebP page | The warning, `Done.`, exit 0, only the `.opus` each time                             |
| `-y -a` (mp3) on `fakewebp.html`                          | The warning, `Done.`, exit 0, only the `.mp3`: the `.webp` rename is removed                    |
| `-y -a -f wav` on `abs.html`                              | `Done.`, exit 0, only the `.wav`, no warning                                                    |
| `-y -a -f m4a`, and mp3, on `abs.html`                    | Unchanged: `Done.`, the cover embedded, no warning                                              |
| `-y -a` on `gone.html` and on `noaudio.html`              | Unchanged `Download failed`, exit 1, **no `.jpg`**. `noaudio (1).mp4` stays (Out)               |
| A user's own `abs (1).png` in the folder, then `-y -a -f opus` on `abs.html` | The user's `.png` is intact (byte-compared), the warning, exit 0               |

A narrow pytest run against the prototype (`tests/test_e2e.py` and `tests/test_downloader.py`, `-k "thumbnail or relative_og or audio or opus or wav"`) gave 146 passed, 2 xfailed (row 25) and 3 failed. The 3 failures are the expected flips: the strict xfail `test_wizard_on_audio_with_a_relative_og_image_ends_without_a_traceback` now passes, and both rows of `test_audio_item_whose_thumbnail_cannot_be_embedded` (spec 29's D6 edge). `tests/test_downloader.py` and `tests/test_i18n.py` passed in full (40). The rest of the suite wasn't run against the prototype.

## Goal

A cover image never decides whether an audio download worked. When the audio is saved without its cover, the user gets `Done.`, exit 0, and one warning naming the file. After any `-a` run, no thumbnail the tool wrote is left in the folder.

## Scope

### In

- **The fetch:** a non-network request error while fetching a thumbnail drops the cover, not the download.
- **The embed:** a failed cover embed keeps the audio, which counts as saved, gets its metadata step and a warning.
- **`-f wav`:** no thumbnail is requested.
- **The list's own thumbnail:** never fetched or written.
- **Cleanup:** the thumbnails this run wrote for an item are removed when the item fails, and when its embed fails. The same goes for the files the embed step makes from them (a `.webp` rename, a `.png` conversion) and for the step's own `.temp.<ext>`. Spec 29's rule applies: never a file the run didn't write.
- **One new message,** `no_cover`, in both blocks.
- **`-p -a`:** an item whose only problem is its cover counts as saved. This reverses spec 29's D6 edge for the cover step only.
- **Tests:** loopback pages and `fake_site` shapes, with the real yt-dlp and ffmpeg.

### Out

- **Other exceptions that aren't a `DownloadError` and reach `_download_one`.** One is measured here: `-y -a -o /proc/dm-test <url>` ends in a `FileNotFoundError` traceback from `output_dir.mkdir` (`downloader.py:358`). Another is `CookieLoadError`, which is row 26. A catch-all needs its own design: which message, which exit code, and whether a bug still shows a traceback to report. See D9. **Proposed as a new row.**
- **Adding `mutagen` as a runtime dependency,** so that opus gets covers. New dependencies need the human's approval (`constraints.md`, Technical 2). Until then, opus says it was saved without a cover. See D10. **Proposed as a new row.**
- **A user's own `<title>.jpg` deleted by yt-dlp** (Findings). Protecting it would mean `overwrites: False`, which also changes how subtitles and other metadata files are rewritten (spec 21). Today's behaviour, not a regression. **Noted for the planner.**
- **The downloaded source left after a failed audio extraction** (`noaudio (1).mp4`). It's media, not a thumbnail. Whether to remove it is spec 29's open "non-media body" question with a different shape. **Noted for the planner.**
- **Resolving a relative `og:image`** so the cover is saved after all. See D3. The real fix belongs in yt-dlp's generic extractor (`generic.py:974`); an upstream report is worth filing.
- **Video mode.** It never writes thumbnails (`writethumbnail` is only set for `-a`, `downloader.py:123`). Unchanged.

## Files

| Action | File                               |
| ------ | ---------------------------------- |
| modify | `src/download_media/downloader.py` |
| modify | `src/download_media/cli.py`        |
| modify | `src/download_media/i18n.py`       |
| modify | `tests/conftest.py`                |
| modify | `tests/test_e2e.py`                |
| modify | `tests/test_downloader.py`         |
| modify | `tests/test_i18n.py` (if the developer pins the new key there, as spec 29 did) |

No new dependency, flag, default or on-disk state. `wizard.py`, `probe.py`, `equivalent.py`, `history.py`, `config.py` and `README.md` don't change: neither the README nor `--help` mentions covers. Possible collisions: row 26 (`cli.py` and the cookie fallback, whose retry this item stops triggering for cover errors), row 30 (`build_ydl_opts`), row 36 (`downloader.py`, `ignoreerrors`), and row 34 (`tests/conftest.py` fixtures).

## Design

Written for the recommended option of each decision. The prototype produced every "after" result above.

### `downloader.py`

**1. `build_ydl_opts`, audio branch** (D5, D6, D4):

- `"writethumbnail": opts.audio_format != "wav"`.
- `"allow_playlist_files": False`. This is a documented `YoutubeDL` parameter (`YoutubeDL.py:316`, the command line's `--no-write-playlist-metafiles`). When it's false, `__process_playlist` skips the list's thumbnail, description and info JSON (`:2107-2119`). The tool writes neither of the last two, so only the thumbnail changes. It's a public parameter, where yt-dlp's own `outtmpl['pl_thumbnail'] = ''` trick would turn `outtmpl` into a dict and change four assertions in `tests/test_downloader.py`.
- Drop `{"key": "EmbedThumbnail"}` from `postprocessors`: `download()` adds the wrapped step (3). `ExtractAudio` stays.

**2. `_Ydl` tracks each item's thumbnails** (D2, D8):

```python
item_thumbs: list[str]   # thumbnail files this run wrote for the current item
item_no_cover = False    # the current item lost its cover

def process_info(self, info_dict):
    self.item_errors = len(self.errors)
    self.item_thumbs, self.item_no_cover = [], False
    try:
        result = super().process_info(info_dict)
    except BaseException:          # single mode: report_error raises DownloadError here
        self.drop_thumbs()
        raise
    if self.item_failed():         # -p: ignoreerrors reported it and carried on
        self.drop_thumbs()
    return result

def _write_thumbnails(self, label, info_dict, filename, thumb_filename_base=None):
    try:
        written = super()._write_thumbnails(label, info_dict, filename, thumb_filename_base)
    except RequestError:           # yt-dlp only catches network errors here
        self.item_no_cover = True
        return []
    self.item_thumbs += [path for path, _ in written or []]
    return written                 # None still means "couldn't create the folder"
```

- **Why `_write_thumbnails`:** it's the only place to catch the error without losing the download. The exception leaves `process_info` before the media is fetched, and no public hook runs between the two. It's a private method, but it has the same name, signature and structure at the `pyproject.toml` floor (2024.10.07: `YoutubeDL.py:4373`, read on GitHub, not run) and in 2026.10.26. The flipped e2e test fails loudly if yt-dlp ever renames it, because the traceback comes back.
- **Which exceptions:** `yt_dlp.networking.exceptions.RequestError` only. Network errors (`HTTPError`, `TransportError` and their subclasses) never get here, because yt-dlp already turns them into "no cover" (`:4544-4549`). What's left is `NoSupportingHandlers` (no scheme, an unknown scheme) and the plain `RequestError`s that `urlopen` raises (a `file://` URL, a missing proxy or impersonation handler, `:4301-4330`). An `OSError` from writing the file isn't caught: the audio couldn't be written either, so the run should fail.
- **Why every returned path counts as this run's:** with `overwrites` unset, `existing_file` deletes any file already at the path and returns `None`, so the "already present" branch never runs (Findings, A same-name image). The developer keeps a comment saying so.
- `ret` is `None` only when `_ensure_dir_exists` fails; that is returned unchanged.
- `drop_thumbs()` unlinks each path in `item_thumbs` (`missing_ok=True`) and empties the list. On a success the embed step has already deleted them, so it does nothing.

**3. `_EmbedCover(EmbedThumbnailPP)`**, added in `download()` with `when="post_process"` **before** `_TagSource` when `opts.audio_only and opts.audio_format != "wav"`. That keeps today's order: `ExtractAudio`, cover, metadata (`add_post_processor` appends after the params' steps, `YoutubeDL.py:946`). `pp_key` returns `"EmbedThumbnail"`, which keeps the `[EmbedThumbnail]` tag and its `postprocessor_args` key, as `_TagSource` and `_EmbedSubtitles` do.

```python
def run(self, info):
    ydl = self._downloader
    temp = Path(prepend_extension(info["filepath"], "temp"))
    temp_was_there = temp.exists()
    pngs = {Path(replace_extension(p, "png")) for p in ydl.item_thumbs}
    pngs_there = {p for p in pngs if p.exists()}
    try:
        return super().run(info)
    except PostProcessingError:
        if not temp_was_there:
            temp.unlink(missing_ok=True)
        for png in pngs - pngs_there:      # the step's conversion of our thumbnail
            png.unlink(missing_ok=True)
        # fixup_webp may have renamed ours to .webp and recorded the new path.
        ydl.item_thumbs += [t["filepath"] for t in info.get("thumbnails") or [] if t.get("filepath")]
        ydl.drop_thumbs()
        ydl.item_no_cover = True
        return [], info
```

- **What the step can raise:** `EmbedThumbnailPPError` (unsupported container, missing `mutagen`) and `FFmpegPostProcessorError` (`Conversion failed!`, a failed `.png` conversion). Both are `PostProcessingError`s, and nothing else is caught. A `mutagen` exception from a broken image in an opus file, on a machine that has `mutagen`, isn't caught. That wasn't reproduced; it's listed under Remaining risks.
- **What the step can create** (`ffmpeg.py:1075-1101`, `embedthumbnail.py`): the `.temp.<ext>` (ffmpeg), a rename of the thumbnail to `.webp` (`fixup_webp`, recorded in `info["thumbnails"][i]["filepath"]`), and a `.png` conversion when the thumbnail isn't jpg or png. The `.temp` and `.png` files are deleted only if they weren't there before the step ran (spec 29's rule). The `.webp` rename is the thumbnail this run wrote, moved.
- On success, yt-dlp's own `_delete_downloaded_files` removes the thumbnail and its conversion as today (`_already_have_thumbnail` stays `False`).
- **The metadata step still runs** after a failed cover, so the source-URL tag is written and a rerun recognises the file.

**4. `download()`**: the post hook appends `Path(path).name` to a `no_cover` list when the item counts as saved and `ydl.item_no_cover` is set. So the warning names the final file (the `.mp3`, not the `.m4a`), and only items that were saved get it. `DownloadResult` gains `no_cover: tuple[str, ...] = ()`.

### `cli.py`

In `_download_one`, next to the `untagged` loop: `for name in result.no_cover: warn(t("no_cover", name))`. Nothing else changes. `_download_with_cookie_fallback` no longer sees an exception for a cover problem, so it no longer retries without cookies.

### `i18n.py`

| Key        | en                                  | es                                        |
| ---------- | ----------------------------------- | ----------------------------------------- |
| `no_cover` | `{}: saved without a cover image.`  | `{}: guardado sin imagen de portada.`     |

One message for every cause (D7). yt-dlp's own reason is hidden by `no_warnings` and is English-only, so it isn't shown.

### What the user sees

```
[info] Downloading video thumbnail 0 ...
[download] Destination: …/rel (1).m4a
[ExtractAudio] Destination: …/rel (1).mp3
[EmbedThumbnail] There are no thumbnails on disk
[Metadata] Adding metadata to "…/rel (1).mp3"
rel (1).mp3: saved without a cover image.
Done.

Equivalent command to repeat this download:
  download-media -y -a -o … http://…/rel.html
```

### yt-dlp behaviour relied on

Confirmed in the 2026.10.26 source and, for the floor, in the 2024.10.07 files read on GitHub (`YoutubeDL.py`, `postprocessor/embedthumbnail.py`, `networking/exceptions.py`, `extractor/generic.py`). The `_write_thumbnails` bodies differ only in `t.get('ext')` and `encodeFilename`.

- `_write_thumbnails` catches only `network_exceptions`; it's called from `process_info` before the download (`:3392`) and from `__process_playlist` when `allow_playlist_files` is true (`:2107-2119`). Floor: `:4373`, `:3276`, `:1999`.
- `existing_file` deletes and returns `None` unless `overwrites` is false (`:3321-3329`). Same at the floor (`:3205`).
- `NoSupportingHandlers` and `RequestError` derive from `YoutubeDLError`, not `DownloadError` (`networking/exceptions.py:11-30`). Same at the floor.
- `EmbedThumbnailPP.run` returns early when no thumbnail has a `filepath` (`There are no thumbnails on disk`). It raises `EmbedThumbnailPPError` for wav and for opus without `mutagen`, and deletes the thumbnail only on success. `_already_have_thumbnail` exists at the floor.
- `process_info` without `ignoreerrors`: `report_error` raises `DownloadError` from inside it. With `ignoreerrors`, `run_pp` reports a failed step and carries on to the post hooks (spec 29).

## Cases

| Case | Expected after the fix |
| ---- | ---------------------- |
| `-y -a`, direct preview, `-P`: relative `og:image` | One audio file, converted and tagged; `no_cover` warning; `Done.`; exit 0; one history row; no image file |
| Same: a thumbnail that can't be embedded (non-image body, opus without `mutagen`, a `.gif` or WebP that converts and then fails) | As above; no `.jpg`, `.webp`, `.png` or `.temp.<ext>` left by the run |
| `-a -f wav`, any source | No thumbnail requested; no warning; `Done.`; exit 0 |
| `-a` mp3 or m4a with a working thumbnail | Unchanged: the cover embedded, no warning, no image file |
| `-a`, media 404, or a source with no audio | Unchanged `Download failed`, exit 1, no history row; **no thumbnail left**. The source left by a failed extraction stays (Out) |
| Wizard and `-i`, the same sources | As `-y`, after the walk. `_download_one` is shared; the strict xfail flips |
| `-p -a`, a list with one cover failure | Every item saved, no `N of M` line, one warning for that item, exit 0. Today: `1 of 2 … 1 failed` (spec 29 D6 edge) |
| `-p -a`, one item 404 with a thumbnail | `1 of 2 … 1 failed`, `Done.`, exit 0, no thumbnail of the failed item left |
| Any list under `-a` (single mode or `-p`) whose result has its own thumbnail | No `<list title>.jpg`; a relative one no longer crashes |
| Several URLs, the first with a relative `og:image` | Every URL downloaded, exit 0. Today the traceback stops the run at the first |
| `--cookies` with a cover failure | No cookie retry, one file. Today: a retry |
| Without cookies | As the rows above |
| Rerun of a cover failure | One file: the metadata step ran, so `_AvoidNameClash` recognises it. Today a `badthumb` rerun saves a copy |
| A user's own `<title>.png` or `.temp.<ext>` next to a failing embed | Left alone. A user's own `<title>.jpg` is yt-dlp's (Out) |
| Video mode, `-s`, `-l` | Unchanged: no thumbnail is written in video mode, and `-l` downloads nothing |
| `es` and `en` | The warning in each language |

Ctrl-C in the middle of an item also runs `drop_thumbs` (the `BaseException` branch). That's harmless, because a rerun fetches the thumbnail again.

## Acceptance criteria

**[fix]** criteria need a test that fails without the fix, which the developer shows by reverting it (`constraints.md`, Verification 5). **[pin]** criteria pass before and after. Tests that can reach `~/Music` use the `home` fixture. "No image left" means no file ending in `.jpg`, `.webp`, `.png` or `.gif` in the output folder, and "no leftovers" adds `.temp.` and `.part` names.

Fixtures, in `tests/conftest.py`, without new encoders (row 34):

- **Generic pages** (written after the server starts, like `audio`): `<audio src="missing.m4a">` with the absolute `og:image`; `<video src="{JUNK}">` with the absolute `og:image` (audio extraction fails); `<audio src="tiny.m4a">` with an `og:image` on `typed/image/jpeg/en.vtt/t.jpg` (a text body); the same with `tiny.gif`; the same with a WebP-signature body (the 12 bytes `RIFF\0\0\0\0WEBP` are enough for `imghdr`, so `fixup_webp` renames it and the `.png` conversion fails), served as `typed/image/jpeg/<file>/t.jpg`; `audios-og` and `audios-relative-og`, two `<audio>` tags with an absolute and a relative `og:image`. `audio-relative-og` and `audio` already exist.
- **`fake_site` shapes:** a single item whose thumbnail is the text body; a two-item carousel whose first item has a thumbnail and media at `/status/404`.
- **No `mutagen`:** tests that need the embed to fail on opus set `yt_dlp.postprocessor.embedthumbnail.mutagen` to `None` with `monkeypatch`, so the result doesn't depend on whether `mutagen` is installed.

Real yt-dlp and ffmpeg, loopback:

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] **[fix]** The strict xfail `test_wizard_on_audio_with_a_relative_og_image_ends_without_a_traceback` loses its marker and becomes a success test (it may be renamed). The same answers as today (`generic(page, "", "", "", "")`) on `audio-relative-og.html`: exit 0, `t("done")` and `t("no_cover", saved)` in the output, exactly one `.mp3` in `~/Music`, no image left
- [ ] **[fix]** `-y -a`, and `-y -a -p`, on `audio-relative-og.html`: exit 0, one `.mp3`, `t("no_cover", …)` once, `t("failed", url)` not, one history row
- [ ] **[fix]** `-y -a` on the text-body thumbnail page: exit 0, one `.mp3`, the warning, no leftovers, and `_source_url` of the `.mp3` equals the URL (the metadata step ran)
- [ ] **[fix]** `-y -a -f wav` on `audio.html`: exit 0, exactly one `.wav`, no image left, `no_cover` not said
- [ ] **[fix]** `-y -a -f opus` without `mutagen`, parametrized over `audio.html` (jpg), the `.gif` page and the WebP-signature page: exit 0, exactly one `.opus`, the warning, no image left, no leftovers
- [ ] **[fix]** `-y -a` (mp3, `mutagen` irrelevant) on the WebP-signature page: exit 0, exactly one `.mp3`, the warning, no `.webp` or `.png` left. This is the `fixup_webp` rename path
- [ ] **[pin]** With a user's own `<name>.png` and `<name>.temp.opus` in the output folder (`<name>` being the saved file's stem), `-y -a -f opus` without `mutagen` on `audio.html`: both files byte-identical afterwards
- [ ] **[fix]** `test_audio_item_whose_thumbnail_cannot_be_embedded` flips: with `-p`, exit 0, two files, `partial_result` not said, `t("no_cover", …)` exactly once; without `-p`, exit 0, `t("done")`, `t("failed", …)` not said
- [ ] **[fix]** `-y -a --cookies brave` on the single `fake_site` item with the text-body thumbnail: exit 0, one file, `t("cookies_retry", "brave")` not said, `fake_site == [("brave",)]`
- [ ] **[fix]** `-y -a` on the 404 page: exit 1, `t("failed", url)`, the output folder empty
- [ ] **[fix]** `-y -a` on the `<video src="{JUNK}">` page: exit 1, `t("failed", url)`, no image left (the junk body itself may stay, Out)
- [ ] **[fix]** `-y -a -p` on the `fake_site` carousel with a 404 item: `t("partial_result", 1, 2, 0, 1)`, exit 0, one audio file, no image left
- [ ] **[fix]** `-y -a`, and `-y -a -p`, on `audios-og` and on `audios-relative-og`: exit 0, two `.mp3`, no image left, `no_cover` not said
- [ ] **[fix]** `-y -a <audio-relative-og> <audio>` (two URLs): exit 0, both saved, two history rows
- [ ] **[fix]** `-y -a` twice on the text-body thumbnail page, in one output folder: one file after each run, exit 0 both times
- [ ] **[pin]** `-y -a` on `audio.html` (mp3, a working jpg): the `.mp3` holds an attached picture (`ffprobe` shows a `video` stream), no image left, `no_cover` not said. The same with `-f m4a`
- [ ] **[pin]** `-y` (video) on `og-video.html`, which has an `og:image`: no image left beside the video

Unit:

- [ ] `tests/test_downloader.py`: `build_ydl_opts` in audio mode sets `allow_playlist_files` to `False`, lists no `EmbedThumbnail` among its `postprocessors`, and sets `writethumbnail` to `False` for `wav` and `True` for the other three formats. Video mode sets no `writethumbnail`
- [ ] `no_cover` is in both the `es` and the `en` blocks (`test_es_and_en_have_same_keys`)

The pre-existing tests that change are the three flips seen on the prototype: the strict xfail and the two rows of `test_audio_item_whose_thumbnail_cannot_be_embedded`. Any other flip must be explained in the developer's report.

Commits: `fix(downloader): keep audio whose cover can't be fetched or embedded` (steps 2 to 4, the wav and playlist-thumbnail options, `cli.py`, `i18n.py`, with their tests). If the developer prefers, the playlist thumbnail goes alone as `fix(downloader): stop -a writing the list's thumbnail`. Each commit comes with its own tests.

## Live check

Not needed. Thumbnails are fetched by yt-dlp's core, not by a site extractor, and embedded by ffmpeg. Both run for real on loopback, and no cookies are involved. The YouTube-specific parts (WebP thumbnails, playlist thumbnails) are covered by the WebP-signature fixture and the `audios-og` page.

## SemVer

`fix`. Nothing breaking: no flag, default or on-disk path changes. Exit codes go from 1, or a traceback, to 0 when the audio is saved. `-a -f wav` stops requesting a cover it could never embed. One new message, which `AGENTS.md` counts as minor. 0.3.0 is already a minor, so the next version doesn't change.

**Release note (for the planner):** "`-a` no longer fails over the cover image. A page whose image link is relative crashed with a traceback. `-f wav`, and `-f opus` on systems without `python-mutagen`, failed on every video that has a thumbnail. A broken image failed the download with the audio on disk. All of these now save the audio and say it has no cover. Thumbnails are no longer left in the folder after a failed download, and a playlist's own thumbnail is no longer saved next to its tracks."

## Remaining risks

- `_write_thumbnails` is a private yt-dlp method (D2). The flipped xfail pins it.
- A `mutagen` exception from a broken image in an opus file, on a machine that has `mutagen`, isn't a `PostProcessingError` and still fails the item. Not reproduced.
- The `no_cover` warning has one wording for every cause. A user missing `mutagen` isn't told that installing it would bring covers to opus (D7, D10).

## Open decisions

The human delegated these to the roles. Each has a recommendation, chosen to lose no data and to be easy to undo. **D1 and D5 change shipped behaviour**; the human reviews them.

### D1: should a cover problem ever fail an audio download?

| Option | Effect |
| ------ | ------ |
| **A. Never: keep the audio, warn, count it saved (recommended)** | The user gets what they asked for. Matches spec 21 (subtitles) and spec 29 (tags): side steps warn, they don't fail. Reverses spec 29's D6 edge for the cover step only; a failed audio extraction still fails |
| B. Keep failing, minus the traceback | `-f wav` keeps failing on every YouTube video, and the audio stays on disk under `Download failed` |
| C. Fail on embed errors, not on fetch errors | Splits one cosmetic step into two policies with no gain to the user |

**Recommendation: A.** Easy to undo: the two `except` branches.

### D2: how to catch the fetch error

| Option | Effect |
| ------ | ------ |
| **A. Override `_Ydl._write_thumbnails`, catch `RequestError` (recommended)** | Covers every non-network request error (no scheme, unknown scheme, `file://`, missing handler). Also the natural place to record which thumbnails the run wrote. The method is private, but it's unchanged since the floor |
| B. A `video`-stage post-processor that drops thumbnails with no scheme | Public API, but only covers relative URLs; `file://` and missing-handler errors still crash |
| C. `except Exception` in `_download_one` | No traceback, but the audio is still lost: `Download failed` |

**Recommendation: A.**

### D3: resolve a relative `og:image` to get the cover after all?

| Option | Effect |
| ------ | ------ |
| **A. No: drop the cover and warn (recommended)** | Wrapper rule: the bug is in yt-dlp's generic extractor (`generic.py:974`). Worth an upstream report; once fixed there, the cover comes back with no change here |
| B. `urljoin` each thumbnail URL against `webpage_url` before the fetch | Gets the cover, but adds URL handling that belongs in yt-dlp, and still needs A's catch for the other errors |

**Recommendation: A.** B can be added later without undoing anything.

### D4: how to handle a failed embed

| Option | Effect |
| ------ | ------ |
| **A. Wrap `EmbedThumbnailPP`, catch `PostProcessingError`, clean up what the step made (recommended)** | Same pattern as `_EmbedSubtitles` and `_TagSource`. The metadata step still runs |
| B. Leave the step alone | The audio stays under `Download failed`, untagged, with a stray thumbnail and `.temp` file |

**Recommendation: A.**

### D5: `-f wav`

| Option | Effect |
| ------ | ------ |
| **A. Don't request a thumbnail for wav (recommended)** | yt-dlp can't embed one in wav (`embedthumbnail.py:222`), so the request only ever produces a failure, or under D1 a warning on every run. No warning; neither the README nor `--help` promises covers |
| B. Request it and warn every time | A warning about something wav never supports, plus a needless download |

**Recommendation: A.** One expression to undo.

### D6: the list's own thumbnail

| Option | Effect |
| ------ | ------ |
| **A. `allow_playlist_files: False` in audio mode (recommended)** | A documented `YoutubeDL` parameter. No more `<list title>.jpg`, and no fetch, so a relative one can't crash |
| B. `outtmpl = {"default": …, "pl_thumbnail": ""}`, as yt-dlp's command line does | Same effect, but `outtmpl` becomes a dict and four assertions in `tests/test_downloader.py` change |
| C. Leave it | A stray image after every `-a` run on a list with a thumbnail |

**Recommendation: A.**

### D7: what the user is told

| Option | Effect |
| ------ | ------ |
| **A. One warning per file, `{}: saved without a cover image.`, then `Done.`, exit 0 (recommended)** | Says what was skipped, as spec 29's `untagged` does |
| B. Nothing | Hides that the cover was dropped |
| C. One message per cause, or a `mutagen` hint | More keys, and they hang on yt-dlp's error text or internals; better decided with D10 |

**Recommendation: A.** The wording is easy to change.

### D8: what the cleanup may delete

| Option | Effect |
| ------ | ------ |
| **A. Every path `_write_thumbnails` returned for the item, its `.webp` rename, and a `.png` or `.temp.<ext>` only if absent before the step (recommended)** | Exactly what the run wrote. yt-dlp has already replaced any same-name `.jpg` with its own by then (measured) |
| B. Snapshot the folder before the item and delete what's new | Misses that yt-dlp replaced the user's `.jpg` in place, so it would leave the replacement behind, and it lists large folders on every item |

**Recommendation: A.**

### D9: other non-`DownloadError` exceptions in `_download_one`

| Option | Effect |
| ------ | ------ |
| **A. Leave them to their own row (recommended)** | This item stays one change. The new row covers the measured `-o /proc/…` traceback and any other; row 26 covers `CookieLoadError` |
| B. Add `except Exception` here | Hides programming errors behind `Download failed`, and needs decisions on the message, the exit code and the multi-URL flow that aren't about covers |

**Recommendation: A.**

### D10: `mutagen`

| Option | Effect |
| ------ | ------ |
| **A. Not in this item; propose a row for the human (recommended)** | A new runtime dependency needs the human's approval. Until then opus is saved without a cover, with the warning |
| B. Add it here | Outside the delegation |

**Recommendation: A.**
