#!/usr/bin/env python3
# Put one narrator into an already packed book. Audio is copied, not re-encoded.
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

DOT = " · "
BOOK_FILE = re.compile(r"^(?P<author>.+?) · (?P<body>.+) · (?P<year>\d{4})$")
BRACKET = re.compile(r"^(?P<title>.+?) \[(?P<narrator>[^\]]+)\]$")


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def nfc(text):
    return unicodedata.normalize("NFC", text or "")


def book_audio():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "book-audio.py")
    spec_name = "book_audio"
    if spec_name in sys.modules:
        return sys.modules[spec_name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(spec_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        die(result.stderr.strip() or "command failed")


def capture(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if result.returncode != 0:
        die("command failed")
    return result.stdout.strip()


def clear_xattr(path):
    subprocess.run(["xattr", "-c", path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def m4b_files(folder):
    found = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.casefold().endswith(".m4b"):
            found.append(path)
    found.sort(key=lambda path: nfc(os.path.basename(path)).casefold())
    return found


def parse_book(path):
    stem = stem_of(os.path.basename(path))
    match = BOOK_FILE.fullmatch(stem)
    if not match:
        die("The book file needs a year: Author · Title · 1856.m4b")
    body = match.group("body").strip()
    bracket = BRACKET.fullmatch(body)
    if bracket:
        title = bracket.group("title").strip()
        narrator = bracket.group("narrator").strip()
    else:
        title = body
        narrator = ""
    if not title:
        die("The book file has no title")
    return {
        "author": match.group("author").strip(),
        "title": title,
        "narrator": narrator,
        "year": match.group("year"),
        "path": path,
    }


def fold(text):
    return nfc(text).casefold()


def stem_of(name):
    # "Толстой Л.Н." has dots. Only a real audio extension is cut off.
    base = nfc(name)
    for ext in (".m4b", ".mp3", ".m4a"):
        if base.casefold().endswith(ext):
            return base[: -len(ext)]
    return base


def split_name(name):
    # "Автор · Название [Чтец] · год" keeps the author. "Название [Чтец]" does not.
    stem = stem_of(name)
    match = BOOK_FILE.fullmatch(stem)
    if not match:
        bracket = BRACKET.fullmatch(stem)
        if bracket:
            return {"title": bracket.group("title").strip(), "narrator": bracket.group("narrator").strip(), "long": False}
        return {"title": stem, "narrator": "", "long": False}
    body = match.group("body").strip()
    bracket = BRACKET.fullmatch(body)
    if bracket:
        title = bracket.group("title").strip()
        narrator = bracket.group("narrator").strip()
    else:
        title = body
        narrator = ""
    return {
        "author": match.group("author").strip(),
        "title": title,
        "narrator": narrator,
        "year": match.group("year"),
        "long": True,
    }


def same_work(left, info):
    if not left.get("long"):
        return fold(left.get("title", "")) == fold(info["title"])
    return (
        fold(left.get("author", "")) == fold(info["author"])
        and fold(left.get("title", "")) == fold(info["title"])
        and left.get("year") == info["year"]
    )


def mp3_dir(folder, info):
    stem = stem_of(os.path.basename(info["path"]))
    exact = os.path.join(folder, stem)
    if os.path.isdir(exact):
        return exact
    found = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if not os.path.isdir(path) or not mp3s_in(path):
            continue
        if same_work(split_name(name), info):
            return path
        found.append(path)
    if len(found) == 1:
        return found[0]
    return ""


def mp3s_in(folder):
    found = []
    if not folder:
        return found
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.casefold().endswith(".mp3"):
            found.append(path)
    found.sort(key=lambda path: nfc(os.path.basename(path)).casefold())
    return found


def locate():
    folder = os.getcwd()
    books = m4b_files(folder)
    if not books:
        parent = os.path.dirname(folder)
        books = m4b_files(parent)
        if len(books) == 1:
            folder = parent
        else:
            die("No audiobook in this folder. Run this next to the .m4b file.")
    if len(books) > 1:
        die("This folder has more than one .m4b file.")
    parsed = parse_book(books[0])
    copies = mp3_dir(folder, parsed)
    parsed["folder"] = folder
    parsed["copies"] = copies
    parsed["mp3"] = mp3s_in(copies)
    if not parsed["mp3"]:
        parsed["mp3"] = mp3s_in(folder)
    return parsed


def file_name(info, narrator):
    return "%s%s%s [%s]%s%s.m4b" % (
        info["author"], DOT, info["title"], narrator, DOT, info["year"],
    )


def folder_name(info, narrator):
    # A folder that already starts with the author keeps that form.
    current = split_name(os.path.basename(info["folder"]))
    if current.get("long") and same_work(current, info):
        return stem_of(file_name(info, narrator))
    return "%s [%s]" % (info["title"], narrator)


def same_path(left, right):
    return nfc(os.path.basename(left)).casefold() == nfc(os.path.basename(right)).casefold()


def audio_mark(path):
    raw = capture([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ])
    data = json.loads(raw)
    if not data.get("streams"):
        die("No audio stream: %s" % os.path.basename(path))
    stream = data["streams"][0]
    return (
        stream.get("codec_name"),
        str(stream.get("sample_rate")),
        str(stream.get("channels")),
        float(data["format"]["duration"]),
    )


def same_audio(before, after):
    return before[:3] == after[:3] and abs(before[3] - after[3]) < 0.05


def chapter_titles(path):
    raw = capture([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", path,
    ])
    data = json.loads(raw)
    return [nfc((chapter.get("tags") or {}).get("title") or "") for chapter in data.get("chapters") or []]


def has_picture(path):
    raw = capture(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path])
    for stream in json.loads(raw).get("streams") or []:
        if stream.get("codec_type") == "video" and (stream.get("disposition") or {}).get("attached_pic"):
            return True
    return False


def tag_value(path, key):
    raw = capture([
        "ffprobe", "-v", "error", "-show_entries", "format_tags=%s" % key, "-of", "json", path,
    ])
    tags = (json.loads(raw).get("format") or {}).get("tags") or {}
    for name, value in tags.items():
        if name.casefold() == key.casefold():
            return nfc(value)
    return ""


def cover_len(path):
    result = subprocess.run(
        ["exiftool", "-b", "-CoverArt", path],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return len(result.stdout)


def cmd_plan():
    info = locate()
    print("folder\t%s" % nfc(os.path.basename(info["folder"])))
    print("file\t%s" % nfc(os.path.basename(info["path"])))
    print("narrator\t%s" % info["narrator"])
    if info["copies"]:
        print("dir\t%s" % nfc(os.path.basename(info["copies"])))
    print("mp3\t%d" % len(info["mp3"]))


def chosen_artist():
    text, kind = book_audio().artist_label(" ".join(sys.argv[2:]).strip() or os.environ.get("BOOK_NARRATOR", ""))
    if not kind:
        die("Type one word, such as Сборник, or a name: Имя Фамилия.")
    return text, kind


def cmd_name():
    text, kind = book_audio().artist_label(" ".join(sys.argv[2:]))
    if not kind:
        sys.exit(1)
    print(text)


def cmd_preview():
    info = locate()
    text, kind = chosen_artist()
    audio = book_audio()
    shown = audio.surname_given(text) if kind == "person" else text
    print("folder\t%s" % folder_name(info, text))
    print("file\t%s" % file_name(info, text))
    if info["copies"]:
        print("dir\t%s" % stem_of(file_name(info, text)))
    print("reader\t%s" % shown)
    print("kind\t%s" % kind)


def write_m4b(path, person, person_sort):
    before_audio = audio_mark(path)
    before_chapters = chapter_titles(path)
    before_cover = cover_len(path)
    backup = path + ".narrator-bak"
    shutil.copy2(path, backup)
    try:
        run([
            "exiftool", "-q", "-m", "-overwrite_original", "-api", "LargeFileSupport=1",
            "-ItemList:Artist=%s" % person,
            "-ItemList:AlbumArtist=%s" % person,
            "-ItemList:Performer=%s" % person,
            "-ItemList:Narrator=%s" % person,
            "-ItemList:Composer=%s" % person,
            "-ItemList:SortArtist=%s" % person_sort,
            "-ItemList:SortAlbumArtist=%s" % person_sort,
            "-ItemList:SortComposer=%s" % person_sort,
            "-XMP:Artist=%s" % person,
            "-XMP:Composer=%s" % person,
            path,
        ])
        if not same_audio(before_audio, audio_mark(path)) or chapter_titles(path) != before_chapters:
            die("The narrator changed more than the name: %s" % os.path.basename(path))
        if cover_len(path) != before_cover:
            die("The cover changed: %s" % os.path.basename(path))
        if tag_value(path, "artist") != person:
            die("The narrator was not written: %s" % os.path.basename(path))
        clear_xattr(path)
    except SystemExit:
        os.replace(backup, path)
        raise
    os.remove(backup)


def write_mp3(path, person, person_sort):
    before_audio = audio_mark(path)
    before_title = tag_value(path, "title")
    picture = has_picture(path)
    partial = path + ".building"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-i", path, "-map", "0:a:0",
    ]
    if picture:
        cmd += ["-map", "0:v:0", "-disposition:v:0", "attached_pic"]
    cmd += [
        "-c", "copy", "-map_metadata", "0", "-id3v2_version", "4",
        "-metadata", "artist=%s" % person,
        "-metadata", "album_artist=%s" % person,
        "-metadata", "narrator=%s" % person,
        "-metadata", "composer=%s" % person,
        "-metadata", "performer=%s" % person,
        "-metadata", "artist-sort=%s" % person_sort,
        "-f", "mp3", partial,
    ]
    try:
        run(cmd)
        if not same_audio(before_audio, audio_mark(partial)):
            die("The narrator changed more than the name: %s" % os.path.basename(path))
        if tag_value(partial, "title") != before_title or tag_value(partial, "artist") != person:
            die("The narrator was not written: %s" % os.path.basename(path))
        if picture and not has_picture(partial):
            die("The cover changed: %s" % os.path.basename(path))
        os.replace(partial, path)
        clear_xattr(path)
    except SystemExit:
        if os.path.isfile(partial):
            os.remove(partial)
        raise


def require_free(path, current):
    if os.path.exists(path) and not same_path(path, current):
        die("A file named \"%s\" is already here." % os.path.basename(path))


def cmd_write():
    info = locate()
    text, kind = chosen_artist()
    audio = book_audio()
    if kind == "person":
        person = audio.surname_given(text)
        person_sort = audio.reader_sort(text, False)
    else:
        person = text
        person_sort = text
    new_file = os.path.join(info["folder"], file_name(info, text))
    new_dir = ""
    if info["copies"]:
        new_dir = os.path.join(info["folder"], stem_of(file_name(info, text)))
    new_folder = os.path.join(os.path.dirname(info["folder"]), folder_name(info, text))
    require_free(new_file, info["path"])
    if new_dir:
        require_free(new_dir, info["copies"])
    require_free(new_folder, info["folder"])
    print(nfc(os.path.basename(info["path"])), flush=True)
    write_m4b(info["path"], person, person_sort)
    if kind == "person":
        total = len(info["mp3"])
        for index, path in enumerate(info["mp3"], 1):
            print("[%d/%d] %s" % (index, total, nfc(os.path.basename(path))), flush=True)
            write_mp3(path, person, person_sort)
    elif info["mp3"]:
        print("Each file keeps its reader.")
    if not same_path(info["path"], new_file):
        os.rename(info["path"], new_file)
    if new_dir and not same_path(info["copies"], new_dir):
        os.rename(info["copies"], new_dir)
    if not same_path(info["folder"], new_folder):
        os.rename(info["folder"], new_folder)
    print("Folder  %s" % nfc(os.path.basename(new_folder)))
    print("File    %s" % nfc(os.path.basename(new_file)))
    if new_dir:
        print("MP3     %s" % nfc(os.path.basename(new_dir)))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"plan", "name", "preview", "write"}:
        die("usage: set-narrator.py plan|name|preview|write")
    action = sys.argv[1]
    if action == "plan":
        cmd_plan()
    elif action == "name":
        cmd_name()
    elif action == "preview":
        cmd_preview()
    else:
        cmd_write()


if __name__ == "__main__":
    main()
