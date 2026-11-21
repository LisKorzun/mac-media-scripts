#!/usr/bin/env python3
# One folder of chapter audio -> an Apple Books M4B, or tagged MP3 copies.
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac"}
M4B_RATE = "96k"
MP3_RATE = "128k"
# A chapter name already stored in the file. The filename is not one of these.
LABEL_KEYS = ("title", "subtitle", "description", "comment", "synopsis")


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def sort_key(name):
    parts = [p.strip(",.") for p in name.split() if p.strip(",.")]
    if not parts:
        return ""
    first = parts[0]
    if "." in first or len(first) <= 2:
        for token in reversed(parts):
            if "." not in token and len(token) > 2:
                return token
        return parts[-1]
    return first


def last_word(name):
    parts = [p.strip(",.") for p in name.split() if p.strip(",.")]
    return parts[-1] if parts else ""


def natural_key(name):
    chunks = re.split(r"(\d+)", name.casefold())
    out = []
    for chunk in chunks:
        out.append(int(chunk) if chunk.isdigit() else chunk)
    return out


def chapters(folder):
    found = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in AUDIO_EXT:
            continue
        found.append(path)
    found.sort(key=lambda path: natural_key(os.path.basename(path)))
    return found


def guess_folder(name):
    match = re.match(
        r"^(?P<author>.+?)\s+[-–—]\s+(?P<title>.+?)(?:\s*\[(?P<narrator>[^\]]+)\])?\s*$",
        name,
    )
    if not match:
        return {"title": "", "author": "", "narrator": ""}
    return {
        "title": match.group("title").strip(),
        "author": match.group("author").strip(),
        "narrator": (match.group("narrator") or "").strip(),
    }


def safe_filename(title):
    name = title.replace("/", " ").replace(":", " ")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "book"


def meta_escape(text):
    return "".join("\\" + ch if ch in "\\=;#\n\r" else ch for ch in text)


def run(cmd):
    return subprocess.run(cmd, check=False).returncode


def capture(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


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


def track_tags(path):
    raw = capture([
        "ffprobe", "-v", "error",
        "-show_entries", "format_tags",
        "-of", "json", path,
    ])
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    tags = data.get("format", {}).get("tags") or {}
    return {str(key).casefold(): str(value).strip() for key, value in tags.items()}


def chapter_label(path, index, reserved):
    stem = os.path.splitext(os.path.basename(path))[0]
    skip = {stem.casefold()}
    for word in reserved:
        text = (word or "").strip()
        if text:
            skip.add(text.casefold())
    tags = track_tags(path)
    for key in LABEL_KEYS:
        text = tags.get(key, "")
        if text and text.casefold() not in skip:
            return text
    return str(index)


def duration_ms(path):
    text = capture([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        path,
    ])
    try:
        return max(0, int(round(float(text) * 1000)))
    except ValueError:
        die("No duration: %s" % os.path.basename(path))


def same_audio(paths):
    first = audio_info(paths[0])
    for path in paths[1:]:
        info = audio_info(path)
        if info != first:
            die(
                "%s is %s Hz %s ch %s; the first file is %s Hz %s ch %s"
                % (
                    os.path.basename(path), info[1], info[2], info[0],
                    first[1], first[2], first[0],
                )
            )
    return first


def clear_xattr(path):
    subprocess.run(["xattr", "-c", path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def env(name, fallback=""):
    return os.environ.get(name, fallback).strip()


def book_fields():
    title = env("BOOK_TITLE")
    author = env("BOOK_AUTHOR")
    narrator = env("BOOK_NARRATOR")
    genre = env("BOOK_GENRE") or "Audiobook"
    author_sort = env("BOOK_SORT") or sort_key(author)
    if not title or not author:
        die("Book title and author are required")
    return title, author, author_sort, narrator, genre


def cmd_guess():
    folder = os.getcwd()
    files = chapters(folder)
    if not files:
        die("No audio files in %s" % folder)
    guessed = guess_folder(os.path.basename(folder))
    reencode = 0
    for path in files:
        if audio_info(path)[0] != "mp3":
            reencode += 1
    rows = [
        ("count", str(len(files))),
        ("first", os.path.basename(files[0])),
        ("last", os.path.basename(files[-1])),
        ("title", guessed["title"]),
        ("author", guessed["author"]),
        ("sort", sort_key(guessed["author"])),
        ("narrator", guessed["narrator"]),
        ("reencode", str(reencode)),
    ]
    for key, value in rows:
        print("%s\t%s" % (key, value))


def cmd_sort_key():
    print(sort_key(" ".join(sys.argv[2:])))


def cmd_files():
    files = chapters(os.getcwd())
    if not files:
        die("No audio files in %s" % os.getcwd())
    for index, path in enumerate(files, 1):
        print("%d\t%s" % (index, os.path.basename(path)))


def write_m4b(folder, files, title, author, author_sort, narrator, genre):
    info = same_audio(files)
    out_name = safe_filename(title) + ".m4b"
    final = os.path.join(folder, out_name)
    durations = [duration_ms(path) for path in files]
    cursor = 0
    lines = [
        ";FFMETADATA1",
        "title=%s" % meta_escape(title),
        "album=%s" % meta_escape(title),
        "artist=%s" % meta_escape(author),
        "album_artist=%s" % meta_escape(author),
        "genre=%s" % meta_escape(genre),
    ]
    if narrator:
        lines.append("composer=%s" % meta_escape(narrator))
    reserved = (title, author, narrator, genre)
    for index, (path, length) in enumerate(zip(files, durations), 1):
        end = cursor + length
        lines.append("")
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append("START=%d" % cursor)
        lines.append("END=%d" % end)
        lines.append("title=%s" % meta_escape(chapter_label(path, index, reserved)))
        cursor = end
    concat = []
    for path in files:
        escaped = os.path.abspath(path).replace("'", r"'\''")
        concat.append("file '%s'" % escaped)
    encoder = "aac_at" if has_encoder("aac_at") else "aac"
    with tempfile.TemporaryDirectory() as tmp:
        meta_path = os.path.join(tmp, "meta.txt")
        list_path = os.path.join(tmp, "list.txt")
        with open(meta_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        with open(list_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(concat) + "\n")
        partial = os.path.join(tmp, "book.m4a")
        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-stats", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-i", meta_path,
            "-map", "0:a:0", "-map_metadata", "1",
            "-c:a", encoder, "-b:a", M4B_RATE, "-ac", "2", "-ar", info[1],
            "-movflags", "+faststart",
            "-metadata", "media_type=2",
            "-f", "ipod", partial,
        ]
        print("Encoding %s (%s stereo %s)" % (out_name, encoder, M4B_RATE), flush=True)
        rc = run(cmd)
        if rc != 0 or not os.path.isfile(partial):
            die("ffmpeg failed")
        shutil.move(partial, final)
    clear_xattr(final)
    if shutil.which("exiftool"):
        sort_cmd = [
            "exiftool", "-overwrite_original",
            "-ItemList:SortArtist=%s" % author_sort,
            "-ItemList:SortAlbumArtist=%s" % author_sort,
            "-ItemList:SortName=%s" % title,
            "-ItemList:SortAlbum=%s" % title,
        ]
        if narrator:
            sort_cmd.append("-ItemList:SortComposer=%s" % last_word(narrator))
        sort_cmd.append(final)
        if run(sort_cmd) != 0:
            die("exiftool could not write sort fields")
        clear_xattr(final)
    else:
        print("exiftool not found: author sort was not written", file=sys.stderr)
    print(out_name, flush=True)


def output_name(index, total, stem):
    width = max(2, len(str(total)))
    return "%0*d · %s.mp3" % (width, index, stem)


def write_mp3(folder, files, title, author, author_sort, narrator, genre):
    dest_dir = os.path.join(folder, "MP3")
    os.makedirs(dest_dir, exist_ok=True)
    for name in os.listdir(dest_dir):
        if name.startswith("."):
            continue
        path = os.path.join(dest_dir, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() == ".mp3":
            os.remove(path)
    total = len(files)
    failed = []
    for index, path in enumerate(files, 1):
        stem = os.path.splitext(os.path.basename(path))[0]
        dest = os.path.join(dest_dir, output_name(index, total, stem))
        codec = audio_info(path)[0]
        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-i", path, "-map", "0:a:0",
        ]
        if codec == "mp3":
            cmd += ["-c", "copy"]
        else:
            cmd += ["-c:a", "libmp3lame", "-b:a", MP3_RATE, "-ac", "2", "-ar", "44100"]
        cmd += [
            "-id3v2_version", "4",
            "-metadata", "title=%s" % stem,
            "-metadata", "album=%s" % title,
            "-metadata", "artist=%s" % author,
            "-metadata", "album_artist=%s" % author,
            "-metadata", "artist-sort=%s" % author_sort,
            "-metadata", "album-sort=%s" % title,
            "-metadata", "title-sort=%s" % stem,
            "-metadata", "track=%d/%d" % (index, total),
            "-metadata", "genre=%s" % genre,
        ]
        if narrator:
            cmd += [
                "-metadata", "composer=%s" % narrator,
                "-metadata", "performer=%s" % narrator,
            ]
        cmd.append(dest)
        print("[%d/%d] %s" % (index, total, os.path.basename(dest)), flush=True)
        if run(cmd) != 0:
            failed.append(os.path.basename(path))
            if os.path.isfile(dest):
                os.remove(dest)
            continue
        clear_xattr(dest)
    if failed:
        die("Failed: %s" % ", ".join(failed))
    print("MP3/%s" % output_name(1, total, os.path.splitext(os.path.basename(files[0]))[0]))


def has_encoder(name):
    text = capture(["ffmpeg", "-hide_banner", "-encoders"])
    return bool(re.search(r"\b%s\b" % re.escape(name), text))


def main():
    if len(sys.argv) < 2:
        die("usage: book-audio.py guess|files|sort-key|m4b|mp3")
    action = sys.argv[1]
    if action == "guess":
        cmd_guess()
        return
    if action == "files":
        cmd_files()
        return
    if action == "sort-key":
        cmd_sort_key()
        return
    if action not in {"m4b", "mp3"}:
        die("unknown action: %s" % action)
    folder = os.getcwd()
    files = chapters(folder)
    if not files:
        die("No audio files in %s" % folder)
    title, author, author_sort, narrator, genre = book_fields()
    if action == "m4b":
        write_m4b(folder, files, title, author, author_sort, narrator, genre)
    else:
        write_mp3(folder, files, title, author, author_sort, narrator, genre)


if __name__ == "__main__":
    main()
