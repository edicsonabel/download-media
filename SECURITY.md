# Security Policy

## Supported versions

This project follows [SemVer](https://semver.org/). Only the latest released version receives security updates.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| Older   | :x:                |

## Reporting a vulnerability

If you find a security issue, **please do not open a public GitHub issue**. Report it privately through GitHub's built-in vulnerability reporting:

👉 **[Open a private security advisory](https://github.com/edicsonabel/download-media/security/advisories/new)**

This is GitHub's native private-reporting workflow. Only the maintainers can see your report; it never appears in the public issue tracker until a fix is published.

Please include:

1. A description of the issue and its impact.
2. Steps to reproduce (commands, sample URLs if relevant, expected vs. actual behavior).
3. Your version: `download-media --version` and `yt-dlp --version`.
4. Your operating system and shell.

You should receive an acknowledgment within **7 days**. If accepted, a fix will be released as soon as possible and credited in the release notes (unless you prefer to remain anonymous).

## Scope

This wrapper executes `yt-dlp` and `ffmpeg` as user-level processes. The kinds of issues that fall in scope:

- Shell injection through user input (URLs, output paths, flag values).
- Unsafe file writes outside the requested output folder.
- Leakage of cookies, history, or credentials to unintended locations.
- Privilege escalation paths created by the wrapper itself.

Out of scope (please report upstream):

- Vulnerabilities in `yt-dlp` itself → https://github.com/yt-dlp/yt-dlp/security
- Vulnerabilities in `ffmpeg` itself → https://ffmpeg.org/security.html
- Issues with browser cookie storage formats → report to the browser vendor.
