# download-media

Interactive wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp) with a step-by-step wizard, clipboard URL detection, download history, subtitles, and browser cookies.

Supports **1800+ sites** through yt-dlp: YouTube, Twitter/X, Instagram, TikTok, Facebook, Twitch, Vimeo, SoundCloud, Reddit, and more.

## Features

- **Wizard mode** — runs by default; asks step by step (URL → type → quality/container → folder → cookies). Type `b` at any prompt to go back to the previous step.
- **Direct mode** — traditional CLI flags for fast, scriptable usage.
- **Clipboard detection** — when no URL is passed, offers to use a URL it finds in the clipboard.
- **Preview + confirmation** — shows title, channel, duration, and approximate size before downloading.
- **Playlist detection** — asks whether to download just the single video or the whole playlist.
- **Container choice** — videos are saved in the container you pick (`--video-format`): mkv by remuxing, with no quality loss; mp4 and webm by re-encoding when the site offers no such format, which can take a while on long videos. A video that can't be converted is kept as downloaded, and the run says so. Images and audio keep their own extension.
- **Subtitles** — `-s` embeds them in the video and keeps them as files next to it (`.vtt`, `.srt`, …); `--sub-langs es,en,fr` picks the languages and turns subtitles on. With `-a` they're saved as files only. A subtitle that can't be downloaded, or that holds no text, is named in a warning and never stops the video. In mp4 the first language is marked as the default track, so players may show it.
- **Browser cookies** — `--cookies firefox` (or `brave`, `chrome`, etc.) for login-walled, age-restricted, or private content. A Chromium browser's `--password-store` is detected from its flags file (`~/.config/brave-flags.conf`, ...); if yours is set elsewhere, name the keyring: `--cookies brave+gnomekeyring`. If the browser's cookies can't be read (it isn't installed, or its keyring can't be reached), the download says why and goes on without them.
- **Carousels** — images are saved next to the videos, in the same folder (`-a` skips them). Items with nothing to fetch are skipped, and the run reports how many items were saved and what it skipped.
- **History** — every successful download is logged at `~/.local/state/download-media/history.log`.
- **Localized output folders** — respects your XDG user dirs (`Vídeos`, `Música`, `Movies`, custom paths on other disks, etc.).
- **Bilingual UI (es/en)** — chosen automatically from `$LANG`.
- **Equivalent command hint** — after every download, prints the exact direct-mode command that reproduces it.

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
| `python-curl_cffi` | required | Browser impersonation; Instagram needs it without cookies |
| `xdg-user-dirs`   | optional  | Resolve localized `Videos`/`Music` folders    |
| `wl-clipboard`    | optional  | Clipboard URL detection on Wayland            |
| `xclip` or `xsel` | optional  | Clipboard URL detection on X11                |

## Usage

### Wizard mode

```bash
download-media                    # asks everything, including URL
download-media <url>              # URL preset, asks the rest
download-media url1 url2 url3     # one walk per URL, then all the downloads
```

The wizard walks through: URL → video/audio → quality and container (or audio format) → output folder → optional browser cookies. Every prompt shows the choices made so far and accepts `b` to go back one step. If a page can't be inspected, the wizard says why, and if it needs a login it offers browser cookies right after the URL.

### Direct mode (any intent flag skips the wizard)

```bash
download-media -a <url>                   # audio mp3 → ~/Music
download-media -q 720 -o . <url>          # 720p in current folder
download-media -a -f opus <url>           # opus audio
download-media --video-format mkv <url>   # mkv container (keeps VP9/AV1, up to 4K)
download-media -s --sub-langs en <url>    # English subtitles embedded
download-media --cookies firefox <url>    # use Firefox cookies
download-media -a url1 url2 url3          # several URLs, same options
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
    --video-format <fmt>   mp4 (H.264, plays everywhere) | mkv | webm
-o, --output <dir>         Output folder
-l, --list                 List available formats and exit
-p, --playlist             Force full playlist
-P, --no-playlist          Force single video only
    --playlist-items <n>   Download only these playlist items, e.g. 1-3,5
-s, --subs                 Subtitles, embedded in videos and kept as files (default: es,en)
    --sub-langs <langs>    Comma-separated subtitle languages (implies -s)
    --cookies <browser>    firefox | chrome | chromium | brave | edge | opera | vivaldi | safari
                           Optional keyring: brave+gnomekeyring (also basictext, kwallet, kwallet5, kwallet6)
-i, --interactive          Force wizard mode
-y, --yes                  Skip all confirmations
-v, --version              Show version
-h, --help                 Show help
```

## Exit status

- **0:** every URL given was saved (warnings allowed).
- **1:** any URL wasn't saved: it failed, the wizard couldn't save it, or it was cancelled or
  declined at a prompt (`c`, `n`, end of input). Also a missing URL.
- **2:** invalid options.
- **130:** Ctrl-C (the process ends by SIGINT, so a calling script loop stops too). A download
  interrupted this way names its folder, since unfinished files may be left there, and the
  printed equivalent command resumes it.

## History

Every successful download is appended to `~/.local/state/download-media/history.log` as TSV:

```
2026-04-14T19:32:10-04:00	video	https://youtu.be/xxxxx	/home/user/Vídeos
2026-04-14T19:45:02-04:00	audio	https://youtu.be/yyyyy	/home/user/Música
```

## License

MIT — see [LICENSE](LICENSE).
