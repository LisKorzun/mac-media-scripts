# mac-media-scripts

Personal macOS media scripts: interactive ffmpeg helpers for video and audio.

---

## Layout

| Path | Purpose |
| ---- | ------- |
| [`bin/`](bin/) | Executable scripts (*binaries* in Unix terms) |
| [`lib/`](lib/) | Shared code, if needed later |
| [`inprogress/`](inprogress/) | Media currently being processed; clear when done |
| [`docs/`](docs/) | Specs: `docs/<script-name>.txt` — see [`docs/to-mp4.txt`](docs/to-mp4.txt) |

---

## Requirements

### Required

- [Homebrew](https://brew.sh)
- **ffmpeg** (typically includes `ffprobe` and `ffplay`)

```bash
brew install ffmpeg
```

### Recommended on Apple silicon

**VideoToolbox** is macOS hardware (GPU) video encoding.

In ffmpeg it appears as encoders such as `h264_videotoolbox` / `hevc_videotoolbox`: usually faster and cooler than software `libx264`, with slightly different quality and compatibility. It is not installed separately — either present in your ffmpeg build or not (see checks below).

### Optional

- **mediainfo** — human-readable file info; `ffprobe` is enough for script menus

```bash
brew install mediainfo
```
---

## Verify in the terminal

```bash
which brew ffmpeg ffprobe ffplay
brew --version
ffmpeg -version
ffprobe -version
ffplay -version
brew list --versions ffmpeg
ffmpeg -hide_banner -encoders | grep videotoolbox
which mediainfo
mediainfo --version
```
