#!/usr/bin/env python3
# Glue an external srt or json onto a copy of the video. The source file stays.
import importlib.util
import json
import os
import re
import subprocess
import sys

C_RESET = "\033[0m"
C_HEAD = "\033[38;5;80m"
C_PICK = "\033[38;5;176m"
C_MUTED = "\033[38;5;245m"
C_PROMPT = "\033[38;5;252m"
SEP = "----------------------------------------"

VIDEO = {".mkv", ".mp4", ".m4v", ".mov"}
SPEED = (24000 / 1001) / 25
HOLD = 0.8
GAP = 0.08
SEASON_SHIFT = 0.6
JUNK = ("tvsubtitles.net", "ydy.com", "ytet", "finished by ls8", "vo+st")
EP = re.compile(r"S\d{2}E\d{2}", re.I)
BR = re.compile(r"<br\s*/?>", re.I)
FONT = re.compile(r"</?font[^>]*>", re.I)


def banner(title):
    print()
    print(f"{C_HEAD}{SEP}{C_RESET}")
    print(f"{C_HEAD}{title}{C_RESET}")
    print(f"{C_HEAD}{SEP}{C_RESET}")


def picked(text):
    print(f"{C_PICK}✓ {text}{C_RESET}")


def ask(prompt):
    print(f"{C_PROMPT}{prompt}{C_RESET} ", end="")
    return input()


def stamp(seconds):
    ms = int(round(max(0, seconds) * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def clock(seconds):
    tenths = int(round(max(0, seconds) * 10))
    secs, frac = divmod(tenths, 10)
    minutes, secs = divmod(secs, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}.{frac}"
    return f"{minutes}:{secs:02d}.{frac}"


def parse_time(text):
    text = text.strip().split()[0].replace(",", ".")
    hours, minutes, secs = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def parse_shift(text):
    text = text.strip().replace(",", ".")
    if text == "":
        return None
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", text):
        raise ValueError
    return float(text)


def clean_text(text):
    text = BR.sub("\n", text or "")
    text = FONT.sub("", text)
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def cues_from_srt(path):
    text = open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    cues = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start, end = lines[0].split("-->")
        body = "\n".join(lines[1:]).strip()
        if body:
            cues.append((parse_time(start), parse_time(end), body))
    return cues


def cues_from_json(path):
    data = json.load(open(path, encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    cues = []
    for item in items or []:
        body = clean_text(item.get("text") or "")
        if not body:
            continue
        cues.append((float(item.get("start") or 0), float(item.get("end") or 0), body))
    return cues


def load_cues(path):
    if path.lower().endswith(".json"):
        return cues_from_json(path)
    return cues_from_srt(path)


def prepare(cues, dur, speed, shift, hold):
    rows = []
    for start, end, text in cues:
        text = text.strip()
        if not text:
            continue
        if hold and any(bit in text.lower() for bit in JUNK):
            continue
        start *= speed
        end *= speed
        if start >= dur or end <= start:
            continue
        if end > dur:
            end = dur
        if end - start < 0.05:
            continue
        rows.append([start, end, text])
    out = []
    for index, (start, end, text) in enumerate(rows):
        new_start = start + shift
        new_end = end + shift
        if hold:
            if index + 1 < len(rows):
                room = rows[index + 1][0] + shift - GAP
                new_end = min(room, new_end + hold)
            else:
                new_end = min(dur, new_end + hold)
            if new_end < new_start + 0.30:
                new_end = new_start + 0.30
        if new_start < 0:
            new_start = 0
        if new_start >= dur or new_end <= new_start:
            continue
        new_end = min(new_end, dur)
        out.append((new_start, new_end, text))
    return out


def write_srt(path, cues):
    blocks = [
        f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(cues, 1)
    ]
    body = "\n".join(blocks)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


def videos():
    found = []
    for name in sorted(os.listdir(".")):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in VIDEO or not os.path.isfile(name):
            continue
        if stem.endswith((".eng", ".rus", ".building")):
            continue
        if stem.endswith(("_apple", "_lgc2")):
            continue
        found.append(name)
    return found


def subtitle_files(folder, prefix, exact):
    if not os.path.isdir(folder):
        return []
    hits = []
    for name in sorted(os.listdir(folder)):
        low = name.lower()
        if not low.endswith((".srt", ".json")):
            continue
        if low.endswith((".eng.srt", ".rus.srt")):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if exact:
            if os.path.splitext(name)[0] == prefix:
                hits.append(path)
        elif name.lower().startswith(prefix.lower()):
            hits.append(path)
    srts = [path for path in hits if path.lower().endswith(".srt")]
    return srts or hits


def find_subs(video):
    stem = os.path.splitext(os.path.basename(video))[0]
    episode = EP.search(stem)
    for folder in ("subs", "."):
        if episode:
            hits = subtitle_files(folder, episode.group(0), exact=False)
        else:
            hits = subtitle_files(folder, stem, exact=True)
        if hits:
            return hits
    return []


def probe(path):
    raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-show_streams", "-of", "json", path],
        text=True,
    )
    data = json.loads(raw)
    return float(data["format"]["duration"]), data["streams"]


def fps_of(streams):
    for stream in streams:
        if stream.get("codec_type") == "video" and stream.get("disposition", {}).get("attached_pic") != 1:
            return stream.get("r_frame_rate") or ""
    return ""


def first_text_sub(path, streams):
    index = None
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        if stream.get("codec_name") in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"):
            continue
        index = stream.get("index")
        break
    if index is None:
        return None
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", path, "-map", f"0:{index}", "-f", "srt", "-"],
        text=True, errors="replace",
    )
    cues = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n")):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start = parse_time(lines[0].split("-->")[0])
        text = " ".join(lines[1:]).strip()
        if text:
            cues.append((start, text))
            if len(cues) == 1:
                break
    return cues[0] if cues else None


def load_set_lang():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "set-lang.py")
    spec = importlib.util.spec_from_file_location("set_lang", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mux(video, srt, dest, lang, title, streams, audio_choice, sub_choice, set_lang):
    sub_count = sum(1 for stream in streams if stream.get("codec_type") == "subtitle")
    rows = list(streams)
    rows.append({
        "codec_type": "subtitle",
        "tags": {"language": lang, "title": title},
        "disposition": {},
    })
    tmp = dest + ".building" + os.path.splitext(dest)[1]
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "error", "-y",
        "-i", video, "-i", srt,
        "-map", "0", "-map", "1:0", "-c", "copy",
        "-max_interleave_delta", set_lang.INTERLEAVE,
    ]
    ext = os.path.splitext(dest)[1].lower()
    if ext in (".mp4", ".m4v", ".mov"):
        cmd += [f"-c:s:{sub_count}", "mov_text"]
    cmd += [
        f"-metadata:s:s:{sub_count}", f"language={lang}",
        f"-metadata:s:s:{sub_count}", f"title={title}",
    ]
    args, notes = set_lang.disposition_args(rows, audio_choice, sub_choice)
    for note in notes:
        print(note)
    cmd += args
    cmd.append(tmp)
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False
    os.replace(tmp, dest)
    return True


def parse_selection(raw, names):
    text = raw.strip()
    count = len(names)
    if text == "":
        return list(range(1, count + 1))
    season = re.fullmatch(r"s0*(\d{1,2})", text, re.I)
    if season:
        code = f"s{int(season.group(1)):02d}e"
        picked = [index for index, name in enumerate(names, 1) if code in name.lower()]
        return picked or None
    picked = []
    for piece in text.split():
        if not piece.isdigit() or not 1 <= int(piece) <= count:
            raise ValueError(piece)
        picked.append(int(piece))
    if not picked:
        raise ValueError("")
    return picked


def choose_numbers(names):
    count = len(names)
    while True:
        raw = ask(f"Numbers, s02 for one season, or Enter for all (1-{count}):")
        try:
            picked = parse_selection(raw, names)
        except ValueError as exc:
            print(f"Bad number: {exc}" if str(exc) else "Enter at least one number.")
            continue
        if picked is None:
            print("No videos of that season in the list.")
            continue
        return picked


def copy_folder(raw):
    name = raw.strip()
    if name == "":
        return None
    if name in (".", "..", "subs") or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError
    return name


def destinations(video, mark, folder):
    root, ext = os.path.splitext(video)
    base = os.path.basename(video)
    if folder:
        dest = os.path.join(folder, base)
    else:
        dest = f"{root}.{mark}{ext}"
    if os.path.abspath(dest) == os.path.abspath(video):
        return None
    return dest


def choose_one(paths):
    if len(paths) == 1:
        return paths[0]
    print("More than one subtitle file:")
    for index, path in enumerate(paths, 1):
        print(f"  {index}. {path}")
    while True:
        raw = ask(f"Which file? (1-{len(paths)}):")
        if raw.isdigit() and 1 <= int(raw) <= len(paths):
            return paths[int(raw) - 1]
        print("Bad number.")


def main():
    names = videos()
    if not names:
        print(f"No video files in {os.getcwd()}")
        return 1
    banner("Found video files:")
    for index, name in enumerate(names, 1):
        print(f"{C_PROMPT}{index}.{C_RESET} {name}")
    print(f"{C_HEAD}{SEP}{C_RESET}")
    print(f"{C_MUTED}Numbers (1 3 5), s02 for one season, or Enter for every video in this folder.{C_RESET}")
    numbers = choose_numbers(names)

    banner("How to time the subtitles")
    print(f"{C_MUTED}1  Glue the file as it is. For a movie.{C_RESET}")
    print(f"{C_MUTED}2  Fit a 25 fps video and shift. Season 2 used this.{C_RESET}")
    while True:
        mode = ask("Mode (1 or 2):").strip()
        if mode in ("1", "2"):
            break
        print("Enter 1 or 2.")
    fit = mode == "2"
    if fit:
        print(f"{C_MUTED}Plus shows the text later. Minus shows it earlier. Enter keeps {SEASON_SHIFT:g}.{C_RESET}")
        while True:
            try:
                shift = parse_shift(ask("Shift in seconds:"))
            except ValueError:
                print("Enter a number, for example 0.6 or -0.3.")
                continue
            if shift is None:
                shift = SEASON_SHIFT
            break
        hold = HOLD
    else:
        print(f"{C_MUTED}Enter keeps 0: the text stays on the times from the file.{C_RESET}")
        while True:
            try:
                shift = parse_shift(ask("Shift in seconds:"))
            except ValueError:
                print("Enter a number, for example 0.3 or -0.2.")
                continue
            if shift is None:
                shift = 0.0
            break
        hold = 0.0

    print(f"{C_MUTED}1 English, 2 Russian. Enter keeps English.{C_RESET}")
    while True:
        lang_pick = ask("Subtitle language:").strip().lower()
        if lang_pick in ("", "1", "en", "eng", "english"):
            lang, title, mark = "eng", "English", "eng"
            break
        if lang_pick in ("2", "ru", "rus", "russian"):
            lang, title, mark = "rus", "Russian", "rus"
            break
        print("Enter 1 or 2.")

    print(f"{C_MUTED}Enter keeps the copy next to the video, with .eng or .rus in the name.{C_RESET}")
    print(f"{C_MUTED}A folder name writes the same file name, without that mark.{C_RESET}")
    while True:
        try:
            folder = copy_folder(ask("Folder for copies:"))
        except ValueError:
            print("Use one folder name, for example ready.")
            continue
        break

    jobs = []
    for number in numbers:
        video = names[number - 1]
        hits = find_subs(video)
        if not hits:
            print(f"{video}: no subtitle file in subs/ or next to the video")
            continue
        sub = choose_one(hits)
        try:
            dur, streams = probe(video)
        except (subprocess.CalledProcessError, OSError, ValueError, KeyError):
            print(f"{video}: could not read the video")
            continue
        fps = fps_of(streams)
        speed = SPEED if fit and fps == "25/1" else 1.0
        try:
            cues = prepare(load_cues(sub), dur, speed, shift, hold)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            print(f"{sub}: could not read subtitles")
            continue
        if not cues:
            print(f"{sub}: no lines left after timing")
            continue
        dest = destinations(video, mark, folder)
        if dest is None:
            print(f"{video}: copy would replace the original, skipped")
            continue
        jobs.append({
            "video": video,
            "sub": sub,
            "cues": cues,
            "dur": dur,
            "streams": streams,
            "fps": fps,
            "speed": speed,
            "dest": dest,
            "inside": first_text_sub(video, streams),
        })

    if not jobs:
        picked("Nothing to add")
        return 0

    set_lang = load_set_lang()
    audio_choice, sub_choice = set_lang.ask_defaults(True, sys.stderr)

    banner("Preview")
    for job in jobs:
        print(job["video"])
        print(f"  from {job['sub']}")
        if fit and job["speed"] != 1:
            print(f"  25 fps, times × {job['speed']:.3f}, shift {shift:+g} s, pause {HOLD:g} s")
        elif fit:
            print(f"  {job['fps'] or 'unknown fps'}, times not sped up, shift {shift:+g} s, pause {HOLD:g} s")
        elif shift:
            print(f"  shift {shift:+g} s")
        else:
            print("  times unchanged")
        start, end, text = job["cues"][0]
        line = text.replace("\n", " | ")
        print(f"  {len(job['cues'])} lines, first at {clock(start)}: {line[:70]}")
        print(f"  last at {clock(job['cues'][-1][0])}, video {clock(job['dur'])}")
        if job["inside"]:
            rus_at, rus_text = job["inside"]
            print(f"  subtitle already in the video at {clock(rus_at)}: {rus_text[:50]}")
        print(f"  copy: {job['dest']}")
        rows = list(job["streams"])
        rows.append({
            "codec_type": "subtitle",
            "tags": {"language": lang, "title": title},
            "disposition": {},
        })
        for line in set_lang.describe(rows, audio_choice, sub_choice):
            print(f"  {line}")
        print(f"  {C_MUTED}{os.path.basename(job['video'])} stays as it is{C_RESET}")

    print()
    answer = ask("Write these copies? [Y/n]:").strip()
    if answer not in ("", "y", "Y"):
        print("Cancelled")
        return 0

    if folder:
        os.makedirs(folder, exist_ok=True)

    for job in jobs:
        print()
        print(job["video"])
        temp_srt = job["dest"] + ".building.srt"
        write_srt(temp_srt, job["cues"])
        ok = mux(
            job["video"], temp_srt, job["dest"], lang, title, job["streams"],
            audio_choice, sub_choice, set_lang,
        )
        if os.path.exists(temp_srt):
            os.remove(temp_srt)
        if not ok:
            print("  could not write the copy")
            continue
        print(f"  {job['dest']}")

    picked("Done")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(1)
