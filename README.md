# mac-media-scripts

Personal macOS media scripts: interactive ffmpeg helpers for video and audio.

---

## Layout


| Path                         | Purpose                                          |
| ---------------------------- | ------------------------------------------------ |
| `[bin/](bin/)`               | Executable scripts                               |
| `[context/](context/)`       | Context for assistants                           |
| `[inprogress/](inprogress/)` | Media currently being processed; clear when done |
| `[lib/](lib/)`               | Shared shell (`fixed-profile-queue.sh` for to-apple / to-lg-tv) |
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

All scripts in `[bin/](bin/)` work the same way after this.

## Which script

Everyday path: **`to-apple`** for Mac/iPhone, **`to-lg-tv`** for the living-room TV. Same idea in both: pick file(s), pick audio/subtitle tracks, confirm once. Quality settings are fixed (small file, still watchable). **`to-mp4`** is the old general tool if you want to choose width/CRF yourself. **`cut-mp4`** only cuts a clip from an **already converted** mp4 (no second encode).

| Command | For | Output |
| ------- | --- | ------ |
| `[to-apple](specs/to-apple.txt)` | MacBook, iMac M1, iPhone 14 Pro Max | `*_apple.mp4` |
| `[to-lg-tv](specs/to-lg-tv.txt)` | LG OLED C2 (USB player) | `*_lgc2.mkv` |
| `[to-mp4](specs/to-mp4.txt)` | Experiment / one-off | `*_854_crf23.mp4` etc. |
| `[cut-mp4](specs/cut-mp4.txt)` | Short clips from a ready mp4 | `*_cut_*.mp4` |
| `[tailwind-clr](specs/tailwind-clr.txt)` | Color palette (not video) | `.clr` |

**`to-apple`** — HEVC 1080p + AAC + text subtitles in MP4. Plays in QuickTime / TV / Files on iPhone. One file or a queue (`1 3 5`): tracks for each film **before** encoding starts, then it runs sequentially. Use this for movies you will watch on Apple devices.

**`to-lg-tv`** — same queue UI, but MKV + HEVC + AAC + SRT (PGS copied). Built for USB on the C2 (DTS/TrueHD from rips often have no sound on that TV). Copy the `_lgc2.mkv` to an **exFAT** stick.

**`to-mp4`** — one file per run. You pick width and CRF; H.264 + AAC. Default 854px is smaller/faster and looks softer on the iMac than `to-apple`. Keep it when you want control, not as the daily Apple/TV profile.

**`cut-mp4`** — stream copy (fast). Times like `1:12:05`. Run it on `_apple.mp4`, not on the original MKV.

## Running scripts

Media scripts look for video in **the folder you are in** (`cd` there first). Output is written next to the source. `inprogress/` in this repo is optional — any folder with the files is fine.

```bash
cd ~/Movies/inprogress
to-apple                  # Mac / iPhone
to-lg-tv                  # TV USB
to-mp4                    # manual H.264
cut-mp4                   # clip from an already-converted mp4
```

1. List of videos in the current folder → type a number (`1`) or several (`1 3 5`).
2. For each chosen file: stream numbers (video + audio, optional subs), space-separated.
3. From the second file in a queue: **Same stream menu numbers as previous?** — `Y` if the rip layout matches.
4. Whole file or first N minutes (one choice for the whole queue).
5. Summary → `Y` / Enter to start.

Without PATH:

```bash
cd ~/Movies/inprogress
~/DEV/scripts/bin/to-apple
```

## Overnight / caffeinate

HEVC encode of a feature film can take hours. macOS may **sleep** and pause ffmpeg if the lid is closed or the display sleeps.

`caffeinate` is a built-in macOS command: “do not idle-sleep while this process is running.” You do **not** type `caffeinate -i to-apple` yourself.

After you confirm the queue, **`to-apple` and `to-lg-tv` start caffeinate automatically** (`-is`: idle + system sleep) until the script exits. You will see a line like *Keeping Mac awake until this script finishes*.

Still do this on a long overnight run:

- Plug the Mac in (power adapter).
- Leave the lid **open** if it is a laptop, or use an iMac. A **closed lid on battery** can still sleep; caffeinate does not always override that.
- Do not close the Terminal window that is converting; `Ctrl+C` stops the queue.

`to-mp4` / `cut-mp4` do not wrap caffeinate (they are usually shorter). For a long `to-mp4` you can still run `caffeinate -is to-mp4` yourself.

### Tailwind color list

`[tailwind-clr](specs/tailwind-clr.txt)` builds a macOS `.clr` from the current Tailwind theme (needs `curl`, `python3`, `swift`):

```bash
tailwind-clr              # → inprogress/tailwind.clr (review, then move)
tailwind-clr --install    # → ~/Library/Colors/Tailwind.clr
```

Do **not** double-click the `.clr` in Finder (macOS asks for an app). Open **Colors** anywhere → **Color Palettes** tab → **⋯** → **Open…** → pick the file. For a permanent list: `tailwind-clr --install` (or copy into `~/Library/Colors/`). Details: `[specs/tailwind-clr.txt](specs/tailwind-clr.txt)`.

## Requirements

- [Homebrew](https://brew.sh)
- **ffmpeg** (typically includes `ffprobe` and `ffplay`) — for media scripts

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
