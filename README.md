# mac-media-scripts

Personal macOS media scripts: interactive ffmpeg helpers for video and audio.

---

## Layout


| Path | Purpose |
| ---- | ------- |
| [`bin/`](bin/) | Executable scripts |
| [`lib/`](lib/) | Shared code, partial files, if needed |
| [`inprogress/`](inprogress/) | Media currently being processed; clear when done |
| [`docs/`](docs/) | Specs: `docs/<script-name>.txt` |
| [`AI/`](AI/) | Context for assistants |

## Requirements

- [Homebrew](https://brew.sh)
- **ffmpeg** (typically includes `ffprobe` and `ffplay`)

```bash
brew install ffmpeg
```

##### Verify in the terminal

```bash
which brew ffmpeg ffprobe ffplay
brew --version
ffmpeg -version
ffprobe -version
ffplay -version
brew list --versions ffmpeg
ffmpeg -hide_banner -encoders | grep videotoolbox
```
