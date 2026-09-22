# iOpenPod: The Open-Source iPod Manager & iTunes Alternative for Windows, macOS, and Linux

**Sync, manage, and listen to your iPod**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=plastic)](LICENSE)
[![Platform: Win | Mac | Linux](https://img.shields.io/badge/Platform-Win%20%7C%20Mac%20%7C%20Linux-2ea44f.svg?style=plastic)](#download-and-install)
[![GitHub Release](https://img.shields.io/github/v/release/TheRealSavi/iOpenPod?style=plastic&color=0a6fdb)](https://github.com/TheRealSavi/iOpenPod/releases/latest)
[![GitHub Stars](https://img.shields.io/github/stars/TheRealSavi/iOpenPod?style=plastic&color=6e5494)](https://github.com/TheRealSavi/iOpenPod/stargazers)
[![PyPI Downloads](https://img.shields.io/pypi/dm/iopenpod?style=plastic&color=0a6fdb&cacheSeconds=86400)](https://pypi.org/project/iopenpod/)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=plastic)](https://discord.gg/9Yy499Tf5d)

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/johngibbons)

iOpenPod is a free, cross-platform iPod manager and iTunes alternative for Linux, macOS, and Windows enabling FLAC to ALAC auto-conversion, iTunesDB metadata editing, and native podcast syncing. Built on Python and PyQt6, it allows you to browse and edit your iPod library, sync media from your PC, and seamlessly preserve iPod-specific database behaviors.

This branch also adds built-in tools for metadata repair, video downloads, and Tidal video syncing. Each tool feeds completed media into the normal iOpenPod sync workflow.

![Album Browser](docs/screenshots/hero.webp)

## Screenshots

| Sync Workflow | Library Editing | Device Tools |
| --- | --- | --- |
| ![Media folder and scan-type selection](docs/screenshots/syncmedia.webp) | ![Track artwork and iPod database editor](docs/screenshots/trackedits.webp) | ![Podcast subscription and episode manager](docs/screenshots/podcasts.webp) |
| ![Sync change and storage review](docs/screenshots/syncreview.webp) | ![Track playback options editor](docs/screenshots/trackedits2.webp) | ![Device-aware backup browser](docs/screenshots/backups.webp) |
| ![Individual sync change selection](docs/screenshots/syncreview2.webp) | ![Smart playlist rule editor](docs/screenshots/smartplaylists.webp) | ![Music and spoken-word transcoding settings](docs/screenshots/settings.webp) |

---

## Download and Install

Download the latest release for your platform. Native builds do not require any separate Python installation.

PyPI installs are recommended over native builds, but they all work the same.

### [Latest Native Release Builds](https://github.com/TheRealSavi/iOpenPod/releases/latest)

Need setup help? Use the [Install Help and Troubleshooting page](https://therealsavi.github.io/iOpenPod/install-help.html).

### Install from PyPI (Recommended)

iOpenPod is available through `pip`, `pipx`, and `uv tool`.

| Method | Install | Run | Upgrade |
| --- | --- | --- | --- |
| `pip` | `python -m pip install iopenpod` | `iopenpod` | `python -m pip install --upgrade iopenpod` |
| `pipx` | `pipx install iopenpod` | `iopenpod` | `pipx upgrade iopenpod` |
| `uv tool` | `uv tool install iopenpod` | `iopenpod` | `uv tool upgrade iopenpod` |

Requires **Python 3.11+**.

After installing, run:

```bash
iopenpod
```

If `iopenpod` is not on your PATH yet, run `pipx ensurepath` for `pipx` or `uv tool update-shell` for `uv tool`.

Update installs with the same tool you used to install them.

> **Required tools:** Install [FFmpeg](https://ffmpeg.org/) with `ffprobe` for transcoding and media probing, and [Chromaprint](https://acoustid.org/chromaprint) for acoustic fingerprinting during sync.
> **Linux desktop dependencies:** If iOpenPod throws a Qt `xcb` error or crashes when you press Ctrl, Alt, or Shift, install the XCB and XKeyboard packages listed on the [Install Help and Troubleshooting page](https://therealsavi.github.io/iOpenPod/install-help.html#helper-tools).
> **Linux iPod identification:** On first use, iOpenPod may ask you to run host-side udev setup commands. They publish only the Apple product serial and do not give the app raw-disk access. The iPod stays plugged in.

---

## How to Use

1. **Connect your iPod**. Make sure it is mounted as a drive.
2. **Select the device**. Pick the detected iPod in iOpenPod. If it shows up wrong, open an issue.
3. **Browse and edit**. Manage tracks, playlists, podcasts, artwork, and metadata.
4. **Sync**. Choose your media folders, review the changes, then apply.

---

## Built-in Tools and Plugin Layer

The fork groups optional tools under a small built-in plugin layer. The tools share iOpenPod's device state, cache, and sync handoff while keeping provider-specific code in its own package. Open them from the sidebar after you connect an iPod.

### Track Repair

Track Repair compares an album on the iPod with MusicBrainz or Apple iTunes metadata. Review proposed track-number, title, artist, album-artist, and disc-number changes before writing them to the iPod database. Loose matching can handle release annotations such as remastered-edition suffixes, while the default matcher avoids overwriting existing metadata without an explicit choice.

### Video Sync

Video Sync accepts a video URL, downloads it with [yt-dlp](https://github.com/yt-dlp/yt-dlp), and converts it with FFmpeg to an iPod-compatible H.264 Baseline video with AAC audio. The finished file enters the regular sync queue, so you can review it with the rest of your planned changes.

### Tidal Sync

Tidal Sync uses the `tiddl` command-line client for device-code sign-in, video search, and downloads. FFmpeg prepares the result for iPod playback, and the panel sends it through the same sync queue as Video Sync. Install `tiddl` separately when you want this tool:

```bash
pip install tiddl
```

The Tidal service and its account determine which videos the tool can access. Check the service's terms before downloading content.

### Device-specific audio compatibility

iOpenPod can apply an audio profile for older iPod decoders during sync. The current profile targets the 1st-generation iPod Mini, where some modern AAC bitstreams produce severe playback artifacts even though the files decode on a computer. iOpenPod converts those tracks to a conservative 160 kbps, 44.1 kHz AAC format and shows the conversion in Sync Review. The original files stay in your PC library.

---

## Core Features and Hardware Compatibility

iOpenPod supports the iPod Classic, iPod Mini, and iPod Nano from the 1st through 7th generations. Rockbox works when you enable the Rockbox compatibility settings.

### Automated FLAC Format Conversion and Transcoding

iOpenPod transcodes unsupported audio and video formats such as FLAC and OGG to iPod-compatible output with FFmpeg. Converted files can be cached so repeat syncs skip unchanged media.

### Managing Podcasts and Smart Playlists

The built-in podcast manager can search, subscribe, download episodes, and sync them to your iPod. You can also manage standard playlists and rule-based smart playlists.

### Acoustic Fingerprinting and Scrobbling

The sync engine matches tracks between your PC library and the iPod using acoustic fingerprints from Chromaprint. That keeps the same recording matched across re-encodes, format changes, and metadata edits. Play history can submit to ListenBrainz or Last.FM on sync.

### Drag and Drop

Files can be copied directly to the iPod by dragging them into the app, without using the full PC-folder sync workflow.

### Play Counts and Ratings

Play counts, ratings, and skip counts can be read from the iPod and synced back to the PC library metadata where supported.

### Embedded Album Artwork

Embedded or folder artwork is extracted, resized, and written to the iPod artwork database.

### Sync Review and Device Backups

Before writing changes, iOpenPod shows the planned additions, removals, metadata updates, and artwork changes. It also saves a device backup so you can roll back if needed.

### Customizable transcoding behavior

Settings are available for transcoding, allowing you to choose the encoder and quality for audio files. The settings adapt to the selected device and hide options that the hardware cannot use.

---

## Supported iPods

iOpenPod supports most iPods. iPod Shuffle support is planned; iPod Touch support is not planned, but may be possible in the future.

| Device | Status | Notes |
| --- | --- | --- |
| iPod "Classic" (all generations 1st-7th) | Supported | |
| iPod Mini (all generations 1st and 2nd) | Supported | |
| iPod Nano (all generations 1st-7th) | Supported | |
| iPod Shuffle | Planned | Shuffle uses a different DB Structure. ETA ~4 mo |
| iPod Touch | Not planned | Touch requires accessing the device through non file-system protocols |

---

## For Contributing Developers

To run iOpenPod from source, clone the repository and use `uv sync`.

### Prerequisites

- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **[FFmpeg](https://ffmpeg.org/)** with `ffprobe` (for transcoding and media probing)
- **[Chromaprint](https://acoustid.org/chromaprint)** (for fingerprinting)

### Setup

```bash
git clone https://github.com/TheRealSavi/iOpenPod.git
cd iOpenPod
uv sync
uv run iopenpod
```

`uv sync` installs dependencies into a local virtual environment.

### Dev checks

All lint, format, typecheck, and test commands go through one entry point:

```bash
uv run python scripts/dev.py check    # full gate
uv run python scripts/dev.py lint     # ruff
uv run python scripts/dev.py fmt      # ruff format
uv run python scripts/dev.py types    # mypy
uv run python scripts/dev.py test     # pytest
```

See `AGENTS.md` for the full command reference (including architecture checks and agent conventions).

Full human guide: [`docs/DEV.md`](docs/DEV.md).

### Plugin layout

Built-in tools live under [`src/iopenpod/plugins`](src/iopenpod/plugins). Each tool keeps its panel, background work, provider or downloader integration, and media preparation code together. The main window exposes each panel through a small sidebar signal and routes completed files into the existing sync handoff.

To add another built-in tool:

1. Create a package under `src/iopenpod/plugins/<name>`.
2. Keep network, subprocess, and media work off the Qt main thread.
3. Export the panel from the package's `__init__.py`.
4. Add the sidebar signal, page shell, and device-state updates in the main window.
5. Add focused tests for matching, provider parsing, or transcoding behavior.

### Contributing

Useful contributions include:

- Hardware testing on different iPod models
- macOS and Linux testing
- Bug reports with steps to reproduce and `iopenpod.log`
- Focused pull requests for documented issues
- Joining the discord to coordinate

To find logs in iOpenPod, open **Settings > Storage**, then click **Open** next to **Log Location**.

Please open an issue before starting major changes, or use the [Discord server](https://discord.gg/9Yy499Tf5d) to discuss implementation details.

### Related Projects

- [libgpod](https://github.com/gtkpod/libgpod) - C library for iPod database access (the reference implementation this project learned from)
- [gtkpod](https://github.com/gtkpod/gtkpod) - GTK+ iPod manager
- [Rockbox](https://www.rockbox.org/) - Open-source firmware replacement for iPods

## Migrating from Legacy Tools & iTunes Alternatives

iOpenPod is built as a modern, actively maintained replacement for abandoned iPod management software like **gtkpod**, **YamiPod**, **CopyPod**, and **EphPod**.

If you are migrating from other open-source iTunes alternatives like **Strawberry Music Player** or **Clementine**, iOpenPod resolves several common device syncing issues:

- **No `libgpod` dependency errors:** Other players rely on the abandoned `libgpod` C library. This often results in "compiled without libgpod support" warnings on Linux, or "unsupported checksum" database corruption when syncing 6th-generation Classics and modern Nanos. iOpenPod utilizes a custom, pure-Python parsing engine to bypass these legacy limitations and safely write modern database checksums.
- **100% Free on all platforms:** Unlike some alternatives that lock their Windows and macOS builds behind monthly sponsorships or paywalls, iOpenPod provides fully featured native releases for Linux, Windows, and macOS completely free of charge.
- **Safe Syncing:** Our pre-sync change review and automatic device snapshot backups ensure your iPod's database is never accidentally corrupted or wiped during a transfer.

---

## Star History
  <!-- markdownlint-disable MD033 -->

<a href="https://www.star-history.com/?repos=therealsavi%2Fiopenpod&type=timeline&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=therealsavi/iopenpod&type=timeline&theme=dark&legend=top-left&sealed_token=aMCIa4pbVoIhlxj9qK6fW6xT1G5WPmVyftpcUHMTDCF-jNcb-ZD5ewReZkCnZxjUqpEYILAoYH1UP1nYDNyqT1PbhUaA09JI1Lrq1EZ2-mO9bYPn3EWaHyBxmimY3pGYha3MHx1aNeAXRuF0UoijWcDkCgvNBHYDbZNCLG6zG8wx6tDuSh8cmrTN0uas" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=therealsavi/iopenpod&type=timeline&legend=top-left&sealed_token=aMCIa4pbVoIhlxj9qK6fW6xT1G5WPmVyftpcUHMTDCF-jNcb-ZD5ewReZkCnZxjUqpEYILAoYH1UP1nYDNyqT1PbhUaA09JI1Lrq1EZ2-mO9bYPn3EWaHyBxmimY3pGYha3MHx1aNeAXRuF0UoijWcDkCgvNBHYDbZNCLG6zG8wx6tDuSh8cmrTN0uas" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=therealsavi/iopenpod&type=timeline&legend=top-left&sealed_token=aMCIa4pbVoIhlxj9qK6fW6xT1G5WPmVyftpcUHMTDCF-jNcb-ZD5ewReZkCnZxjUqpEYILAoYH1UP1nYDNyqT1PbhUaA09JI1Lrq1EZ2-mO9bYPn3EWaHyBxmimY3pGYha3MHx1aNeAXRuF0UoijWcDkCgvNBHYDbZNCLG6zG8wx6tDuSh8cmrTN0uas" />
 </picture>
</a>

 <!-- markdownlint-enable MD033 -->
---

## Support

iOpenPod is free and open source. Donations are optional and help support development.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/johngibbons)

## License

MIT. See [LICENSE](LICENSE).
