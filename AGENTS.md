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
  (`download_media.cli:entry`, which wraps `main`).
- **Runtime deps:** `yt-dlp` (used as a library — `from yt_dlp import
  YoutubeDL`, never shelled out), `ffmpeg` (audio extraction + merging quality
  streams), `secretstorage` (decrypting Chromium-family browser cookies on
  Linux), `curl_cffi` (browser impersonation — without it yt-dlp skips
  Instagram's logged-out path and public posts fail).
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
├── .ai/                           # agent system: roles, specs, roadmap, pipeline state
├── .github/workflows/
│   ├── ci.yml                     # main push and PRs → the gate, no live tests
│   ├── deploy-aur.yml             # tag push → tests, patch PKGBUILD, push to AUR
│   └── deploy-pages.yml           # main push → publish docs/ to GitHub Pages
├── docs/index.html                # project landing page (GitHub Pages)
├── scripts/
│   ├── pre-release.sh             # release gate: lint, types, tests, live tests
│   └── ai-link.py                 # symlinks .ai/ into .claude/ and .opencode/
├── src/download_media/
│   ├── cli.py                     # argparse, mode dispatch, per-URL pipeline
│   ├── wizard.py                  # probe-first interactive wizard
│   ├── probe.py                   # "what is at this URL?" (site, kind, items, auth)
│   ├── downloader.py              # DownloadOptions → yt-dlp options dict
│   ├── config.py                  # defaults, DownloadOptions, xdg_dir(), state paths
│   ├── cookies.py                 # --cookies BROWSER[+KEYRING] spec, keyring detection
│   ├── equivalent.py              # equivalent direct-mode command after a run
│   ├── selection.py               # item selection parser ("1-3,5")
│   ├── history.py                 # append-only TSV download log
│   ├── clipboard.py               # Wayland/X11 clipboard URL detection
│   ├── i18n.py                    # es/en runtime messages
│   └── ui.py                      # colors, prompts, formatters
├── tests/                         # pytest suite (loopback only)
│   └── live/                      # real network and cookies, opt-in
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
  `--sub-langs`, `-p`, `-P`, `--playlist-items`, `--cookies`). It is probe-first:
  1. URL (clipboard detected if available), plus cookies up front for sites
     that always need them.
  2. Probe the URL with yt-dlp; on an auth failure, ask for cookies and
     re-probe. If probing fails outright, fall back to a manual flow.
  3. Steps tailored to the detected content: item selection (carousels /
     playlists) → type → quality + container (video) or format (audio) →
     folder → cookies. Each step shows a running summary and accepts `b` to
     go back and `c` to cancel.

  With several URLs, the wizard walks each one in turn, each starting from the
  command-line flags, then downloads them all. `c` cancels the whole run before
  anything downloads. A URL the wizard refuses is skipped, and named again at
  the end.
- **Direct:** triggered by passing any intent flag — the wizard is skipped and
  the download runs with those values plus defaults (a short preview and
  confirmation is still shown for single URLs).
- **`-i`:** force the wizard even when intent flags are present (they become
  preselected defaults in each prompt).
- **`-y`:** skip the wizard and all confirmations (scripting mode).

Multi-entry URLs: images (no formats, a thumbnail, like Instagram's) are saved
through yt-dlp's thumbnail writer, with three yt-dlp params scoped to them
(`_Ydl.process_video_result`). Items with nothing to fetch are still skipped by a
`match_filter`, not by `ignoreerrors`, so a single-mode failure still raises and
reaches the cookie fallback. yt-dlp downloads carousels even with `noplaylist`,
so single mode names their items with `[id]`. A run only counts as done if
something was saved; `-p` must not report success on a total failure.
`--playlist-items` names any item that isn't in the list, and a single video in
playlist mode keeps its single-mode name.

After every run that reached the download, whether it worked or not, the
equivalent direct-mode command is printed (`equivalent.py`), so the user can
repeat it without the wizard or any prompt. It always carries `-y`, and
otherwise omits flags whose values match the defaults. URLs that ended in
different playlist modes, or with different wizard choices, get one command
each. Nothing is printed when the run was cancelled before downloading: a
cancelled wizard only holds half-made choices.

Exit status: 0 only when every URL given was saved (warnings allowed). 1 when
any URL wasn't: it failed, the wizard refused it, or it was cancelled or
declined at a prompt (`c`, `n`, EOF); also for a missing URL. 2 for a usage
error. Ctrl-C never shows a traceback: it prints `Cancelled.` on a fresh line,
or, during a download, the `interrupted` line naming the URL and the folder
where unfinished files may be left, followed by the equivalent command for the
URLs that reached the download. Later URLs don't run. Then `entry()` ends the
process by SIGINT, so a shell shows 130 and a calling loop stops; `main()`
returns 130 instead, so tests can call it in-process.

## Development

```bash
# One-time setup
python -m venv .venv
.venv/bin/pip install -e '.[dev]'

# Verification — all must pass before committing
.venv/bin/pytest -q              # tests (no network, loopback only; needs ffmpeg)
.venv/bin/ruff check src tests   # lint
.venv/bin/ruff format src tests  # format (the gate runs it with --check)
.venv/bin/mypy                   # strict type check

# Release gate (see Testing)
DM_LIVE_IG_CAROUSEL='<public post>' DM_LIVE_BROWSER=brave scripts/pre-release.sh

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

## Testing

Three layers, all required before a release:

- **Unit** (`tests/test_*.py`): pure logic and scripted wizard walks.
- **End-to-end** (`tests/test_e2e.py`): `cli.main` with the real yt-dlp and
  ffmpeg, against the `fake_site` fixture (fake extractor, loopback server). Use
  it for anything that depends on how yt-dlp behaves: mocks can't see that.
- **Live** (`tests/live/`, marker `live`, deselected by default): real sites, the
  real keyring, real cookies. Configured through `DM_LIVE_*` variables; never
  hardcode personal URLs.

Every bug fix adds a test that fails without the fix. The autouse `isolated_env`
fixture keeps tests away from the real history log, browser flag files and, outside
the live tests, the home folder and the session keyring (a dead D-Bus address).

## Agent system

Work goes through a pipeline of agents: an orchestrator launches specialized
roles (spec → implementation → tests → review → integration), each item in its
own git worktree, with human approval at the key points. A planner keeps the
roadmap, and a separate pipeline handles releases.

- How it works: `.ai/README.md` and `.ai/orchestration/WORKFLOW.md`
- Roadmap: `.ai/specs/PLAN.md`
- Commands: `/roadmap`, `/feature`, `/fix`, `/orchestrate`, `/pipeline`, `/release`
- Setup after cloning: `python scripts/ai-link.py`

If you are launched as one of those roles, your definition is in
`.ai/roles/<role>.md`, and it overrides this file on anything role-specific.

Every reply to the user in the main session starts with a label saying who
speaks, in the language of the conversation:

- `**[Orchestrator]**` when moving the pipeline.
- `**[Orchestrator · <role> report]**` when summarizing what a role delivered.
- `**[Assistant]**` for anything outside the pipeline.

Roles never talk to the user directly: they return their REPORT to the main
session, which summarizes it.

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

Commits and PRs are authored by the human you're working with, only. Never add
AI attribution: no `Co-authored-by:` trailer for an assistant, no
"Generated with ..." line, no session links.

## Versioning

SemVer starting from **0.0.1** (patch). Increments:
- **patch (0.0.x):** fixes, minor tweaks, changes that don't alter expected behavior.
- **minor (0.x.0):** new features, new flags, new messages.
- **major (x.0.0):** breaking changes (renamed flags, changed defaults), marks "stable API".

**Do not bump the version while iterating.** The version stays fixed until a
release is actually published. Bump it as part of the same commit that creates
the git tag and the GitHub release.

The version lives in **4 places** that must be kept in sync:
1. `src/download_media/__init__.py` → `__version__ = "x.y.z"`
2. `pyproject.toml` → `version = "x.y.z"`
3. `PKGBUILD` → `pkgver=x.y.z`
4. `docs/index.html` → the `vx.y.z` label in the hero (pushing it redeploys Pages)

## Distribution (AUR)

Flow: run `scripts/pre-release.sh` → commit the version bump → tag `vX.Y.Z` →
push the tag → create the GitHub release.

Pushing a `v*` tag triggers `.github/workflows/deploy-aur.yml`, which runs
`scripts/pre-release.sh --no-live`, then waits for the GitHub release tarball,
patches `pkgver` and `sha256sums` in the
PKGBUILD, regenerates `.SRCINFO`, and pushes both to
`ssh://aur@aur.archlinux.org/download-media.git`. The AUR repo only holds
`PKGBUILD` and `.SRCINFO`. The workflow can also be run manually
(`workflow_dispatch`) with an explicit version.

Every release also updates GitHub Pages: the bump changes `docs/index.html`, so
pushing `main` triggers `deploy-pages.yml`. `tests/test_packaging.py` fails the
gate if the landing page shows a different version.

## Future-change reminders

- Adding support for "more sites" never requires code changes — `yt-dlp`
  already supports 1800+ sites. Only update the `pkgdesc`/README wording if
  it's relevant.
