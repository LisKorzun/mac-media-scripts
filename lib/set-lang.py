#!/usr/bin/env python3
# Choose which audio and subtitle language starts when a file opens.
# Video and audio are copied. Text subtitles are written back through the file
# so a player can show them when the track is selected.
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

C_RESET = "\033[0m"
C_HEAD = "\033[38;5;80m"
C_PICK = "\033[38;5;176m"
C_MUTED = "\033[38;5;245m"
C_PROMPT = "\033[38;5;252m"
SEP = "----------------------------------------"

VIDEO = {".mkv", ".mp4", ".m4v", ".mov"}
# Microseconds. Holds a cue until the picture reaches it, even in a long film.
INTERLEAVE = "28800000000"
IMAGE_SUBS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvb_subtitle",
    "xsub",
}
WORDS = {
    "keep": "stays",
    "none": "off",
    "eng": "English",
    "rus": "Russian",
}


def banner(title, stream):
    print(file=stream)
    print(f"{C_HEAD}{SEP}{C_RESET}", file=stream)
    print(f"{C_HEAD}{title}{C_RESET}", file=stream)
    print(f"{C_HEAD}{SEP}{C_RESET}", file=stream)


def picked(text, stream=None):
    print(f"{C_PICK}✓ {text}{C_RESET}", file=stream or sys.stdout)


def ask_line(prompt, stream):
    print(f"{C_PROMPT}{prompt}{C_RESET} ", end="", file=stream, flush=True)
    return input().strip()


def norm_lang(value):
    text = (value or "").strip().lower().replace("_", "-").split("-")[0]
    if text in ("en", "eng", "english"):
        return "eng"
    if text in ("ru", "rus", "russian"):
        return "rus"
    return text


def lang_word(stream):
    code = norm_lang((stream.get("tags") or {}).get("language"))
    return {"eng": "English", "rus": "Russian"}.get(code, code or "track")


def is_text_sub(stream):
    return (
        stream.get("codec_type") == "subtitle"
        and (stream.get("codec_name") or "") not in IMAGE_SUBS
    )


def flags_for(group, choice):
    if choice == "keep":
        return [bool((s.get("disposition") or {}).get("default")) for s in group]
    if choice == "none":
        return [False] * len(group)
    target = norm_lang(choice)
    flags = []
    used = False
    for stream in group:
        lang = norm_lang((stream.get("tags") or {}).get("language"))
        if not used and lang == target:
            flags.append(True)
            used = True
        else:
            flags.append(False)
    if not used:
        return None
    return flags


def disp_value(stream, default):
    names = []
    for name, on in (stream.get("disposition") or {}).items():
        if name == "default" or not on:
            continue
        names.append(name)
    if default:
        names.insert(0, "default")
    return "+".join(names) if names else "0"


def disposition_args(streams, audio_choice, sub_choice):
    args = []
    notes = []
    video_i = 0
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        flag = bool((stream.get("disposition") or {}).get("default"))
        args += ["-disposition:v:%d" % video_i, disp_value(stream, flag)]
        video_i += 1
    for kind, letter, choice in (
        ("audio", "a", audio_choice),
        ("subtitle", "s", sub_choice),
    ):
        group = [s for s in streams if s.get("codec_type") == kind]
        flags = flags_for(group, choice)
        if flags is None:
            notes.append(f"No {WORDS.get(choice, choice)} {kind}. It stays as it is.")
            flags = flags_for(group, "keep")
        index = 0
        for stream, flag in zip(group, flags):
            args += [f"-disposition:{letter}:{index}", disp_value(stream, flag)]
            index += 1
    return args, notes


def describe(streams, audio_choice, sub_choice):
    lines = []
    for kind, choice, label in (
        ("audio", audio_choice, "audio"),
        ("subtitle", sub_choice, "subtitles"),
    ):
        group = [s for s in streams if s.get("codec_type") == kind]
        now_names = [lang_word(s) for s in group if (s.get("disposition") or {}).get("default")]
        now = ", ".join(now_names) if now_names else "off"
        if choice == "keep":
            lines.append(f"{label}: {now}, stays")
            continue
        if choice == "none":
            lines.append(f"{label}: off")
            continue
        if flags_for(group, choice) is None:
            lines.append(f"{label}: no {WORDS[choice]}, stays {now}")
        else:
            lines.append(f"{label}: {WORDS[choice]}")
    return lines


def ask_defaults(new_file, stream):
    print(file=stream)
    print(f"{C_HEAD}Which audio starts when the file opens{C_RESET}", file=stream)
    print(f"{C_MUTED}1 English{C_RESET}", file=stream)
    print(f"{C_MUTED}2 Russian{C_RESET}", file=stream)
    print(f"{C_MUTED}Enter leaves the audio that plays now.{C_RESET}", file=stream)
    audio_table = {
        "": "keep",
        "1": "eng",
        "en": "eng",
        "eng": "eng",
        "english": "eng",
        "2": "rus",
        "ru": "rus",
        "rus": "rus",
        "russian": "rus",
    }
    while True:
        audio = audio_table.get(ask_line("Default audio:", stream).lower())
        if audio:
            break
        print("Enter 1, 2, or press Enter.", file=stream)

    print(file=stream)
    print(f"{C_HEAD}Which subtitles start when the file opens{C_RESET}", file=stream)
    print(f"{C_MUTED}1 English{C_RESET}", file=stream)
    print(f"{C_MUTED}2 Russian{C_RESET}", file=stream)
    print(f"{C_MUTED}3 None. You turn them on in the player.{C_RESET}", file=stream)
    if new_file:
        print(f"{C_MUTED}Enter starts with subtitles off.{C_RESET}", file=stream)
    else:
        print(f"{C_MUTED}Enter leaves subtitles as they are.{C_RESET}", file=stream)
    sub_table = dict(audio_table)
    sub_table.update({"3": "none", "none": "none", "off": "none"})
    if new_file:
        sub_table[""] = "none"
    while True:
        subs = sub_table.get(ask_line("Default subtitles:", stream).lower())
        if subs:
            break
        print("Enter 1, 2, 3, or press Enter.", file=stream)
    picked(f"Audio: {WORDS[audio]}. Subtitles: {WORDS[subs]}.", stream)
    return audio, subs


def probe_streams(path):
    raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
    )
    return json.loads(raw).get("streams") or []


def canon_audio(value):
    text = (value or "").strip().lower()
    table = {
        "keep": "keep",
        "": "keep",
        "eng": "eng",
        "en": "eng",
        "english": "eng",
        "1": "eng",
        "rus": "rus",
        "ru": "rus",
        "russian": "rus",
        "2": "rus",
    }
    if text not in table:
        raise ValueError(value)
    return table[text]


def canon_subs(value):
    text = (value or "").strip().lower()
    if text in ("none", "off", "3"):
        return "none"
    if text in ("keep", ""):
        return "keep"
    return canon_audio(text)


def apply(path, audio_choice, sub_choice):
    audio_choice = canon_audio(audio_choice)
    sub_choice = canon_subs(sub_choice)
    streams = probe_streams(path)
    ext = os.path.splitext(path)[1].lower()
    sub_codec = "mov_text" if ext in (".mp4", ".m4v", ".mov") else "srt"
    root, _ = os.path.splitext(path)
    out = root + ".building" + ext
    tmp = tempfile.mkdtemp(prefix="set-lang-")
    try:
        text_paths = []
        for stream in streams:
            if not is_text_sub(stream):
                continue
            dest = os.path.join(tmp, "%d.srt" % len(text_paths))
            subprocess.check_call(
                [
                    "ffmpeg", "-v", "error", "-y", "-i", path,
                    "-map", "0:%d" % stream["index"], "-f", "srt", dest,
                ]
            )
            text_paths.append(dest)
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", path]
        for extra in text_paths:
            cmd += ["-i", extra]
        cmd += ["-max_interleave_delta", INTERLEAVE, "-c", "copy"]
        text_i = 0
        sub_i = 0
        for stream in streams:
            if is_text_sub(stream):
                cmd += ["-map", "%d:0" % (text_i + 1)]
                cmd += ["-c:s:%d" % sub_i, sub_codec]
                tags = stream.get("tags") or {}
                lang = tags.get("language") or ""
                title = tags.get("title") or ""
                if lang:
                    cmd += ["-metadata:s:s:%d" % sub_i, "language=%s" % lang]
                if title:
                    cmd += ["-metadata:s:s:%d" % sub_i, "title=%s" % title]
                text_i += 1
                sub_i += 1
            else:
                cmd += ["-map", "0:%d" % stream["index"]]
                if stream.get("codec_type") == "subtitle":
                    sub_i += 1
        args, notes = disposition_args(streams, audio_choice, sub_choice)
        for note in notes:
            print(note, file=sys.stderr)
        cmd += args
        if ext in (".mp4", ".m4v", ".mov"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(out)
        subprocess.check_call(cmd)
        if len(probe_streams(out)) != len(streams):
            raise OSError("stream count changed")
        os.replace(out, path)
        return True
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError):
        return False
    finally:
        if os.path.exists(out):
            os.remove(out)
        shutil.rmtree(tmp, ignore_errors=True)


def videos():
    names = []
    for name in sorted(os.listdir(".")):
        if os.path.splitext(name)[1].lower() not in VIDEO:
            continue
        if ".building" in name or not os.path.isfile(name):
            continue
        names.append(name)
    return names


def parse_selection(raw, names):
    text = raw.strip()
    count = len(names)
    if text == "":
        return list(range(1, count + 1))
    season = re.fullmatch(r"s0*(\d{1,2})", text, re.I)
    if season:
        code = "s%02de" % int(season.group(1))
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


def interactive():
    names = videos()
    if not names:
        print("No video files in %s" % os.getcwd())
        return 1
    banner("Found video files:", sys.stdout)
    for index, name in enumerate(names, 1):
        print(f"{C_PROMPT}{index}.{C_RESET} {name}")
    print(f"{C_HEAD}{SEP}{C_RESET}")
    print(f"{C_MUTED}Numbers (1 3 5), s02 for one season, or Enter for every video in this folder.{C_RESET}")
    while True:
        try:
            picked_rows = parse_selection(
                ask_line(f"Numbers, s02 for one season, or Enter for all (1-{len(names)}):", sys.stdout),
                names,
            )
        except ValueError as exc:
            print("Bad number: %s" % exc if str(exc) else "Enter at least one number.")
            continue
        if picked_rows is None:
            print("No videos of that season in the list.")
            continue
        break

    audio, subs = ask_defaults(False, sys.stdout)
    jobs = []
    for number in picked_rows:
        path = names[number - 1]
        try:
            streams = probe_streams(path)
        except (subprocess.CalledProcessError, OSError, ValueError, KeyError):
            print("%s: could not read the video" % path)
            continue
        jobs.append((path, streams))
    if not jobs:
        picked("Nothing to change")
        return 0

    banner("Preview", sys.stdout)
    for path, streams in jobs:
        print(path)
        for line in describe(streams, audio, subs):
            print("  %s" % line)
        print(f"  {C_MUTED}picture and sound stay, the file is written again{C_RESET}")
    print()
    answer = ask_line("Write these files? [Y/n]:", sys.stdout).strip()
    if answer not in ("", "y", "Y"):
        print("Cancelled")
        return 0
    for path, _streams in jobs:
        print()
        print(path)
        if not apply(path, audio, subs):
            print("  could not write the file")
            continue
        print("  %s" % path)
    picked("Done")
    return 0


def self_test():
    rus = {"codec_type": "audio", "tags": {"language": "rus"}, "disposition": {"default": 1}}
    eng = {"codec_type": "audio", "tags": {"language": "eng"}, "disposition": {"default": 0, "original": 1}}
    sub = {"codec_type": "subtitle", "tags": {"language": "eng"}, "disposition": {"default": 1}}
    assert flags_for([rus, eng], "eng") == [False, True]
    assert flags_for([rus, eng], "keep") == [True, False]
    assert flags_for([rus], "eng") is None
    assert flags_for([sub], "none") == [False]
    args, notes = disposition_args([rus, eng, sub], "eng", "none")
    assert args[args.index("-disposition:a:1") + 1] == "default+original"
    assert args[args.index("-disposition:s:0") + 1] == "0"
    assert notes == []
    args, notes = disposition_args([rus], "eng", "keep")
    assert notes and args[1] == "default"
    assert canon_subs("off") == "none"
    assert canon_audio("english") == "eng"


def main(argv):
    if len(argv) >= 2 and argv[1] == "self-test":
        self_test()
        return 0
    if len(argv) >= 2 and argv[1] == "ask":
        audio, subs = ask_defaults(True, sys.stderr)
        print(audio, flush=True)
        print(subs, flush=True)
        return 0
    if len(argv) >= 5 and argv[1] == "apply":
        try:
            ok = apply(argv[2], argv[3], argv[4])
        except ValueError as exc:
            print("Bad choice: %s" % exc, file=sys.stderr)
            return 1
        return 0 if ok else 1
    if len(argv) > 1:
        print("Usage: set-lang", file=sys.stderr)
        return 1
    return interactive()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        print()
        sys.exit(1)
