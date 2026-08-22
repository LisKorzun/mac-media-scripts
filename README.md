# mac-media-scripts

Personal macOS media scripts: interactive ffmpeg helpers for video and audio.

---

## Layout


| Path                         | Purpose                                          |
| ---------------------------- | ------------------------------------------------ |
| `[bin/](bin/)`               | Executable scripts                               |
| `[context/](context/)`       | Context for assistants                           |
| `[inprogress/](inprogress/)` | Media currently being processed; clear when done |
| `[lib/](lib/)`               | Shared code, if needed                           |
| `[specs/](specs/)`           | Script specifications                            |


## One-time setup (PATH)

Do this **once per Mac**. Typing `export PATH=...` only in Terminal does **not** persist — you must **edit and save** `~/.zshrc`.

1. Open the config file in an editor:
  ```bash
   nano ~/.zshrc
  ```
   (Or open `~/.zshrc` in Cursor / any text editor.)
2. Add this line (keep existing lines; place it after `$HOME/.local/bin` if you have one):
  ```bash
   export PATH="$HOME/DEV/scripts/bin:$PATH"
  ```
   Example — file after edit:
3. **Save the file.**
  In `nano`: Ctrl+O → Enter → Ctrl+X.
4. Confirm the line is on disk:
  ```bash
   cat ~/.zshrc
  ```
   You must see `DEV/scripts/bin` in the output.  
   (`cat` reads the file; your **current** Terminal window still has the old PATH until step 5.)
5. Apply the change in the **current** window:
  ```bash
   source ~/.zshrc
  ```
6. Check:
  ```bash
   which to-mp4
  ```
   Expected: `/Users/lis/DEV/scripts/bin/to-mp4`
   Or close Terminal and open a **new** window — new windows load `~/.zshrc` automatically; then run `which to-mp4` again.

All scripts in `[bin/](bin/)` work the same way after this (`to-mp4`, future tools, etc.).

## Running scripts

Scripts look for video files in **the folder you are in** (`cd` there first). Output is written next to the source file.

Example with `[to-mp4](specs/to-mp4.txt)`:

```bash
cd ~/DEV/scripts/inprogress
to-mp4
```

Use any folder that contains your video — `inprogress/` is optional.

Without PATH setup, use the full path instead:

```bash
cd ~/DEV/scripts/inprogress
~/DEV/scripts/bin/to-mp4
```

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
