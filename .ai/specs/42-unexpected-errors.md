# 42 — Stop errors other than `DownloadError` ending a run in a traceback

Status: draft
Item: `fix/unexpected-errors`

## Problem

An output folder that can't be created, and a few other errors that aren't a `DownloadError`, escape `_download_one`. The run ends in a traceback, the URLs after it never run, and no command is printed.

Reproduced on `main` at `d56c2bb`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. The real console script ran in both locales, and nothing reached the network: the folder is created before any request.

```bash
LANG=en_US.UTF-8 .venv/bin/download-media -y -o /proc/x 'http://127.0.0.1:9/a.mp4' 'http://127.0.0.1:9/b.mp4'
# Downloading...
#   URL: http://127.0.0.1:9/a.mp4
#   → /proc/x
#
# Traceback (most recent call last):
#   ...
#   File ".../download_media/downloader.py", line 479, in download
#     output_dir.mkdir(parents=True, exist_ok=True)
#   ...
# FileNotFoundError: [Errno 2] No such file or directory: '/proc/x'
# exit 1
```

`LANG=es_ES.UTF-8` gives the same traceback after `Descargando...`. Row 42 points at `downloader.py:449`. The line is now `downloader.py:479`.

- **What happened:** a traceback, exit 1. `b.mp4` is never tried, and no equivalent command is printed.
- **What should happen:** one line saying the folder can't be used and why, then `Download failed: <url>` for each URL, in every mode. The remaining URLs still run, the equivalent command is printed, and the exit code is 1.

## Findings

### Every path, measured on `main`

The first row was run through the console script. The others went through `cli.main` with the `fake_site` fixture (real yt-dlp and ffmpeg, loopback only), using a scratch test that added two pages: `thumbed`, a video with a `tiny.jpg` thumbnail, and `crash`, an extractor that raises `TypeError`. `ro` is an existing folder with mode `0500`, and `afile` is a regular file. The `-l`, `junk-thumb` and `-P` rows ran under `es_ES`, and the rest under `en_US`. None of the messages involved are translated, so the locale doesn't change any of them.

| Run                                              | Output                                                                                             | Exit                       |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------- | -------------------------- |
| `-y -o /proc/x video video-b`                    | **`FileNotFoundError` traceback**. `video-b` never runs                                            | 1 (uncaught)               |
| `-y -p -o /proc/x carousel`                      | **`FileNotFoundError` traceback**                                                                  | 1 (uncaught)               |
| `-y -o afile video`                              | **`FileExistsError` traceback**                                                                    | 1 (uncaught)               |
| `-y -o ro video`                                 | yt-dlp `ERROR: unable to open for writing: [Errno 13] …`, `Download failed`, command printed       | 1                          |
| `-y -P -o ro carousel`                           | Same, clean                                                                                        | 1                          |
| `-y -a -o ro thumbed` (and `junk-thumb`)         | `Writing video thumbnail 0 to: …/Clip.cover.jpg`, then **`PermissionError` traceback**             | 1 (uncaught)               |
| `-y -a -p -o ro thumbed`                         | yt-dlp `ERROR: [Errno 13] Permission denied: …/Clip.cover.jpg`, `Download failed`, command printed | 1                          |
| `-y -o ok crash video`                           | **`TypeError` traceback**. `video` never runs                                                      | 1 (uncaught)               |
| `-y -p -o ok crash video`                        | yt-dlp `ERROR: 'NoneType' object is not subscriptable`, `Download failed` for `crash`, `video` saved, `Done.`, one command | 1 |
| `-y --playlist-items 1 -o ok crash`              | Same as `-p`, clean                                                                                | 1                          |
| `-y -l crash`                                    | **`TypeError` traceback**                                                                          | 1 (uncaught)               |

### What yt-dlp wraps and what it lets through (source, 2026.10.26)

- **Our own `mkdir`** (`downloader.py:479`) runs before yt-dlp starts, so nothing wraps it, in any mode. yt-dlp would create the folder by itself: `process_info` calls `_ensure_dir_exists(full_filename)` (`YoutubeDL.py:3378`). That method turns an `OSError` into `report_error('Unable to create directory: …')` (`:2040-2046`), and `report_error` raises `DownloadError` unless `ignoreerrors` is set (`trouble`, `:1072-1111`).
- **Extraction and processing.** `__extract_info` and `__process_iterable_entry` carry `_handle_extraction_exceptions` (`:1723-1753`, applied at `:1858` and `:2196`). That decorator re-raises `CookieLoadError`, `DownloadCancelled` and the lazy-list `IndexError`s, and reports `ExtractorError` through `report_error`. **Any other `Exception` is reported only under `ignoreerrors`. Without it, the exception is re-raised as it is.** `process_info` runs inside these calls, so the same rule covers everything it does.
- **In `process_info`:** the media download wraps network errors in `report_error`, and an `OSError` in `UnavailableVideoError` (`:3592-3598`). `__download_wrapper` (`:3681-3697`) reports that `UnavailableVideoError`, which is why `-o ro video` ends cleanly. Post-processors: `run_pp` catches only `PostProcessingError` (`:3808-3813`). Any other exception from a step, such as the `os.replace`, `os.remove` or `shutil.move` in `MoveFilesAfterDownloadPP` (`movefilesafterdownload.py:40-52`), goes up. Post hooks are wrapped in `except Exception` (`:3666-3671`).
- **Thumbnails:** `_write_thumbnails` catches only `network_exceptions` around `open(thumb_filename, 'wb')` (`:4536-4549`), so a write error escapes. Our `_Ydl._write_thumbnails` adds `RequestError` and `_UnsafeExtensionError` (spec 28), but not `OSError`.
- **Extractors:** `InfoExtractor.extract` wraps `KeyError`, `StopIteration` and `IncompleteRead` in `ExtractorError` (`extractor/common.py:785-788`). A `TypeError`, `AttributeError` or `IndexError` from extractor code is not wrapped. These are common when a site changes.

The result: **under `ignoreerrors` (`-p`, `--playlist-items`, a picker run), only our `mkdir` escapes.** Without it (wizard single, direct, `-y`, `-P`), anything that isn't an `ExtractorError`, a network error or a media-write `OSError` escapes: thumbnail write errors, non-`PostProcessingError` failures in post-processing, and extractor bugs. `KeyboardInterrupt` and `SystemExit` are `BaseException`s and escape in every mode, as intended.

### Other things measured

- `_show_preview` and `probe()` already catch `Exception` (`cli.py:200`, `probe.py:366`). Neither leaks.
- `-l` goes through `_list_formats` (`cli.py:379-390`), not `_download_one`. It creates no folder and writes no file, so only extractor bugs reach it (the `-l crash` row).
- `history.append` runs only after a success, and swallows its own `OSError` (`history.py:17-24`). A failed URL has never written a history row.
- The cookie fallback (`cli.py:333-365`) only catches `DownloadError` and `CookiesUnavailable`, so another exception from the first attempt goes up without a retry. That is right: the error isn't about cookies.

## Goal

Any error that isn't a `DownloadError` fails only the URL it happened on, with a message, never a run-ending traceback. The other URLs still run. The run exits 1, and the equivalent command is printed as after any other failed download.

## Scope

### In

- `_download_one`: catch `Exception` around the download call, as well as `DownloadError` (D1).
- The output folder: a dedicated message when it can't be created (D3).
- Errors that look like bugs keep their traceback for the report, without ending the run (D2).
- `-l` (`_list_formats`) gets the same handling (D4).
- Three new messages, in both blocks of `i18n.py`: `output_unusable`, `unexpected_error` and `unexpected_bug`.
- Tests: e2e on `fake_site`, plus unit tests in `test_cli.py`.

### Out

- **Ctrl-C (`KeyboardInterrupt`) and exit codes for cancelling.** That's row 23 (`cancel-exits`). The existing `except KeyboardInterrupt: warn(cancelled); raise` in `_download_one` stays as it is, and `except Exception` doesn't catch it.
- **`CookieLoadError`.** Row 26 handles it below `_download_one` (`CookiesUnavailable`). Nothing changes there.
- **Re-asking the folder in the wizard** when it can't be created. A wizard that checks the folder up front is a separate UX change, and it races with the real `mkdir`. Noted for the planner.
- **Treating a thumbnail write `OSError` as "saved without a cover"** (extending spec 28 D1 to `OSError` in `_Ydl._write_thumbnails`). When the folder is unwritable, the audio fails right after anyway. Only odd cases differ, such as a directory sitting at `<title>.cover.jpg`. The catch-all covers it with an accurate message. Noted for the planner.
- **`BrokenPipeError` from piping output into `head`.** It's an `OSError` and the catch-all would see it, but the next `print` raises again. That is how it already behaves, and it's another row's problem if it's ever reported.
- **Files saved before a single-mode carousel fails.** yt-dlp downloads carousels even with `noplaylist`. Without `ignoreerrors`, a failure on item 2 fails the URL after item 1 is on disk. That's already true for `DownloadError` (`test_single_mode_stops_at_a_broken_item`), and this item doesn't change it.
- **Removing our `mkdir` and leaving the folder to yt-dlp.** See D5.

## Files

| Action | File                                 |
| ------ | ------------------------------------ |
| modify | `src/download_media/downloader.py`   |
| modify | `src/download_media/cli.py`          |
| modify | `src/download_media/i18n.py`         |
| modify | `tests/conftest.py`                  |
| modify | `tests/test_e2e.py`                  |
| modify | `tests/test_cli.py`                  |

No overlap with open rows except `cancel-exits` (row 23), which also edits the `except` chain in `_download_one`. Merge them one after the other.

## Design

### `downloader.py`

A new exception next to `CookiesUnavailable`:

```python
class OutputUnusable(Exception):
    """The output folder can't be created; nothing was requested yet."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(reason)
        self.path = path
        self.reason = reason
```

In `download()`, the `mkdir` becomes:

```python
try:
    output_dir.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    raise OutputUnusable(output_dir, exc.strerror or str(exc)) from exc
```

`exc.strerror` is the OS text without the errno and path (`No such file or directory`, `File exists`, `Permission denied`, `Read-only file system`, `Not a directory`). The path is printed by the message itself. The docstring adds: "Raises `OutputUnusable` if the folder can't be created." Nothing else in `download()` changes.

### `cli.py`

In `_download_one`, the `try` keeps covering only `_download_with_cookie_fallback(url, effective)`. Bugs in the reporting code after a download stay visible. The `except` chain becomes, in this order:

1. `except DownloadError` stays as it is.
2. `except KeyboardInterrupt` stays as it is (row 23).
3. `except OutputUnusable as exc:` prints `err(t("output_unusable", exc.path, exc.reason))`, then `warn(t("failed", url))`, and returns `_Outcome(False, False, pmode)`.
4. `except Exception as exc:` calls `_report_unexpected(exc)`, then `warn(t("failed", url))`, and returns `_Outcome(False, False, pmode)`.

A small helper, shared with `_list_formats`:

```python
def _report_unexpected(exc: Exception) -> None:
    """An error yt-dlp didn't report; an OSError is the system's, anything else likely a bug."""
    if isinstance(exc, OSError):
        err(t("unexpected_error", exc))
        return
    traceback.print_exception(exc)  # stderr: what a bug report needs (D2).
    err(t("unexpected_bug", f"{type(exc).__name__}: {exc}"))
```

`mode` is `pmode`, so the URL counts as one that reached the download. `_print_equivalent` prints its command, and `main` returns 1. No history row is written. The cookie-hint and keyring-hint logic (`_hint_for_download_error`) isn't called: the error isn't one of yt-dlp's.

`_download_with_cookie_fallback` doesn't change. `OutputUnusable` and any other non-`DownloadError` from the first attempt aren't retried without cookies, because they aren't about cookies. If the first attempt fails with a `DownloadError` and the retry without cookies raises something else (a full disk, say), that second error goes up and is the one shown. It's the last thing that happened, and the user can act on it. `cookies_dropped` is `False` for every failed outcome, as today.

`_list_formats` (D4): after its `except DownloadError`, add `except Exception as exc:` → `_report_unexpected(exc)`, `warn(t("failed", url))`, `return 1`. `KeyboardInterrupt` isn't caught there either.

`err` comes from `ui.py` (stderr, red on a TTY). `failed` keeps going through `warn` (stdout), as today.

### `i18n.py`

Add the same keys to both blocks:

| Key                | en                                                                                                                                  | es                                                                                                                                   |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `output_unusable`  | `Can't save to {}: {}. Choose another folder with -o.`                                                                              | `No se puede guardar en {}: {}. Elige otra carpeta con -o.`                                                                          |
| `unexpected_error` | `Error: {}`                                                                                                                         | `Error: {}`                                                                                                                          |
| `unexpected_bug`   | `Unexpected error ({}). It may be a bug: if updating yt-dlp doesn't fix it, report it with the output above at https://github.com/edicsonabel/download-media/issues` | `Error inesperado ({}). Puede ser un fallo: si actualizar yt-dlp no lo arregla, repórtalo con la salida de arriba en https://github.com/edicsonabel/download-media/issues` |

`unexpected_error` gets `str(exc)` of an `OSError`: `[Errno 13] Permission denied: '/…/Clip.cover.jpg'`. It names the file, which a bare `strerror` wouldn't. The URL is the `Issues` entry in `pyproject.toml:44`.

### `tests/conftest.py`

A new `FakeSiteIE` page, `crash`, whose extraction raises `TypeError("'NoneType' object is not subscriptable")`. It stands for an extractor bug: it's the one non-`OSError` path that's reachable without monkeypatching yt-dlp. `junk-thumb` already gives a single video with a thumbnail, for the `-a` write error.

### What the user sees

```text
$ download-media -y -o /proc/x URL1 URL2
Downloading...
  URL: URL1
  → /proc/x

Can't save to /proc/x: No such file or directory. Choose another folder with -o.   (stderr)
Download failed: URL1
Downloading...
  URL: URL2
  → /proc/x

Can't save to /proc/x: No such file or directory. Choose another folder with -o.   (stderr)
Download failed: URL2

Equivalent command to repeat this download:
  download-media -y -o /proc/x URL1 URL2
# exit 1
```

## Cases

| Case                                      | Expected                                                                                                                                                         |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| wizard, one URL, folder can't be created  | After the walk: `output_unusable`, `failed`, the command with that `-o`, exit 1, no history row                                                                  |
| wizard, two URLs, the first folder bad    | First URL: `output_unusable`, `failed`. The second is saved in its own folder with `Done.`. Two commands (different `-o`), exit 1                                |
| direct, no `-y`                           | Preview and confirm as today, then the same as `-y`                                                                                                              |
| `-y`, single URL                          | `output_unusable` (folder), `unexpected_error` (an `OSError` inside yt-dlp, e.g. `-a` into a read-only folder) or traceback plus `unexpected_bug` (`crash`). Then `failed`, the command, exit 1 |
| `-y`, several URLs                        | Each URL fails or succeeds on its own. Exit 1 if any failed                                                                                                      |
| `-P`                                      | Same as `-y` single (no `ignoreerrors`)                                                                                                                          |
| `-p`, `--playlist-items`, picker          | Folder: `output_unusable` once per URL, before any item. Errors inside yt-dlp: unchanged, since yt-dlp already reports them per item (`-p crash video` keeps its measured output) |
| `-l`                                      | `crash`: traceback plus `unexpected_bug`, `failed`, exit 1. No folder involved                                                                                   |
| with cookies                              | A non-`DownloadError` is never retried without cookies. `CookiesUnavailable` is unchanged (row 26)                                                               |
| single, carousel, playlist                | As above. A single-mode carousel that fails midway fails the URL, as today with `DownloadError` (Out)                                                            |
| total failure                             | Every URL failed: exit 1, the command printed                                                                                                                    |
| partial failure                           | One URL failed and another saved: exit 1, `Done.` for the saved one, the command lists both                                                                      |
| Ctrl-C                                    | Unchanged: `Cancelled.`, then the `KeyboardInterrupt` propagates (row 23)                                                                                        |
| rerun                                     | Not affected: nothing new is written to disk                                                                                                                     |

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes.
- [ ] `LANG=en_US.UTF-8 .venv/bin/download-media -y -o /proc/x 'http://127.0.0.1:9/a.mp4' 'http://127.0.0.1:9/b.mp4'` prints no `Traceback`. It prints `Can't save to /proc/x: No such file or directory. Choose another folder with -o.` twice and `Download failed:` for both URLs, then the equivalent command `download-media -y -o /proc/x http://127.0.0.1:9/a.mp4 http://127.0.0.1:9/b.mp4`, and exits 1. Under `LANG=es_ES.UTF-8`, the same with `No se puede guardar en /proc/x: …` and `Falló la descarga:`.
- [ ] e2e: `-y -o <file>/sub VIDEO VIDEO_B`, where `<file>` is a regular file under `tmp_path`, returns 1 from `cli.main` without raising. The output holds `t("output_unusable", <path>, "Not a directory")` once per URL, `t("failed", url)` for both, and one equivalent command with both URLs. No history row, and `<file>` is left as it was.
- [ ] e2e: the same with `-y -p` on `CAROUSEL` gives one `output_unusable`, exit 1, no traceback.
- [ ] e2e: a scripted wizard on two URLs, where the first walk's folder answer can't be created and the second's is `out`. The second URL is saved in `out`, the first gets `output_unusable` and `failed`, exit 1, and two commands are printed.
- [ ] e2e: `-y -o out https://fake.test/crash VIDEO` returns 1 without raising. The output holds `Traceback`, `TypeError`, `t("unexpected_bug", "TypeError: 'NoneType' object is not subscriptable")` and `t("failed", crash url)`. `VIDEO` is saved in `out` (`Clip.mp4`) with `Done.`, and one command lists both URLs. The test fails on `main`.
- [ ] e2e: `-y -p -o out https://fake.test/crash VIDEO` keeps its output on `main`. There is no `unexpected_bug`, since yt-dlp reported the error itself, `VIDEO` is saved, and the exit is 1.
- [ ] e2e: `-y -a -o <ro> https://fake.test/junk-thumb`, with `<ro>` mode `0500`, returns 1 without raising. The output holds `t("unexpected_error", …)` with `Permission denied` and the `.cover.jpg` path, and no `Traceback`. `<ro>` is empty. Skipped when `os.geteuid() == 0`. The test fails on `main`.
- [ ] e2e: `-y -l https://fake.test/crash` returns 1 without raising, with `unexpected_bug` and `failed` (D4).
- [ ] unit (`test_cli.py`): `_download_one` with `download` monkeypatched to raise `OSError(28, "No space left on device", "/x/f")` returns `_Outcome(False, False, "no")` and prints `t("unexpected_error", …)`. With `--cookies firefox`, `download` is called once: no retry without cookies.
- [ ] unit: `_download_one` with `download` raising `KeyboardInterrupt` still raises it (the existing `test_interrupt_propagates_after_cancel_message`, `test_cli.py:402-412`, stays green).
- [ ] `test_i18n.py::test_es_and_en_have_same_keys` passes with the three new keys.
- [ ] Every new e2e test that "fails on `main`" is shown to fail with the `src/` change reverted (constraints, Verification 5).

## Live check

Not needed. Every failure here happens on the local machine: creating the folder, writing a file, or an exception's path through yt-dlp's wrappers. The `fake_site` e2e tests reproduce each one with the real yt-dlp and ffmpeg, and the console-script command above checks the real entry point. A real site would add nothing: the folder is created before the first request, and an extractor bug can't be summoned on demand.

## SemVer

Minor (counted in the 0.4.0 batch): three new messages. Nothing breaking. No flag, default or state path changes.

## Open decisions

The human delegated these to the roles. Each one has a recommended option for the orchestrator to approve and record. **They change published behaviour, so they are marked for the human's review before the release that ships them.**

### D1: what `_download_one` catches

| Option                                         | Effect                                                                                                                                                  |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. `Exception`, after `DownloadError` (recommended)** | Every path in Findings, and any that wasn't found, fails only its URL. `KeyboardInterrupt` and `SystemExit` still pass. Bugs stay visible through D2 |
| B. `OSError` only                              | Environment errors are handled. An extractor bug (`crash`) still kills the run and skips the later URLs                                                  |
| C. Fix at the source only (wrap `mkdir`, add `OSError` to `_write_thumbnails`) | No catch-all, so unknown paths (post-processing `os.replace`, extractor bugs) keep ending the run                                       |

**Recommendation: A.**

### D2: what the user sees for an error that isn't an `OSError`

| Option                                                       | Effect                                                                                                                                                         |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Traceback on stderr, then `unexpected_bug` (recommended)** | It's almost always a bug (ours or yt-dlp's), and the traceback is what the report needs. The run goes on, so it no longer *ends* in one                         |
| B. One line only (`TypeError: …`), no traceback               | Matches how yt-dlp prints it under `-p`, but the report would lack the traceback, and there's no `--verbose` flag to get it back                               |

**Recommendation: A.** `OSError`s get one line in both options: they come from the system, and the user can act on them.

### D3: the output folder's message

| Option                                                  | Effect                                                                                                                                       |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Dedicated `output_unusable` via `OutputUnusable` (recommended)** | `Can't save to <path>: File exists. Choose another folder with -o.` says what to do. A bare `[Errno 17] File exists: '<path>'` reads as if a download already existed |
| B. Let the generic `unexpected_error` print it          | One message fewer, but less clear for `FileExistsError` and `NotADirectoryError`                                                              |

**Recommendation: A.** The message repeats once per URL that uses the folder, like spec 26 D8.

### D4: `-l`

| Option                                  | Effect                                                                                     |
| --------------------------------------- | ------------------------------------------------------------------------------------------ |
| **A. Same handling in `_list_formats` (recommended)** | The one other per-URL path that ends in a traceback (`-y -l crash`). It's the same helper, four lines |
| B. Leave it to another row              | The item stays strictly on `_download_one`, and `-l` keeps the traceback                   |

**Recommendation: A.**

### D5: keep our `mkdir` or leave the folder to yt-dlp

| Option                                         | Effect                                                                                                                                                                                                 |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A. Keep it, wrapped (recommended)**          | Fails fast with one message per URL, before any request. A failed run still leaves the folder created, as today                                                                                         |
| B. Delete it, and let `_ensure_dir_exists` report | Closer to "wrapper, not re-implementation", and no empty folder is left after a failure. But under `-p`, every item prints `Unable to create directory`, so a 50-item list gives 50 errors after 50 extractions |

**Recommendation: A.**
