# download-media

Interactive wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp) with a step-by-step wizard, clipboard URL detection, download history, subtitles, and browser cookies.

Supports **1800+ sites** through yt-dlp: YouTube, Twitter/X, Instagram, TikTok, Facebook, Twitch, Vimeo, SoundCloud, Reddit, and more.

## Features

- **Wizard mode** — runs by default; asks step by step (URL → type → quality/container → folder → cookies). Type `b` at any prompt to go back to the previous step.
- **Direct mode** — traditional CLI flags for fast, scriptable usage.
- **Clipboard detection** — when no URL is passed, offers to use a URL it finds in the clipboard.
- **Preview + confirmation** — shows title, channel, duration, and approximate size before downloading.
- **Playlist detection** — asks whether to download just the single video or the whole playlist.
- **Subtitles** — `-s` to embed them, `--sub-langs es,en,fr` to pick languages.
- **Browser cookies** — `--cookies firefox` (or `brave`, `chrome`, etc.) for login-walled, age-restricted, or private content.
- **History** — every successful download is logged at `~/.local/state/download-media/history.log`.
- **Localized output folders** — respects your XDG user dirs (`Vídeos`, `Música`, `Movies`, custom paths on other disks, etc.).
- **Bilingual UI (es/en)** — chosen automatically from `$LANG`.
- **Equivalent command hint** — after every wizard run, prints the exact direct-mode command that reproduces the same download.

## Installation

### From AUR (recommended)

```bash
yay -S download-media
# or
paru -S download-media
```

### From this repo (local build)

```bash
git clone https://github.com/edicsonabel/download-media.git
cd download-media
makepkg -si
```

This builds and installs the package with `pacman`, putting the binary at `/usr/bin/download-media`, plus bash/zsh completions and the manpage.

### Uninstall

```bash
sudo pacman -R download-media
```

## Dependencies

| Package           | Type      | Purpose                                       |
|-------------------|-----------|-----------------------------------------------|
| `yt-dlp`          | required  | Actual download engine                        |
| `ffmpeg`          | required  | Quality merging + audio extraction            |
| `xdg-user-dirs`   | optional  | Resolve localized `Videos`/`Music` folders    |
| `wl-clipboard`    | optional  | Clipboard URL detection on Wayland            |
| `xclip` or `xsel` | optional  | Clipboard URL detection on X11                |

## Usage

### Wizard mode

```bash
download-media                    # asks everything, including URL
download-media <url>              # URL preset, asks the rest
```

The wizard walks through: URL → video/audio → quality and container (or audio format) → output folder → optional browser cookies. Every prompt shows the choices made so far and accepts `b` to go back one step.

### Direct mode (any intent flag skips the wizard)

```bash
download-media -a <url>                   # audio mp3 → ~/Music
download-media -q 720 -o . <url>          # 720p in current folder
download-media -a -f opus <url>           # opus audio
download-media --video-format mkv <url>   # mkv container
download-media -s --sub-langs en <url>    # English subtitles embedded
download-media --cookies firefox <url>    # use Firefox cookies
download-media url1 url2 url3             # multiple URLs in one run
download-media -l <url>                   # list available formats
download-media -p <url>                   # force full playlist
download-media -P <url>                   # force single video (ignore playlist)
```

> **Always quote URLs.** Unquoted URLs break in any shell because `?` and `&` are shell metacharacters. This is true for `curl`, `wget`, `yt-dlp`, and every CLI tool that takes URLs.

## All options

```
-a, --audio                Audio only (mp3 by default)
-q, --quality <N>          360 | 480 | 720 | 1080 | 1440 | 2160 | best
-f, --format <fmt>         mp3 | m4a | opus | wav
    --video-format <fmt>   mp4 | mkv | webm
-o, --output <dir>         Output folder
-l, --list                 List available formats and exit
-p, --playlist             Force full playlist
-P, --no-playlist          Force single video only
-s, --subs                 Download subtitles (default: es,en)
    --sub-langs <langs>    Comma-separated subtitle languages
    --cookies <browser>    firefox | chrome | chromium | brave | edge | opera | vivaldi | safari
-i, --interactive          Force wizard mode
-y, --yes                  Skip all confirmations
-v, --version              Show version
-h, --help                 Show help
```

## History

Every successful download is appended to `~/.local/state/download-media/history.log` as TSV:

```
2026-04-14T19:32:10-04:00	video	https://youtu.be/xxxxx	/home/user/Vídeos
2026-04-14T19:45:02-04:00	audio	https://youtu.be/yyyyy	/home/user/Música
```

## License

MIT — see [LICENSE](LICENSE).
