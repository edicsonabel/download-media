# download-media

Interactive Bash wrapper around `yt-dlp`, packaged for Arch Linux (AUR).

## Stack and philosophy

- **Language:** plain Bash (`#!/bin/bash`, `set -euo pipefail`).
- **Runtime deps:** `yt-dlp` (actual download engine), `ffmpeg` (audio extraction + merging quality streams).
- **Optional deps:** `xdg-user-dirs` (localized `Videos`/`Music` folders), `wl-clipboard` (Wayland) or `xclip`/`xsel` (X11) for clipboard URL detection.
- **Distribution:** PKGBUILD (Arch). Source code hosted on GitHub, package published on AUR.

The script is deliberately a **wrapper** on top of `yt-dlp`: it never re-implements what `yt-dlp` already does. It only adds UX (wizard, clipboard, preview, history, sensible defaults per download type).

## Layout

```
.
├── PKGBUILD                       # Arch recipe (source = GitHub release tarball)
├── README.md                      # user docs (English)
├── CLAUDE.md                      # this file
├── LICENSE                        # MIT
├── .gitignore                     # ignores pkg/, src/, *.pkg.tar.*, tmp/* (keeps tmp/.gitkeep)
├── bin/
│   └── download-media             # main script (kebab-case)
├── completions/
│   ├── download-media.bash        # bash completion
│   └── _download-media            # zsh completion
├── man/
│   └── download-media.1           # roff manpage
└── tmp/                           # scratch folder for tests (gitignored contents)
```

## Conventions

- **Naming:** kebab-case for files and commands (`download-media`, never `downloadVideo`).
- **i18n (runtime UX):** dual es/en, decided by `${LANG}` (prefix `es*` → Spanish, otherwise English). All user-facing messages live in `M_*` blocks at the top of the script. **When you add a new message, add it to both blocks.**
- **Code, comments, docs and commits: English only.** The runtime i18n is for end users; everything else stays in English so the project stays accessible to any contributor.
- **Colors:** literal `$'\e[...m'` escapes, disabled when stdout is not a TTY (`[[ -t 1 ]]`).
- **Persistent paths:** `$XDG_STATE_HOME` (fallback `~/.local/state`) — history lives at `~/.local/state/download-media/history.log`.
- **Output folders:** resolved through `xdg_dir VIDEOS Videos` / `xdg_dir MUSIC Music` (uses `xdg-user-dir` when available, falls back to `~/Videos` / `~/Music`). Never hardcode `$HOME/Videos`.
- **Comments:** minimal. Only the *why* when it isn't obvious. No long docstrings.
- **Sensible defaults:** video → `~/Videos`, audio → `~/Music`, video quality cap 1080p, container mp4, audio mp3.

## Execution modes

- **Wizard (default):** runs when no URL is provided, or when a URL is provided without any "intent flags" (`-a`, `-q`, `-f`, `--video-format`, `-o`, `-s`, `--sub-langs`, `-p`, `-P`, `--cookies`). Steps: URL → type → quality+container (video) or format (audio) → folder → cookies. Each step shows a running summary of choices made so far and accepts `b` to go back to the previous step.
- **Direct:** triggered by passing any intent flag — wizard is skipped and the download runs with those values plus defaults.
- **`-i`:** force wizard even when intent flags are present (they become preselected defaults in each prompt).
- **`-y`:** skip wizard and all confirmations (scripting mode).

After every download (cancelled or successful) `print_equivalent_command()` prints the equivalent direct-mode command, so the user can repeat the operation without going through the wizard. It omits flags whose values match the defaults.

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
| `docs`     | Documentation only (README, CLAUDE.md, manpage, comments).     |
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

**Do not bump the version while iterating.** The version stays fixed until a release is actually published. Bump it as part of the same commit that creates the git tag and the GitHub release.

The version lives in **3 places** that must be kept in sync:
1. `bin/download-media` → `VERSION="x.y.z"`
2. `man/download-media.1` → `.TH ... "x.y.z" ...` line
3. `PKGBUILD` → `pkgver=x.y.z`

After bumping, also update the GitHub tag (`v0.0.1`) and regenerate `.SRCINFO` for AUR.

## Distribution (AUR)

Flow: edit code → git tag → GitHub release → update PKGBUILD/`.SRCINFO` → push to `ssh://aur@aur.archlinux.org/download-media.git`.

The PKGBUILD pulls source from the tarball GitHub generates per release. The AUR repo only holds `PKGBUILD` and `.SRCINFO`.

## Useful commands during development

```bash
# Syntax check
bash -n bin/download-media

# Run the wizard
download-media

# Test downloads to the scratch folder (gitignored)
download-media -o tmp/ <url>

# Inspect download history
cat ~/.local/state/download-media/history.log

# Local package build (requires the GitHub release to exist, or --skipchecksums)
makepkg -f --skipchecksums
sudo pacman -U download-media-*.pkg.tar.zst

# PKGBUILD linter
namcap PKGBUILD
namcap download-media-*.pkg.tar.zst

# Clean build artifacts
rm -rf src/ pkg/ download-media-*.tar.gz *.pkg.tar.zst
```

A symlink `~/.local/bin/download-media` → `bin/download-media` is set up locally so `download-media` in any terminal runs this repo's version (no `makepkg -si` needed for iteration). If the symlink breaks:

```bash
ln -sf "$(pwd)/bin/download-media" ~/.local/bin/download-media
```

## Future-change reminders

- Adding support for "more sites" never requires script changes — `yt-dlp` already supports 1800+ sites. Only update the `pkgdesc`/README/manpage wording if it's relevant.
- If the script ever grows beyond ~500 lines or needs complex state, consider rewriting it in Python or Go (keeping the public binary name as `download-media`).
