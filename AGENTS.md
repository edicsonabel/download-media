# AGENTS.md

Single source of project conventions for AI coding agents (Claude Code, Codex,
Cursor, Aider, Continue, etc.) and human contributors. `CLAUDE.md` only points
here — edit this file, not that one.

# download-media

Interactive CLI wrapper around `yt-dlp`, written in Python and packaged for
Arch Linux (AUR).

## Stack and philosophy

- **Language:** Python ≥ 3.10. Package source at `src/download_media/`, entry
  point exposed as the `download-media` console script
  (`download_media.cli:main`).
- **Runtime deps:** `yt-dlp` (used as a library — `from yt_dlp import
  YoutubeDL`, never shelled out), `ffmpeg` (audio extraction + merging quality
  streams), `secretstorage` (decrypting Chromium-family browser cookies on
  Linux).
- **Optional deps:** `xdg-user-dirs` (localized `Videos`/`Music` folders),
  `wl-clipboard` (Wayland) or `xclip`/`xsel` (X11) for clipboard URL detection.
- **Build backend:** `hatchling` via `pyproject.toml`.
- **Distribution:** PKGBUILD (Arch) that builds and installs the wheel. Source
  hosted on GitHub, package published on AUR.

The tool is deliberately a **wrapper** on top of `yt-dlp`: it never
re-implements what `yt-dlp` or `ffmpeg` already do. It only adds UX (wizard,
clipboard, preview, carousel item picker, history, sensible defaults per
download type).

## Layout

```
.
├── pyproject.toml                 # metadata, deps, ruff/mypy/pytest config
├── PKGBUILD                       # Arch recipe (source = GitHub release tarball)
├── README.md                      # user docs (English)
├── AGENTS.md                      # this file
├── CLAUDE.md                      # pointer to AGENTS.md
├── CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, LICENSE (MIT)
├── .github/workflows/
│   ├── deploy-aur.yml             # tag push → patch PKGBUILD, push to AUR
│   └── deploy-pages.yml           # main push → publish docs/ to GitHub Pages
├── docs/index.html                # project landing page (GitHub Pages)
├── src/download_media/
│   ├── cli.py                     # argparse, mode dispatch, per-URL pipeline
│   ├── wizard.py                  # probe-first interactive wizard
│   ├── probe.py                   # "what is at this URL?" (site, kind, items, auth)
│   ├── downloader.py              # DownloadOptions → yt-dlp options dict
│   ├── config.py                  # defaults, DownloadOptions, xdg_dir(), state paths
│   ├── equivalent.py              # equivalent direct-mode command after a run
│   ├── selection.py               # item selection parser ("1-3,5")
│   ├── history.py                 # append-only TSV download log
│   ├── clipboard.py               # Wayland/X11 clipboard URL detection
│   ├── i18n.py                    # es/en runtime messages
│   └── ui.py                      # colors, prompts, formatters
├── tests/                         # pytest suite (no network)
├── legacy/                        # old Bash prototype — reference only, not built or tested
└── tmp/                           # scratch folder for test downloads (contents gitignored)
```

## Conventions

- **Naming:** kebab-case for files and CLI tokens (`download-media`,
  `--video-format`), snake_case for Python identifiers.
- **i18n (runtime UX):** dual es/en, decided once from `$LANG` (prefix `es*` →
  Spanish, otherwise English). All user-facing messages live in the `es` and
  `en` blocks of `i18n.py` and are looked up with `t(key, *args)`. **When you
  add a new message, add it to both blocks.**
- **Code, comments, docs and commits: English only.** The runtime i18n is for
  end users; everything else stays in English so the project stays accessible
  to any contributor.
- **Colors:** helpers in `ui.py`, emitted only when stdout is a TTY.
- **Persistent paths:** `$XDG_STATE_HOME` (fallback `~/.local/state`) — history
  lives at `~/.local/state/download-media/history.log`.
- **Output folders:** resolved through `xdg_dir("VIDEOS", "Videos")` /
  `xdg_dir("MUSIC", "Music")` in `config.py` (uses `xdg-user-dir` when
  available, falls back to `~/Videos` / `~/Music`). Never hardcode
  `~/Videos` or `~/Music`.
- **Comments:** minimal. Only the *why* when it isn't obvious. Short module
  docstrings are fine; no long docstrings on trivial functions.
- **Typing:** `mypy --strict` must pass on `src/download_media`.
- **Formatting:** ruff, line length 100.
- **Sensible defaults:** video → `~/Videos`, audio → `~/Music`, video quality
  cap 1080p, container mp4, audio mp3.

## Execution modes

- **Wizard (default):** runs when no URL is provided, or when a URL is provided
  without any "intent flags" (`-a`, `-q`, `-f`, `--video-format`, `-o`, `-s`,
  `--sub-langs`, `-p`, `-P`, `--cookies`). It is probe-first:
  1. URL (clipboard detected if available), plus cookies up front for sites
     that always need them.
  2. Probe the URL with yt-dlp; on an auth failure, ask for cookies and
     re-probe. If probing fails outright, fall back to a manual flow.
  3. Steps tailored to the detected content: item selection (carousels /
     playlists) → type → quality + container (video) or format (audio) →
     folder → cookies. Each step shows a running summary and accepts `b` to
     go back and `c` to cancel.
- **Direct:** triggered by passing any intent flag — the wizard is skipped and
  the download runs with those values plus defaults (a short preview and
  confirmation is still shown for single URLs).
- **`-i`:** force the wizard even when intent flags are present (they become
  preselected defaults in each prompt).
- **`-y`:** skip the wizard and all confirmations (scripting mode).

After every run (cancelled or successful) the equivalent direct-mode command
is printed (`equivalent.py`), so the user can repeat the operation without the
wizard. It omits flags whose values match the defaults.

## Development

```bash
# One-time setup
python -m venv .venv
.venv/bin/pip install -e '.[dev]'

# Verification — all must pass before committing
.venv/bin/pytest -q              # tests (no network access)
.venv/bin/ruff check src tests   # lint
.venv/bin/ruff format src tests  # format
.venv/bin/mypy                   # strict type check

# Run the current checkout
.venv/bin/download-media --help  # locale-dependent rendering
.venv/bin/download-media -o tmp/ '<url>'   # test downloads to the gitignored scratch folder

# Inspect download history
cat ~/.local/state/download-media/history.log

# Local package build (requires the GitHub release to exist, or --skipchecksums)
makepkg -f --skipchecksums
sudo pacman -U download-media-*.pkg.tar.zst
namcap PKGBUILD
```

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/). Subject in
lower case, imperative mood, ≤ 72 chars. Body in plain English, focused on
the *why*; wrap at ~80 chars.

Allowed types:

| Type       | When to use                                                    |
| ---------- | -------------------------------------------------------------- |
| `feat`     | New user-visible feature, flag, wizard step, or message.       |
| `fix`      | Bug fix that changes observable behavior.                      |
| `refactor` | Internal restructuring with no behavior change.                |
| `perf`     | Performance improvement, no behavior change.                   |
| `docs`     | Documentation only (README, AGENTS.md, docstrings, comments).  |
| `test`     | Adding or fixing tests only.                                   |
| `build`    | Build/packaging changes (`pyproject.toml`, PKGBUILD).          |
| `ci`       | GitHub Actions, AUR deploy workflow, Pages workflow.           |
| `style`    | Formatting only — no logic change. Rare; ruff handles most.    |
| `chore`    | Repo housekeeping (gitignore, scaffolding, license, meta-doc). |

Optional scope after the type when it sharpens intent:
`feat(wizard):`, `fix(probe):`, `ci(aur):`, `docs(readme):`, etc.

Use `BREAKING CHANGE:` in the body (or `!` after type/scope) for any change
that renames a flag, alters defaults, or shifts on-disk paths/state format.

Examples — good:

```
feat(wizard): add browser cookies step
fix(probe): treat None entries as unsupported images
refactor(cli): extract per-URL pipeline into helper
docs: document quoted-URL rule for shells
ci(aur): wait for release tarball before computing sha256
```

Examples — avoid:

```
update stuff               # no type, vague
feat: add cookies and fix  # two intentions in one commit
fix(wizard): bug           # body would have to explain everything
```

One commit per logical change. No multi-purpose mega-commits.

## Versioning

SemVer starting from **0.0.1** (patch). Increments:
- **patch (0.0.x):** fixes, minor tweaks, changes that don't alter expected behavior.
- **minor (0.x.0):** new features, new flags, new messages.
- **major (x.0.0):** breaking changes (renamed flags, changed defaults), marks "stable API".

**Do not bump the version while iterating.** The version stays fixed until a
release is actually published. Bump it as part of the same commit that creates
the git tag and the GitHub release.

The version lives in **3 places** that must be kept in sync:
1. `src/download_media/__init__.py` → `__version__ = "x.y.z"`
2. `pyproject.toml` → `version = "x.y.z"`
3. `PKGBUILD` → `pkgver=x.y.z`

## Distribution (AUR)

Flow: commit the version bump → tag `vX.Y.Z` → push the tag → create the
GitHub release.

Pushing a `v*` tag triggers `.github/workflows/deploy-aur.yml`, which waits
for the GitHub release tarball, patches `pkgver` and `sha256sums` in the
PKGBUILD, regenerates `.SRCINFO`, and pushes both to
`ssh://aur@aur.archlinux.org/download-media.git`. The AUR repo only holds
`PKGBUILD` and `.SRCINFO`. The workflow can also be run manually
(`workflow_dispatch`) with an explicit version.

## Future-change reminders

- Adding support for "more sites" never requires code changes — `yt-dlp`
  already supports 1800+ sites. Only update the `pkgdesc`/README wording if
  it's relevant.
