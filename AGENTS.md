# AGENTS.md

This file is the entry point for AI coding agents (Codex, Cursor, Aider, Continue, Claude Code, etc.) working on this repository.

The full project conventions, architecture, and operational rules live in [`CLAUDE.md`](./CLAUDE.md). **Read it first.** This file exists so non-Claude agents know where to look.

## Quick reference

- **Language:** Python ≥ 3.10. Package source at `src/download_media/`, entry point exposed as the `download-media` console script.
- **Wraps:** `yt-dlp` (used as a library — `from yt_dlp import YoutubeDL`) plus `ffmpeg`. Never reimplement what they already do.
- **Build backend:** `hatchling` via `pyproject.toml`. Distribution: PKGBUILD on AUR (Python wheel install).
- **Style:** kebab-case for files and CLI tokens, snake_case for Python identifiers. English in code, comments, docs, and commits. Runtime user messages stay bilingual (es/en) — see `i18n.py`.
- **Use Conventional Commits** (`feat`, `fix`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `perf`, `style`). Subject lower case, imperative, ≤ 72 chars. See `CLAUDE.md` for the full table.
- **No version bumps during iteration.** Bump `__version__` in `src/download_media/__init__.py`, `version` in `pyproject.toml`, and `pkgver` in `PKGBUILD` only on the same commit that creates a release tag.
- **Do not hardcode `~/Videos` / `~/Music`.** Use the `xdg_dir()` helper in `config.py`.
- **When you add a runtime message, add it to both the `es` and `en` blocks** in `i18n.py`.
- **Past prototype** lives under `legacy/` — historical reference only, not built or tested.

## Verification commands

```bash
.venv/bin/pytest -q              # tests must stay green (123+)
.venv/bin/ruff check src tests   # lint
.venv/bin/ruff format src tests  # format
.venv/bin/mypy                   # strict type check
download-media --version
download-media --help            # locale-dependent rendering
```

For everything else (wizard architecture, module layout, distribution flow, release process) → see `CLAUDE.md`.
