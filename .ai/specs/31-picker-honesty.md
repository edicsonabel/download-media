# 31 — Picker honesty

Status: draft
Item: `fix/picker-honesty`

> **For the human's review before the release:** the architect took every decision below under the overnight delegation and recommends an option for each. Five change published behaviour, none renames a flag, moves a path or changes a state format. `-i -p` makes the picker's Enter take the whole list, as `-y -p` does, and the printed command keeps `-p` (D1). `-i -P` makes the probe ask yt-dlp for the single video, so a video inside a playlist is no longer saved with the whole list (D2, the one to look at first: today the flag is reversed). A pick made only of audio items defaults the type step to audio, so Enter saves `.mp3` files in `~/Music` instead of raw files in `~/Videos` (D3). Under `-a`, skipped images are counted as images, not as "without downloadable media" (D4), and a list of only images says so instead of `No items are downloadable` (D5). D1 and D3 change what Enter does in the wizard; spec 01 D2 left the same question to the human, so they're flagged the same way.

## Problem

The wizard's picker, its type step and the run's closing count say things that aren't what happens. Reproduced on `main` at `0c76bcd` (`src/` identical to `b85e3db`), yt-dlp 2026.10.26, Python 3.14.9, `LANG=C`, through `cli.main` on loopback only:

| #   | Command                                                                   | What happened                                                                                                                                                                                              | What should happen                                                                                  |
| --- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| R1  | `-i -P https://fake.test/watch` (a video inside a playlist), Enter at every prompt | `Detected: generic / Carousel/Playlist`, picker of 3, `Selected: 1,2,3`, yt-dlp's `Downloading playlist pl - add --no-playlist to download just the video v1`, **3 files** (`01 - Clip [v1].mp4` …), command `--playlist-items 1,2,3` | What `-y -P` does on the same URL: `Downloading just the video v1 because of --no-playlist`, `Clip.mp4` only |
| R2  | `-i -p https://fake.test/carousel` (2 videos, 2 images without thumbnail), Enter | Prompt `Pick items (Enter = everything downloadable, …)`, `Selected: 2,4`, the 2 videos, no count line, command `download-media -y -o <out> --playlist-items 2,4 …`                                              | What `-y -p` does: the same 2 files, `2 of 4 items downloaded (2 without downloadable media, 0 failed).`, command with `-p` |
| R3  | Wizard on `audio-carousel` (two `.m4a` items) and on generic `audios.html` (two `<audio src="*.m4a">`), Enter at every prompt | Both items tagged `[audio]`, then `What do you want to download? … Select [1]` (video), quality, container, folder `~/Videos`: `Videos/01 - Clip [a1].m4a`, `Videos/02 - Clip [a2].m4a`, raw, 7 prompts | Type step at `[2]`: `.mp3` files in `~/Music`, as a single `.m4a` page already does (no type step, `~/Music`) |
| R4  | `-y -a -p https://fake.test/ig-carousel` (2 videos, 2 images); also `-y -a` and `-i -a` with Enter | `i1: an image, skipping`, `i2: an image, skipping`, then `2 of 4 items downloaded (2 without downloadable media, 0 failed).`                                                                                 | The count names them as images that audio mode doesn't save. `-y -p` saves both                      |
| R5  | `-y -a -p` and `-y -a` on a carousel of two Instagram-shaped images        | Both named as images, then `No items are downloadable. Cancelling.`, exit 1, no files                                                                                                                       | Say that audio mode skipped images, which a run without `-a` saves                                   |
| R6  | `grep -rn unsupported_count src tests`                                     | Only its definition, `probe.py:76-78`: nothing reads it                                                                                                                                                      | Gone                                                                                                 |

Rechecked against the row: spec 01's Enter (every downloadable item) already makes `-i -p` save what `-y -p` saves on `ig-carousel` (4 files) and `carousel` (2 files), so "videos only" (spec 20, `:91`) no longer holds. What's left of R2 is the prompt, the missing count line and the printed command. The row's `i18n.py:81` is now `:83` (`partial_result`); `:81` is `item_image_skipped`.

## Goal

Under `-i`, `-p` and `-P` mean at the picker what they mean to `-y`. The type step's default follows what was picked. Under `-a`, the closing count and the nothing-saved line call skipped images images.

## Findings

### How it was run

A scratch test file, outside the repo, ran through `pytest -p tests.conftest` so `fake_site`, `media_url`, `answers` and the autouse `isolated_env` (fresh `HOME`, XDG state and config, dead D-Bus address, empty cwd) applied:

```bash
LANG=C REPRO_OUT=<scratch>/out .venv/bin/python -m pytest -p tests.conftest -p no:cacheprovider \
  --rootdir=$PWD <scratch>/test_repro.py
```

`https://fake.test/watch` was added for the run by wrapping `FakeSiteIE._codec_shape`: it returns three videos when `self._yes_playlist("pl", "v1")` is true and `_video("v1")` otherwise. That's yt-dlp's own switch (`extractor/common.py:4045-4059`), the one YouTube's tab extractor calls on `watch?v=…&list=…` (`extractor/youtube/_tab.py:2328`). At least 27 extractor modules read `noplaylist` through it or directly. The generic extractor doesn't, so generic multi-media pages stay lists under `-P`.

### Why each happens

- **R1.** `probe()` always sets `"noplaylist": False` (`probe.py:352`). The wizard then locks `playlist_mode = "yes"` for any list (`wizard.py:631-632`), so `-P` never reaches yt-dlp. Spec 17's case table (`:228`) says "the wizard's own decision overrides `-P` on any multi-entry URL"; that was written for generic pages and carousels, which stay lists for yt-dlp too.
- **R2.** `-p` sets `playlist_mode = "yes"` and leaves `playlist_items` unset (`cli.py:181-182`), so `step_select` gets no preset (`wizard.py:565`, `:314-321`) and Enter writes the supported indices. `equivalent.build` prints `--playlist-items` for them (`equivalent.py:56-58`). `all` already gives exactly `-y -p` (`playlist_items = ""`, printed as `-p`; spec 18's `partial-broken` row and `test_wizard_all_on_a_generic_page_with_every_kind_repeats_with_all_of_them`).
- **R3.** Only a lone `SINGLE_AUDIO` result sets `audio_only` (`wizard.py:620-623`). A list always gets the type step (`:392-393`), whose default is `"2" if opts.audio_only else "1"` (`:221`). Spec 17 (`:104`) and spec 27 (`:122`) recorded this and left it.
- **R4, R5.** `_skip_items_without_media` puts skipped images and items with nothing to fetch in the same `skipped` list (`downloader.py:755-763`). `partial_result` calls them all "without downloadable media" (`cli.py:388-389`, `i18n.py:83`, `:185`), and an empty run says `no_supported_items` (`cli.py:384-387`).
- **The risk D3 must avoid.** A page of two `<audio>` tags, one Theora and one Vorbis `.ogg`, is all `[audio]`, and one of them holds a video. Today's video default keeps it (pinned by `test_a_page_of_labelled_audio_items_keeps_the_carousel_flow`, `test_e2e.py:3623`). The scratch run read each entry: both `.ogg` entries have `vcodec` `'none'`, `acodec` unset and `extractor_key` `HTML5MediaEmbed`, so `_audio_by_label_only` is true for each, as spec 27 uses it for a lone result. The `.m4a` entries of `audios.html` and `audios-og.html` read false.

### Prototype

The same scratch file, with `probe_module._ProbeYdl` wrapped to set `noplaylist` when the walk's command line had `-P` (no `src/` edit):

| Command                         | Result                                                                                                           |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `-i -P …/watch`, Enter          | `Detected: generic / Video`, no picker, 6 prompts, `Downloading just the video v1 because of --no-playlist`, `Clip.mp4` |
| `-i -P …/ig-carousel`, Enter    | The probe asked with `noplaylist`; still a list (the fake extractor, like Instagram's, ignores it): picker, the 4 numbered files |
| `-i -p …/watch`, Enter          | Probe without `noplaylist`, picker of 3, 3 files                                                                  |

### Side findings, for the planner (not in scope)

- **The wizard without `-P` on a video inside a playlist** offers the whole list with Enter on it (`-i …/watch`: 3 files), while direct mode without `-y` asks with `1) Just this video` as the default and `-y` saves only the video. Same mechanism as R1, but no flag asked for anything; a new row.
- **`-y -a -p mixed-carousel`** leaves `02 - Clip [p1].jpg` (a direct `.jpg` link) on disk after counting it as failed (`ERROR: WARNING: unable to obtain file audio codec with ffprobe`). Row 46's shape.

## Scope

### In

- `-i -p` (and `-i --playlist-items ''`, which the printed command already reads as `-p`): the picker's Enter takes the whole list, as `all` and `-y -p` do, with its own prompt (D1).
- `-i -P`: every probe of the walk asks yt-dlp with `noplaylist` (D2).
- The type step defaults to audio when every picked item is audio that the probe trusts (D3). Items carry spec 27's label-only flag for that.
- Under `-a`: skipped images counted on their own, a closing line that names them (D4), and a nothing-saved line that says why (D5).
- Remove `Probe.unsupported_count`.
- Three messages in both blocks; one sentence in `AGENTS.md`.

### Out

- **The wizard without `-P` on a video inside a playlist** (side finding): its own row.
- **`-i -P` on a carousel or a generic multi-media page.** yt-dlp returns them whole under `noplaylist` too, so the picker stays, with today's Enter and numbered names (spec 17's case holds there). `-y -P` names them without the index; making the picker drop the index under `-P` is a naming change nobody asked for.
- **What Enter selects under `-i -a`.** It still takes images, which audio mode then skips and D4 names. Leaving them out at the picker would lose them silently if the user then chose video at the type step.
- **Direct mode on an all-audio list** (`-y -p audio-carousel`): no probe, so it keeps the flags' defaults (raw files in `~/Videos`). `-a` is the flag for that.
- **Skipping the type step on all-audio picks** (D3 B).
- **The printed command after Enter selects every supported item** without `-p` (`--playlist-items 1,2,3,4`): row 62. D1 only covers `-p` given on the command line.
- **`[no soportado]`**, hardcoded Spanish in both locales: row 02.
- **`-l`**: no closing count, so D4 and D5 don't reach it.

## Files

Line numbers on `main` at `0c76bcd`.

| Action | File                               | What, and where                                                                                                                                                                                                                                  |
| ------ | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| modify | `src/download_media/wizard.py`     | `step_type` (`:215-223`): an audio-default argument. `step_select` (`:279-340`): the `-p` Enter and prompt (`:303-321`). New `_audio_items_only` next to `_images_only` (`:403-411`). `_run_step` (`:425-441`): the `type` and `select` lambdas. `_walk_plan` (`:444-488`): pass the `-p` flag through. `_walk_url` (`:558-645`): read `-p`/`-P` next to `preset` (`:565`), and the three `probe(...)` calls (`:586`, `:590`, `:604`) |
| modify | `src/download_media/probe.py`      | `ProbeItem` (`:38-46`): `may_hold_video`. `Probe.unsupported_count` (`:76-78`): removed. `_classify_entry` (`:246-269`). The `may_hold_video` expression in `_read_result` (`:339-340`) moves into a helper both use. `probe()` (`:344-371`): a `no_playlist` keyword setting `ydl_opts["noplaylist"]` (`:352`) |
| modify | `src/download_media/downloader.py` | `DownloadResult` (`:68-91`): `skipped_images`. `_skip_items_without_media` (`:738-765`): an optional `images` list. `download()`: the filter's lists (`:780-782`) and the result (`:831-842`)                                                         |
| modify | `src/download_media/cli.py`        | `_download_one`: the nothing-saved branch and the count line (`:384-389`)                                                                                                                                                                         |
| modify | `src/download_media/i18n.py`       | `partial_result_images` and `no_audio_items` after `partial_result` (`:83`, `:185`); `ask_selection_default_all` after `ask_selection_default_preset` (`:116`, `:218`)                                                                            |
| modify | `AGENTS.md`                        | the `-i` bullet (`:117-118`)                                                                                                                                                                                                                      |
| modify | `tests/conftest.py`                | `FakeSiteIE._codec_shape` (`:558-745`): pages `watch` and `ig-only-images`                                                                                                                                                                         |
| modify | `tests/test_e2e.py`                | the criteria below; `test_wizard_tags_audio_items_without_codec_info` (`:1813`), `test_audio_mode_skips_the_images_as_images` (`:3855`), `test_wizard_in_audio_mode_on_a_mixed_pick_skips_the_image` (`:3942`) and `test_audio_and_list_modes_skip_a_linked_image_as_an_image` (`:4219`) change their expected lines or files |
| modify | `tests/test_wizard_flow.py`        | the `lambda url, cookies:` probe fakes (`:58`, `:147`, `:158`, `:257`, `:402`, `:423`, `:448`, `:722`) and `probe_for` (`:581`) accept the new keyword; new unit tests                                                                              |
| modify | `tests/test_probe.py`              | `no_playlist`, `ProbeItem.may_hold_video`                                                                                                                                                                                                         |
| modify | `tests/test_downloader.py`         | the `match_filter` helper (`:282-286`) and an `images` case                                                                                                                                                                                       |

No new dependency, flag, default value or on-disk state. `equivalent.py`, `history.py`, `config.py`, `selection.py` and `README.md` don't change.

### Items in flight

All four merge before this item's development, in this order: `fix/cancel-exits`, `fix/subs-failures`, `fix/kind-edges`, `feature/ig-lone-images`. Start from `main` after the last one and re-measure the lines above. What meets:

- **`fix/cancel-exits`** (spec 23, in testing): reshapes `_download_one`'s `except` chain (`cli.py:361-381`), wraps `main` and rewords `no_supported_items` and `image_only_skipped` (`i18n.py:107-108`, `:209-210`). This item edits the lines right after the chain (`:384-389`) and adds keys elsewhere in `i18n.py`. Adjacent, not shared. D5 reuses `no_supported_items` as spec 23 rewords it, and the new `no_audio_items` carries no `Cancelling.` either. Spec 23's `AGENTS.md` edit is in the same "Execution modes" section as this item's `-i` sentence: textual only.
- **`fix/subs-failures`** (spec 36, spec approved): adds two fields to `DownloadResult` and its construction in `download()`, and two loops to the warning block right after the `partial_result` line (`cli.py:388-399`). This item edits that `partial_result` line and adds one field next to theirs. Same hunks, textual: keep both.
- **`fix/kind-edges`** (spec 37, approved): changes `_audio_by_label_only` (`probe.py:192-197`) and the `may_hold_video` expression in `_read_result` (`:339-340`), adding `_declared_video`. This item moves that expression, as kind-edges leaves it, into a helper that `_classify_entry` also calls. The same line: write it on top of kind-edges' version. The scratch run shows list entries from `<audio>` tags carry `extractor_key` `HTML5MediaEmbed`, so kind-edges' `_declared_video` applies to them unchanged. Spec 37 leaves `wizard.py` alone; its wizard effects come through the probe.
- **`feature/ig-lone-images`** (spec 61, approved): adds a lone-image branch to `_skip_items_without_media` (`downloader.py:737-764`), a `lone_images` field to `DownloadResult`, a `saveable_image` field to `Probe` (not `ProbeItem`), and edits `wizard.py`'s plan builder (`:386-400`), the retry message (`:591-592`) and `:610-623`. This item's probe calls sit at `:586`, `:590` and `:604`, around spec 61's retry message: textual. Its lone-image branch must also record into the new `images` list, so a lone image under `-a` gets D5's line. That flips spec 61's `test_audio_mode_skips_a_lone_image` from `t("no_supported_items")` to `t("no_audio_items", 1)`; say so in the report.

## Design

### D1: `-p` at the picker

- `_walk_url` reads, next to `preset` (`:565`) and before anything changes `opts`: `whole = opts.playlist_mode == "yes" and not opts.playlist_items`. That's `-p`, and `--playlist-items ''`. `run_all` starts every walk from the command line's options, so each URL gets it.
- It reaches `step_select` through `_walk_plan` and `_run_step`, as `preset` does, as a keyword with a default (`_run_step` keeps working with three arguments: `test_wizard_flow.py` calls it so).
- In `step_select`, with `whole` and no preset: the prompt is `t("ask_selection_default_all")`, and Enter does what `all` does today: `opts.playlist_items = ""`, prints `t("selected", f"all (1-{len(items)})")`, returns. Typed selections, `all`, `b` and `c` are unchanged. A non-empty preset (`--playlist-items 2`) keeps spec 20's prompt and Enter.
- Nothing else moves: `""` already means "every item" to `_images_only`, to `build_ydl_opts` (no `playlist_items`, so yt-dlp reads the whole list) and to `equivalent.build` (`-p`). So the run, its count and its command are those of `-y -p`.

### D2: `-P` reaches the probe

- `probe(url, cookies_browser=None, *, no_playlist=False)`: `ydl_opts["noplaylist"] = no_playlist`.
- `_walk_url` reads `no_playlist = opts.playlist_mode == "no"` next to `preset`, before `:631-638` overwrite `playlist_mode`, and passes it to all three `probe(...)` calls (the first probe, the one after an unreadable browser, the one after the cookies prompt).
- Then the existing flow does the rest. An extractor that honours `noplaylist` returns the single video: `SINGLE_VIDEO`, no picker, `playlist_mode = "no"`, and the download runs with `noplaylist: True` (`downloader.py:391`). The printed command carries `-P` when the URL looks like a playlist (`equivalent.py:60-63`; `https://fake.test/watch?list=pl` does). A carousel or a generic page comes back as a list, and the picker shows as today.
- The test fakes that replace `wizard.probe` with `lambda url, cookies:` gain `**_`.

### D3: the type default follows the pick

- `ProbeItem.may_hold_video: bool = False`, set in `_classify_entry` with the same predicate `_read_result` uses for a lone result. Move that expression (`probe.py:339-340`, as kind-edges leaves it) into one helper, say `_may_hold_video(kind, info)`, and call it from both. The default keeps every hand-built `ProbeItem` in the tests as it is.
- `_audio_items_only(opts, items)`, next to `_images_only` and read the same way (`None` selection or a spec only yt-dlp reads: false; `""`: every item): true when every chosen item is `supported`, `SINGLE_AUDIO` and not `may_hold_video`.
- `step_type(opts, audio_default=False)`: the default is `"2" if opts.audio_only or audio_default else "1"`. The `type` lambda in `_run_step` passes `_audio_items_only(opts, items)`; `items` is already there.
- Only the default changes: the step is still asked, its answer still sets `audio_only`, and `step_output`'s folder default follows `audio_only` as today (`~/Music`). After `b`, the step's default is the previous answer, as for every step.
- Lone results are unchanged: `SINGLE_AUDIO` skips the step, label-only audio keeps spec 27's step at video.

### D4, D5: the count under `-a`

- `_skip_items_without_media(skipped, *, keep_images, images=None)`: an image it skips goes into `skipped`, as today, and into `images` when one is given. Items with nothing to fetch only go into `skipped`. `list_formats` passes no `images`.
- `DownloadResult.skipped_images: int = 0`: how many of `skipped` were images. `total` is unchanged.
- `download()` passes an `images` list and sets `skipped_images=len(images)`. Images are only skipped when `keep_images` is false, which in `download()` means `-a`.
- `cli._download_one`:
  - nothing saved: `failed` (missing items, as today), else `no_audio_items` with `skipped_images` when it's non-zero, else `no_supported_items`;
  - something saved: `partial_result_images(saved, total, skipped_images, skipped - skipped_images, failed)` when `skipped_images`, else today's `partial_result` when anything was skipped or failed.

### Messages (both blocks)

| Key                          | es                                                                                                                                  | en                                                                                                                                   |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `ask_selection_default_all`  | `Selecciona items (Enter = todos, como pide -p; c = cancelar): `                                                                    | `Pick items (Enter = all of them, as -p asks; c = cancel): `                                                                         |
| `partial_result_images`      | `{} de {} items descargados ({} omitidos por ser imágenes, que el modo audio no guarda; {} sin medio descargable, {} con error).`    | `{} of {} items downloaded ({} skipped as images, which audio mode doesn't save; {} without downloadable media, {} failed).`          |
| `no_audio_items`             | `Ningún item se puede guardar como audio: {} omitidos por ser imágenes, que el modo audio no guarda. Sin -a, o eligiendo video, se guardan.` | `No items can be saved as audio: {} skipped as images, which audio mode doesn't save. Without -a, or choosing video, they are saved.` |

"Audio mode", not "`-a`", in the counts: a wizard user chose "Audio only" and typed no flag. `partial_result`, `no_supported_items`, `ask_selection_default`, `ask_selection_default_preset` and `item_image_skipped` are unchanged. The wording is a proposal; the developer may adjust it, keeping both blocks in step and the tests on `t()`.

### `unsupported_count`

Delete the property. Nothing reads it (R6).

### Docs

`AGENTS.md`, after the `-i` bullet's sentence: "`-p` makes the item picker's Enter take the whole list, as `-y -p` does, and `-P` makes the probe ask yt-dlp for the single video, as the download does; a list yt-dlp returns whole even then (a carousel) still gets the picker."

## Cases

"Today" is `0c76bcd`. `watch` is the page above, at `https://fake.test/watch?list=pl`.

| Case                                                        | Today                                                                         | After                                                                                                                   |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `-i -P watch`, Enter                                        | Picker, 3 numbered files, `--playlist-items 1,2,3`                            | No picker, 6 prompts, `Clip.mp4`, command with `-P`, as `-y -P`                                                         |
| `-i watch`, `-i -p watch`                                   | Picker, 3 files                                                                | Same (`-i -p`: Enter = all, command `-p`)                                                                               |
| `-i -P` on `ig-carousel`, `multi.html`                      | Picker, numbered files                                                         | Same (Out)                                                                                                              |
| `-i -P`, gated page, cookies at the prompt                  | Picker after the second probe                                                  | The second probe asks with `noplaylist` too                                                                             |
| `-i -p carousel`, Enter                                     | `Selected: 2,4`, 2 files, no count, `--playlist-items 2,4`                    | `ask_selection_default_all`, `Selected: all (1-4)`, 2 files, `partial_result(2, 4, 2, 0)`, command `-p`, as `-y -p`      |
| `-i -p ig-carousel`, Enter                                  | 4 files, `--playlist-items 1,2,3,4`                                           | 4 files, command `-p`                                                                                                   |
| `-i -p`, typed `2`                                          | `02 - Clip [v1].mp4`, `--playlist-items 2`                                    | Same                                                                                                                    |
| `-i --playlist-items 2`                                     | Spec 20's preset                                                               | Same                                                                                                                    |
| `-i -p` on a single video                                   | Wizard drops `-p` (spec 20 D5)                                                 | Same                                                                                                                    |
| Wizard on `audio-carousel`, `audios.html`, Enter            | Type `[1]`, raw `.m4a` in `~/Videos`, 7 prompts                               | Type `[2]`, format, `.mp3` in `~/Music`, 6 prompts, command with `-a`                                                    |
| Same, `1` at the type step                                  | n/a                                                                            | Raw `.m4a` in `~/Videos`, as today's Enter                                                                              |
| Wizard on `audios-theora-and-vorbis.html`, Enter           | Type `[1]`, both `.ogg` in `~/Videos`, one with video                         | Same: both items may hold video                                                                                         |
| Wizard on `everything.html`, typed `3` (the `.m4a`)         | Type `[1]`, raw `.m4a`                                                         | Type `[2]`, `.mp3` in `~/Music`                                                                                         |
| Wizard on a mixed pick (video and audio)                    | Type `[1]`                                                                     | Same                                                                                                                    |
| Lone `.m4a`, lone label-only audio                          | No type step; spec 27's step at `[1]`                                          | Same                                                                                                                    |
| `-y -a -p`, `-y -a`, `-i -a` + Enter on `ig-carousel`       | `partial_result(2, 4, 2, 0)`                                                   | `partial_result_images(2, 4, 2, 0, 0)`; no `partial_result` line                                                        |
| `-y -a -p mixed-carousel`                                   | `2 of 4 … (1 without downloadable media, 1 failed)`                           | `partial_result_images(2, 4, 1, 0, 1)`                                                                                  |
| `-y -a -p carousel` (images without thumbnail)              | `partial_result(2, 4, 2, 0)`                                                   | Same: those have nothing to fetch                                                                                       |
| `-y -a -p`, `-y -a` on `ig-only-images`                     | `No items are downloadable. Cancelling.`, exit 1                              | `no_audio_items(2)`, exit 1, no files                                                                                   |
| `-y -p ig-only-images`                                      | Both `.jpg`                                                                    | Same                                                                                                                    |
| `-y -a` on a lone image with cookies (after spec 61)        | `no_supported_items`, exit 1                                                   | `no_audio_items(1)`, exit 1                                                                                             |
| `-y -l`, `-y -l -a` on `ig-carousel`                        | `item_image_skipped`, no count                                                 | Same                                                                                                                    |
| With / without cookies                                      | —                                                                              | Same results: cookies only change what yt-dlp returns. The probe takes `noplaylist` and the browser together            |
| Rerun of the printed command                                | —                                                                              | Same files, `already downloaded`, no new file                                                                           |
| Several URLs, `-i -P watch carousel`                        | Pickers for both                                                               | `watch` single, `carousel` picker: each walk starts from the command line                                               |
| Partial and total failure                                   | —                                                                              | Exit codes unchanged: 0 when something saved, 1 when nothing (spec 23's rules after it merges)                          |

## Acceptance criteria

**[fix]** fails on `main` without this item; **[pin]** holds on `main` and must keep holding.

New `fake_site` pages (`tests/conftest.py`, `_codec_shape`):

- `watch`: `if self._yes_playlist("pl", "v1")`: `playlist_result([self._video(i, page="watch") for i in ("v1", "v2", "v3")], "pl", "Playlist")`; else `self._video("v1", page="watch")`. Used as `https://fake.test/watch?list=pl`, so `is_playlist_url` is true.
- `ig-only-images`: `playlist_result([self._instagram_image("i1"), self._instagram_image("i2")], "post", "Post")`.

Criteria:

- [ ] `scripts/pre-release.sh --no-live` passes.
- [ ] **[fix]** e2e `test_wizard_capital_p_asks_yt_dlp_for_the_single_video`: `-i -P -o <out> https://fake.test/watch?list=pl` with 6 replies: exactly 6 prompts, none of them a picker prompt; `detected("kind_video")`; `names(out) == ["Clip.mp4"]`; exit 0; the one printed command, `shlex.split`, contains `-P`, and replayed into `again` saves `["Clip.mp4"]`.
- [ ] **[pin]** e2e `test_wizard_without_capital_p_offers_the_whole_list`: `-i` on the same URL with `PICKER_WALK`: the 3 numbered files.
- [ ] **[pin]** `test_interactive_no_playlist_flag_still_shows_the_picker` (`test_e2e.py:2019`) unchanged, and `-i -P` on `ig-carousel` with `PICKER_WALK` still saves spec 01's 4 numbered files.
- [ ] **[fix]** e2e `test_wizard_p_takes_the_whole_list_on_enter`, over `carousel` and `ig-carousel`: `-i -p` with `PICKER_WALK`; the picker prompt is `t("ask_selection_default_all")`; `t("selected", "all (1-4)")` in the output; the files `-y -p` saves on that page; on `carousel`, `t("partial_result", 2, 4, 2, 0)`; the printed command is `download-media -y -o <out> -p <url>`, and replayed into `again` it saves the same names.
- [ ] **[pin]** e2e `test_wizard_p_still_takes_a_typed_selection`: `-i -p carousel`, `2` at the picker: `["02 - Clip [v1].mp4"]`, command with `--playlist-items 2`.
- [ ] **[pin]** spec 20's preset tests (`test_e2e.py:737-817`: `test_wizard_picker_preselects_the_playlist_items_flag`, `…_preset_out_of_range_is_reported`, `…_preset_reaches_every_url`, `…_preset_survives_going_back`, `…_preset_prompt_in_both_languages`) pass unchanged.
- [ ] **[fix]** e2e `test_wizard_defaults_to_audio_on_a_pick_of_audio`, over `audio-carousel` (with `fake_site` and `home`, no `-o`) and generic `audios.html`: replies URL, picker, type, format, folder, cookies, all Enter: exactly 6 prompts, the type prompt is `t("ask_choice", "2")`; two `.mp3` files in `home/Music`, `home/Videos` empty; the command carries `-a`. `test_wizard_tags_audio_items_without_codec_info` (`:1813`) becomes this test's `audios.html` row.
- [ ] **[fix]** e2e `test_wizard_type_default_follows_the_items_picked`: generic `everything.html`, `3` at the picker, then Enter: `t("ask_choice", "2")` among the prompts, `home/Music == ["03 - everything (3) [everything-3].mp3"]`. With `1,3` at the picker, the type prompt is `t("ask_choice", "1")`.
- [ ] **[pin]** `test_a_page_of_labelled_audio_items_keeps_the_carousel_flow` (`:3623`) and `test_wizard_audio_type_on_every_audio_item_goes_to_music` (`:3694`) pass unchanged.
- [ ] **[fix]** e2e `test_audio_mode_skips_the_images_as_images` (`:3855`) expects `t("partial_result_images", 2, 4, 2, 0, 0)` and `not said_partial_result(output)`; `test_wizard_in_audio_mode_on_a_mixed_pick_skips_the_image` (`:3942`) and the `-a -p` row of `test_audio_and_list_modes_skip_a_linked_image_as_an_image` (`:4219`) expect `t("partial_result_images", 1, 2, 1, 0, 0)`. New: `-i -a` on `ig-carousel` with Enter (6 replies) prints `t("partial_result_images", 2, 4, 2, 0, 0)`.
- [ ] **[fix]** e2e `test_audio_mode_counts_images_apart_from_failures`: `-y -a -p mixed-carousel`: `t("partial_result_images", 2, 4, 1, 0, 1)`, exit 0.
- [ ] **[pin]** e2e: `-y -a -p carousel` still prints `t("partial_result", 2, 4, 2, 0)`, and no `partial_result_images` line.
- [ ] **[fix]** e2e `test_audio_mode_on_a_list_of_only_images_says_why`, over `-y -a -p` and `-y -a` on `ig-only-images`: `t("no_audio_items", 2)`, no `t("no_supported_items")`, exit 1, no files, no history row. `-y -p` on it saves `01 - Clip [i1].jpg` and `02 - Clip [i2].jpg`.
- [ ] **[fix]** `test_probe.py`: on `fake_site`, `probe("https://fake.test/watch?list=pl", no_playlist=True)` is `SINGLE_VIDEO`; without the keyword it's a `PLAYLIST` of 3.
- [ ] **[fix]** `test_probe.py`: `_classify_entry` sets `may_hold_video` on an `<audio>`-shaped `.ogg` entry (`vcodec` `'none'`, no `acodec`) and not on an `.m4a` one, nor on a video; the lone-result flag is unchanged (the existing tables pass).
- [ ] **[fix]** `test_wizard_flow.py`: a carousel `Probe` of two supported `SINGLE_AUDIO` items, Enter at the picker and the type step, ends with `audio_only` true; the same with one item `may_hold_video=True`, or with a video item, ends with `audio_only` false.
- [ ] **[fix]** `test_wizard_flow.py`: `DownloadOptions(urls=[URL], playlist_mode="yes")` and Enter at the picker gives `playlist_items == ""`, with `t("ask_selection_default_all")` as the prompt; `playlist_mode="no"` makes the walk call the probe with `no_playlist=True`, and `"auto"` or `"yes"` with `False`.
- [ ] **[fix]** `test_downloader.py`: the `match_filter` given an `images` list puts a skipped image in both lists and an item with nothing to fetch only in `skipped`; without one it behaves as today. `DownloadResult(saved=1, skipped=2, skipped_images=1, failed=3).total == 6`.
- [ ] `grep -rn unsupported_count src tests` prints nothing.
- [ ] `test_i18n.py` passes: the three new keys are in both blocks.
- [ ] `AGENTS.md` carries the sentence in Design.

## Live check

Recommended, not blocking: everything above runs on yt-dlp's real `_yes_playlist`, and this only confirms YouTube's tab extractor still calls it. One command, through the tool, no cookies, nothing downloaded. The URL is from yt-dlp's own tests (`extractor/youtube/_tab.py:1453`):

```bash
printf '\nc\n' | .venv/bin/download-media -i -P -o tmp/picker 'https://www.youtube.com/watch?v=FqZTN594JQw&list=PLMYEtVRpaqY00V9W81Cwmzp6N6vZqfUKD4'; echo "exit $?"
ls -A tmp/picker 2>/dev/null | wc -l      # 0
```

What must hold: `Detected: youtube / Video` with the video's title, no item list, the type step, then `Cancelled.` and exit 1 (0 before cancel-exits merges). Without `-P`, the same command shows `Carousel/Playlist` and the picker. If YouTube refuses the probe for reasons of its own (a bot check, a missing PO token), record it and rely on the e2e tests.

## Open decisions

All recommended options are what this spec describes, taken under the overnight delegation. **(published behaviour)** marks the ones for the 0.4.0 review.

- **D1, `-i -p` at the picker (published behaviour).** **A (recommended):** Enter takes the whole list, as `all` and `-y -p` do, with its own prompt; the printed command keeps `-p`. It's spec 20 D6's rule ("a preselected default means exactly what `-y` would do with the same flag") applied to `-p`, and `AGENTS.md`'s "intent flags become preselected defaults". Its cost: on a list holding items the probe calls unsupported, Enter now tries them, and the run prints `partial_result`, where today's Enter left them out quietly. **B:** keep today's Enter (everything downloadable) and only print `-p` when it selected every item: the same files, but `-p` would stay a no-op at the picker, and the command would claim a run the user didn't make. **C:** leave it.
- **D2, `-i -P` at the probe (published behaviour).** **A (recommended):** every probe of the walk passes `noplaylist`, so a video inside a playlist is probed, offered and saved as that video, as `-y -P` saves it. Lists yt-dlp returns whole anyway keep the picker. It narrows spec 17's "the wizard's own decision overrides `-P` on any multi-entry URL" to the URLs yt-dlp itself treats as lists under `-P`. **B:** under `-P`, skip the picker on every list and keep single mode, matching `-y -P`'s names on carousels too. It drops the picker spec 17 and 18 pin for carousels under `-i -P`, for a naming difference. **C:** leave it: the flag stays reversed on YouTube watch links.
- **D3, the type default on all-audio picks (published behaviour).** **A (recommended):** default to audio when every picked item is audio the probe trusts; items that may hold a video keep the video default. Enter then saves `.mp3` files in `~/Music`, as a lone `.m4a` page already does, and `1` still keeps the raw files. **B:** skip the type step on such picks, as a lone audio result does: one prompt fewer, but no way left to keep the raw files, and `test_wizard_audio_type_on_every_audio_item_goes_to_music` loses its step. **C:** leave it. `AGENTS.md` counts a changed default as breaking; spec 17 read "which type a page is detected as" as not breaking, since each type keeps its defaults, and this is the same reading. The human's call, as for spec 01 D2.
- **D4, the count under `-a` (published behaviour).** **A (recommended):** a second message, `partial_result_images`, used only when audio mode skipped images; `partial_result` keeps its four numbers everywhere else, so its 84 uses in `test_e2e.py` stay. **B:** rebuild `partial_result` from only its non-zero parts: one message for all cases, but every pinned count changes. **C:** leave images out of the count under `-a`: no line at all on `ig-carousel`, but the summary then hides a skip a long carousel scrolls away.
- **D5, nothing saved under `-a` (published behaviour, small).** **A (recommended):** `no_audio_items` when audio mode skipped images, naming how many and what to do. It changes spec 61's `-a` line on a lone image. **B:** keep `no_supported_items`, which calls images that `-y -p` saves "not downloadable".

## SemVer

A fix that adds messages, so minor; 0.4.0 already is one. For the 0.4.0 review list:

1. D1: under `-i -p`, the picker's Enter takes the whole list, items marked unsupported included, as `-y -p` does; the run prints the same count and the command keeps `-p`. New message: `ask_selection_default_all`.
2. D2: under `-i -P`, the wizard asks yt-dlp for the single video, so a YouTube `watch?v=…&list=…` link saves that video, where it saved the whole playlist. Carousels and generic pages keep the picker.
3. D3: a wizard pick made only of audio items (that can't hold a video) defaults to audio: `.mp3` in `~/Music`, where Enter kept the raw files in `~/Videos`.
4. D4: under `-a`, skipped images are counted as images. New message: `partial_result_images`.
5. D5: under `-a`, a list of only images says so. New message: `no_audio_items`, also after spec 61's lone image.

Release note, for `.ai/releases/0.4.0.md`: "In the wizard, `-p` and `-P` now preselect what they ask for: `-p` takes the whole list at the item picker, and `-P` saves just the video when a link points to a video inside a playlist. A pick of audio tracks defaults to audio. In audio mode, skipped images are reported as images."
