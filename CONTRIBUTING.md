# Contributing to download-media

Thanks for taking the time to contribute! This document explains the workflow, conventions, and how to test changes locally.

## Code of conduct

By participating in this project, you agree to abide by the [Code of Conduct](./CODE_OF_CONDUCT.md).

## Getting set up

Requirements:

- Arch Linux (or any distro with `bash`, `yt-dlp`, `ffmpeg` available)
- `xdg-user-dirs` (recommended, for localized folder detection)
- A browser with cookies set for sites you want to test against (YouTube usually requires browser cookies)

```bash
git clone https://github.com/edicsonabel/download-media.git
cd download-media

# Symlink the script into your PATH so edits are picked up immediately:
ln -sf "$(pwd)/bin/download-media" ~/.local/bin/download-media

download-media --version
```

Test downloads can go to the gitignored `tmp/` folder so they don't pollute your `~/Videos`:

```bash
download-media -o tmp/ "https://youtu.be/dQw4w9WgXcQ"
```

## Project layout

See [`CLAUDE.md`](./CLAUDE.md#layout) for the directory tree and what each file does.

## Conventions

### Code

- Plain Bash, `set -euo pipefail`, kebab-case filenames.
- Comments in English. Keep them minimal and only explain the *why*.
- Colors use literal `$'\e[...m'` escapes; respect `[[ -t 1 ]]` to disable when stdout is not a TTY.
- Never hardcode `~/Videos` or `~/Music` — use the `xdg_dir TYPE FALLBACK` helper.
- The script is a wrapper. If `yt-dlp` already does it, don't reimplement it.

### User-facing strings (i18n)

All runtime messages live in two blocks at the top of `bin/download-media`: one for `es*` locales, one for everything else. **When adding a new message, add it to both blocks** with a matching key (`M_*`).

### Commits

We use [Conventional Commits](https://www.conventionalcommits.org/). Subject
in lower case, imperative, ≤ 72 chars. Body explains the *why* (not the
*what* — the diff already shows that), wrapped at ~80 chars.

Allowed types: `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `build`,
`ci`, `style`, `chore`. See [`CLAUDE.md`](./CLAUDE.md#commit-conventions)
for when to use each.

Optional scope: `feat(wizard):`, `fix(probe):`, `ci(aur):`, `docs(readme):`.

Use `!` after the type/scope (or `BREAKING CHANGE:` in the body) when a
change renames a flag, alters a default, or shifts an on-disk path or
state format.

✅ Good:

```
feat(wizard): add browser cookies step
fix(probe): treat None entries as unsupported images
refactor(cli): extract per-URL pipeline into helper
docs: document quoted-URL rule for shells
```

❌ Avoid:

```
update stuff               # no type, vague
feat: add cookies and fix  # two intentions
fix(wizard): bug           # body would carry all the meaning
```

### Versioning

[SemVer](https://semver.org/), starting from `0.0.1`:

- **patch** (`0.0.x`): fixes, minor tweaks, no behavior change for existing flags.
- **minor** (`0.x.0`): new features, new flags, new wizard steps.
- **major** (`x.0.0`): breaking changes (renamed flags, changed defaults).

**Never bump the version while iterating.** The version stays fixed until a release is published. Bump it as part of the same commit that creates the release tag.

The version lives in three files that must stay in sync:

1. `bin/download-media` → `VERSION="x.y.z"`
2. `man/download-media.1` → `.TH ... "x.y.z" ...`
3. `PKGBUILD` → `pkgver=x.y.z`

## Testing changes

There's no automated test suite — this is a Bash wrapper and most behavior is interactive. Manual verification matrix:

```bash
# Syntax check
bash -n bin/download-media

# Help renders
download-media --help

# Wizard end-to-end (try the back option 'b' and cancel 'c')
download-media

# Direct mode (intent flag skips wizard)
download-media -a "https://youtu.be/dQw4w9WgXcQ"

# Multiple URLs
download-media "url1" "url2"

# With cookies (requires you to be logged in to that browser)
download-media --cookies firefox "<url>"

# Force-skip everything (scripting mode)
download-media -y "<url>"
```

Verify the equivalent-command line printed at the end matches what you'd write by hand.

## Releasing

1. Update `VERSION` in `bin/download-media`, `pkgver` in `PKGBUILD`, and the `.TH` line in `man/download-media.1`. All three in one commit.
2. Tag the commit: `git tag -a vX.Y.Z -m "vX.Y.Z"`
3. Push: `git push && git push --tags`
4. Create the GitHub release pointing at the tag (this generates the source tarball the AUR PKGBUILD downloads).
5. Update `.SRCINFO` in the AUR repo and push there:

   ```bash
   cd /tmp/aur-download-media
   cp /path/to/download-media/PKGBUILD .
   makepkg --printsrcinfo > .SRCINFO
   git add PKGBUILD .SRCINFO
   git commit -m "update to vX.Y.Z"
   git push
   ```

## Reporting bugs

Open an issue on [GitHub](https://github.com/edicsonabel/download-media/issues) with:

- Your distro and version
- `download-media --version`
- `yt-dlp --version`
- The exact command you ran (with quotes)
- The full output, including any error message

For security issues, see [SECURITY.md](./SECURITY.md) instead — please don't open a public issue.

## Pull requests

- Branch from `main`.
- Keep PRs focused on one topic.
- Update `README.md`, `man/download-media.1`, completions, and `CLAUDE.md` if your change affects user-visible behavior or project conventions.
- A maintainer will review and merge.
