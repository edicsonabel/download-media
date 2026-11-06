# Project

**download-media** is an interactive CLI wrapper around `yt-dlp`, written in Python and published on the AUR. Stack, layout, commands and conventions are in `AGENTS.md`; this file only says what the project is for.

## Who it's for

People who want to save a video, song or carousel without learning `yt-dlp` flags, and people who script it once they know what they want. Arch Linux first.

## What it adds

UX only: a probe-first wizard, clipboard detection, previews, a carousel item picker, history, browser cookies that work, and sensible defaults per download type.

## What it is not

- Not a downloader: `yt-dlp` and `ffmpeg` do that work. Anything they already do gets exposed, never re-implemented.
- Not a per-site tool: support for a new site should never need code here.
- Not a GUI or a service.

## What good looks like

- The defaults work, and the file plays everywhere.
- When something fails, the user learns why and what to do next. A failure never reads as success, and a success never hides what was skipped.
- What the tool reports matches what is on disk.
