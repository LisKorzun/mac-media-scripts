#!/usr/bin/env python3
# Rename one mp3 and write that same name into its title. Sound is copied.
import importlib.util
import json
import os
import subprocess
import sys
import unicodedata


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def join_mp3():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "join-mp3.py")
    spec = importlib.util.spec_from_file_location("join_mp3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def nfc(text):
    return unicodedata.normalize("NFC", text or "")


def files():
    return join_mp3().listing()


def chosen(number):
    names = files()
    if not number.isdigit():
        die("Type the number on the left.")
    index = int(number)
    if index < 1 or index > len(names):
        die("No line %s. The list has %s files." % (index, len(names)))
    return names[index - 1]


def tag_title(path):
    raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=title", "-of", "json", path],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    tags = (json.loads(raw).get("format") or {}).get("tags") or {}
    for key, value in tags.items():
        if key.casefold() == "title":
            return nfc(value)
    return ""


def duration(path):
    raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return float(raw.strip())


def has_picture(path):
    raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    for stream in json.loads(raw).get("streams") or []:
        if stream.get("codec_type") == "video":
            return True
    return False


def clean_name(typed):
    name = nfc(typed).strip()
    if not name or name.startswith(".") or os.path.basename(name) != name:
        die("Type a file name.")
    if "/" in name or name.endswith("."):
        die("Type a file name.")
    if name.lower().endswith(".mp3"):
        name = name[:-4].strip()
    if not name:
        die("Type a file name.")
    return name + ".mp3"


def cmd_show():
    name = chosen(sys.argv[2] if len(sys.argv) > 2 else "")
    print("file\t%s" % nfc(name))
    print("title\t%s" % tag_title(name))


def cmd_write():
    if len(sys.argv) < 4:
        die("Tell it the line number and the new name.")
    old = chosen(sys.argv[2])
    new = clean_name(" ".join(sys.argv[3:]))
    title = nfc(os.path.splitext(new)[0])
    if os.path.exists(new) and os.path.abspath(new) != os.path.abspath(old):
        die("A file named \"%s\" is already here." % new)
    before = duration(old)
    picture = has_picture(old)
    partial = old + ".building"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-i", old, "-map", "0:a:0",
    ]
    if picture:
        cmd += ["-map", "0:v:0", "-disposition:v:0", "attached_pic"]
    cmd += [
        "-c", "copy", "-map_metadata", "0", "-id3v2_version", "4",
        "-metadata", "title=%s" % title,
        "-metadata", "title-sort=%s" % title,
        "-f", "mp3", partial,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 or not os.path.isfile(partial):
            die(result.stderr.strip() or "ffmpeg failed")
        if abs(duration(partial) - before) >= 0.05 or tag_title(partial) != title:
            die("The name was not written.")
        if picture and not has_picture(partial):
            die("The picture was lost.")
        os.replace(partial, old)
        subprocess.run(["xattr", "-c", old], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if nfc(old) != new:
            os.rename(old, new)
    except SystemExit:
        if os.path.isfile(partial):
            os.remove(partial)
        raise
    print("file\t%s" % new)
    print("title\t%s" % title)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"list", "show", "write"}:
        die("Use list, show, or write.")
    if sys.argv[1] == "list":
        for name in files():
            print(nfc(name))
        return
    if sys.argv[1] == "show":
        cmd_show()
        return
    cmd_write()


if __name__ == "__main__":
    main()
