#!/usr/bin/env python3
# Join neighbouring MP3 files into one. Sound is copied. Later numbers move back.
import os
import re
import subprocess
import sys
import tempfile
import unicodedata

LEAD = re.compile(r"^(\d{1,3})(\s+)(.*)$")
# "02.mp3" has no space after the number. It is still file 2, next to "01 - text".
LEAD_KEY = re.compile(r"^(\d{1,3})(?:\s|\.|$)")
PART_TAIL = re.compile(
    r"(?:\s*[-–—]\s*|\s+)"
    r"(?:главы|глава|фрагменты|фрагмент|части|часть|притчи|притча|дни|день|"
    r"chapters|chapter|fragments|fragment|parts|part|days|day)"
    r"\s+\d{1,3}\s*$",
    re.IGNORECASE,
)
NUM_TAIL = re.compile(r"\s+\d{1,3}\s*$")
# The part number can sit before [Narrator] or (note): "Крестник 1 [Илья Бобылев]".
END_MARK = re.compile(r"\s*(\[[^\]]+\]|\([^)]+\))\s*$")


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def capture(cmd):
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return ""
    return raw.decode("utf-8", "replace").strip()


def clear_xattr(path):
    subprocess.run(
        ["xattr", "-c", path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def listing():
    names = []
    for name in os.listdir("."):
        if name.startswith(".") or not os.path.isfile(name):
            continue
        if os.path.splitext(name)[1].lower() != ".mp3":
            continue
        names.append(name)

    def key(name):
        stem = unicodedata.normalize("NFC", os.path.splitext(name)[0])
        match = LEAD_KEY.match(stem)
        if match:
            return (0, int(match.group(1)), stem.casefold())
        return (1, 0, stem.casefold())

    names.sort(key=key)
    if not names:
        die("No mp3 files in this folder.")
    return names


def parse_choice(text, count):
    text = (text or "").replace(",", " ").replace("–", "-").replace("—", "-")
    indexes = []
    if not text.strip():
        die("Type the numbers, for example 3-4.")
    for token in text.split():
        found = re.fullmatch(r"(\d+)-(\d+)", token)
        if found:
            start, end = int(found.group(1)), int(found.group(2))
            if start > end:
                start, end = end, start
            indexes.extend(range(start, end + 1))
            continue
        if re.fullmatch(r"\d+", token):
            indexes.append(int(token))
            continue
        die("Use numbers, for example 3-4 or 3 4 5.")
    if len(indexes) < 2:
        die("Pick at least two files.")
    if len(set(indexes)) != len(indexes):
        die("A file is listed twice.")
    ordered = sorted(indexes)
    if ordered != list(range(ordered[0], ordered[-1] + 1)):
        die("Pick files that are next to each other, for example 3-4.")
    for number in ordered:
        if number < 1 or number > count:
            die("No line %s. The list has %s files." % (number, count))
    return ordered


def rest_body(rest):
    text = unicodedata.normalize("NFC", rest or "").strip()
    marks = []
    while True:
        match = END_MARK.search(text)
        if not match:
            break
        marks.append(match.group(1))
        text = text[:match.start()].strip()
    marks.reverse()
    stripped = PART_TAIL.sub("", text).strip(" -–—")
    if stripped and stripped != text:
        text = stripped
    else:
        text = NUM_TAIL.sub("", text).strip()
    if marks:
        text = "%s %s" % (text, " ".join(marks))
    return text.strip()


def proposal_for(picked):
    bodies = []
    for name in picked:
        match = LEAD.match(os.path.splitext(name)[0])
        rest = match.group(3) if match else os.path.splitext(name)[0]
        bodies.append(rest_body(rest))
    if not bodies or len(set(bodies)) != 1 or not bodies[0]:
        return ""
    match = LEAD.match(os.path.splitext(picked[0])[0])
    if match:
        return unicodedata.normalize("NFC", "%s %s.mp3" % (match.group(1), bodies[0]))
    return unicodedata.normalize("NFC", "%s.mp3" % bodies[0])


def shifted_name(name, delta):
    stem, ext = os.path.splitext(name)
    match = LEAD.match(stem)
    if match:
        number = int(match.group(1)) - delta
        gap = match.group(2)
        rest = match.group(3)
        width = len(match.group(1))
    else:
        bare = re.fullmatch(r"(\d{1,3})", stem)
        if not bare:
            return None
        number = int(bare.group(1)) - delta
        gap = ""
        rest = ""
        width = len(bare.group(1))
    if number < 1:
        die("The number in %s would fall below 1." % name)
    return "%s%s%s%s" % (str(number).zfill(width), gap, rest, ext)


def audio_info(path):
    text = capture([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels",
        "-of", "default=nw=1:nk=1",
        path,
    ])
    lines = text.splitlines()
    if len(lines) < 3:
        die("No audio stream: %s" % os.path.basename(path))
    return lines[0], lines[1], lines[2]


def same_audio(names):
    first = audio_info(names[0])
    for name in names[1:]:
        info = audio_info(name)
        if info != first:
            die(
                "%s is %s %s Hz %s ch; the first file is %s %s Hz %s ch"
                % (name, info[0], info[1], info[2], first[0], first[1], first[2])
            )
    return first


def packet_count(path):
    text = capture([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-count_packets",
        "-show_entries", "stream=nb_read_packets",
        "-of", "default=nw=1:nk=1",
        path,
    ])
    try:
        return int(text)
    except ValueError:
        die("No audio frames: %s" % os.path.basename(path))


def duration_sec(path):
    text = capture([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        path,
    ])
    try:
        return float(text)
    except ValueError:
        die("No duration: %s" % os.path.basename(path))


def clock(seconds):
    whole = int(round(seconds))
    minutes, secs = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%d h %d min %d sec" % (hours, minutes, secs)
    return "%d min %d sec" % (minutes, secs)


def check_names(files, picked, moves, out_name):
    deleted = set(picked)
    rename_from = {old: new for old, new in moves}
    finals = []
    for name in files:
        if name in deleted:
            continue
        finals.append(rename_from.get(name, name))
    if out_name:
        finals.append(out_name)
    if len(finals) != len(set(finals)):
        die("Two files would get the same name.")
    if out_name and out_name in set(files) and out_name not in deleted and out_name not in rename_from:
        die("%s already exists." % out_name)


def prepare(choice):
    files = listing()
    indexes = parse_choice(choice, len(files))
    picked = [files[index - 1] for index in indexes]
    last = indexes[-1]
    delta = len(picked) - 1
    moves = []
    keeps = []
    for name in files[last:]:
        new = shifted_name(name, delta)
        if new is None:
            keeps.append(name)
        elif new != name:
            moves.append((name, new))
    same_audio(picked)
    return files, picked, moves, keeps, proposal_for(picked)


def clean_name(typed, proposal):
    name = unicodedata.normalize("NFC", (typed or "").strip())
    if not name:
        name = proposal
    if not name:
        die("Type a file name.")
    if os.path.basename(name) != name or name.startswith("."):
        die("The name has to be one file in this folder.")
    if not name.lower().endswith(".mp3"):
        name += ".mp3"
    return name


def title_of(filename):
    stem = os.path.splitext(filename)[0]
    match = LEAD.match(stem)
    if match:
        return unicodedata.normalize("NFC", match.group(3).strip())
    return unicodedata.normalize("NFC", stem)


def concat(names, dest):
    lines = []
    for name in names:
        path = os.path.abspath(name).replace("'", r"'\''")
        lines.append("file '%s'" % path)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False)
    list_path = handle.name
    try:
        handle.write("\n".join(lines) + "\n")
        handle.close()
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-v", "fatal", "-y",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-map", "0:a:0", "-c", "copy",
                dest,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0 or not os.path.isfile(dest):
            tail = "\n".join(proc.stderr.splitlines()[-8:])
            die(tail or "ffmpeg failed")
    finally:
        if os.path.isfile(list_path):
            os.remove(list_path)


def write_title(source, dest, title):
    # The joined file has no tags yet. Copy the first file's tags, then set the title.
    if not title:
        return
    tagged = os.path.join(os.path.dirname(dest) or ".", ".join-mp3-tag.mp3")
    if os.path.exists(tagged):
        os.remove(tagged)
    before = packet_count(dest)
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "fatal", "-y",
            "-i", dest,
            "-i", source,
            "-map", "0:a:0",
            "-map_metadata", "1",
            "-c", "copy",
            "-id3v2_version", "3",
            "-metadata", "title=%s" % title,
            tagged,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0 or not os.path.isfile(tagged) or packet_count(tagged) != before:
        if os.path.exists(tagged):
            os.remove(tagged)
        print("The title was not written", file=sys.stderr)
        return
    os.replace(tagged, dest)


def restore(hidden):
    for tmp, original in reversed(hidden):
        if os.path.exists(tmp) and not os.path.exists(original):
            os.rename(tmp, original)


def emit_plan(choice):
    files, picked, moves, keeps, proposal = prepare(choice)
    check_names(files, picked, moves, "")
    total = sum(duration_sec(name) for name in picked)
    info = audio_info(picked[0])
    print("name\t%s" % proposal)
    print("shift\t%d" % (len(picked) - 1))
    print("length\t%s" % clock(total))
    print("audio\t%s\t%s\t%s" % info)
    for name in picked:
        print("take\t%s" % name)
    for old, new in moves:
        print("move\t%s\t%s" % (old, new))
    for name in keeps:
        print("keep\t%s" % name)


def join(choice, typed):
    files, picked, moves, _keeps, proposal = prepare(choice)
    out_name = clean_name(typed, proposal)
    check_names(files, picked, moves, out_name)
    folder = os.getcwd()
    audio_tmp = os.path.join(folder, ".join-mp3-out.mp3")
    out_path = os.path.join(folder, out_name)
    if os.path.exists(audio_tmp):
        os.remove(audio_tmp)
    src_hidden = []
    move_hidden = []
    placed = False
    try:
        concat(picked, audio_tmp)
        expected = sum(packet_count(name) for name in picked)
        got = packet_count(audio_tmp)
        # A few frames can drop at each seam. A missing file is thousands of frames.
        slack = max(8, 4 * (len(picked) - 1))
        if abs(got - expected) > slack:
            die("Joined file has %s frames, the parts have %s." % (got, expected))
        write_title(picked[0], audio_tmp, title_of(out_name))
        clear_xattr(audio_tmp)
        for index, name in enumerate(picked):
            original = os.path.join(folder, name)
            tmp = os.path.join(folder, ".join-mp3-src-%03d.mp3" % index)
            os.rename(original, tmp)
            src_hidden.append((tmp, original))
        for index, (old, new) in enumerate(moves):
            original = os.path.join(folder, old)
            tmp = os.path.join(folder, ".join-mp3-mov-%03d.mp3" % index)
            os.rename(original, tmp)
            move_hidden.append((tmp, original, os.path.join(folder, new)))
        os.rename(audio_tmp, out_path)
        placed = True
        for tmp, _original, dest in move_hidden:
            os.rename(tmp, dest)
        for tmp, _original in src_hidden:
            os.remove(tmp)
    except SystemExit:
        if not placed:
            if os.path.exists(audio_tmp):
                os.remove(audio_tmp)
            restore([(tmp, original) for tmp, original, _dest in move_hidden])
            restore(src_hidden)
        raise
    except Exception:
        if not placed and os.path.exists(audio_tmp):
            os.remove(audio_tmp)
        restore([(tmp, original) for tmp, original, _dest in move_hidden if os.path.exists(tmp)])
        restore(src_hidden)
        raise
    clear_xattr(out_path)
    print("file\t%s" % out_name)
    print("length\t%s" % clock(duration_sec(out_path)))


def main():
    if len(sys.argv) < 2:
        die("Use list, plan, or join.")
    cmd = sys.argv[1]
    if cmd == "list":
        for name in listing():
            print(name)
        return
    if cmd == "plan":
        if len(sys.argv) < 3:
            die("Tell it which files, for example 3-4.")
        emit_plan(sys.argv[2])
        return
    if cmd == "join":
        if len(sys.argv) < 4:
            die("Tell it which files and the new name.")
        join(sys.argv[2], sys.argv[3])
        return
    die("Unknown command.")


if __name__ == "__main__":
    main()
