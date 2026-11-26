#!/usr/bin/env python3
# Write one genre into an already packed book. Audio is copied, not re-encoded.
import os
import sys

GENRES = (
    "Философия",
    "Психология",
    "Физиология",
    "Менеджмент",
    "Рисование",
    "Программирование",
    "Литература",
)


def narrator():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "set-narrator.py")
    name = "set_narrator"
    if name in sys.modules:
        return sys.modules[name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def clean(text):
    text = narrator().nfc(" ".join((text or "").split()))
    if not text or any(char in text for char in "/:\\"):
        return ""
    if len(text) > 80:
        return ""
    return text


def chosen():
    raw = " ".join(sys.argv[2:]).strip() or os.environ.get("BOOK_GENRE", "")
    text = clean(raw)
    if not text:
        narrator().die("Type a genre word, without / or :.")
    return text


def cmd_list():
    for name in GENRES:
        print(name)


def cmd_word():
    text = clean(" ".join(sys.argv[2:]))
    if not text:
        sys.exit(1)
    print(text)


def cmd_plan():
    info = narrator().locate()
    sn = narrator()
    print("folder\t%s" % sn.nfc(os.path.basename(info["folder"])))
    print("file\t%s" % sn.nfc(os.path.basename(info["path"])))
    if info["copies"]:
        print("dir\t%s" % sn.nfc(os.path.basename(info["copies"])))
    print("mp3\t%d" % len(info["mp3"]))
    print("genre\t%s" % sn.tag_value(info["path"], "genre"))


def write_m4b(path, genre):
    sn = narrator()
    before_audio = sn.audio_mark(path)
    before_chapters = sn.chapter_titles(path)
    before_cover = sn.cover_len(path)
    backup = path + ".genre-bak"
    import shutil
    shutil.copy2(path, backup)
    try:
        sn.run([
            "exiftool", "-q", "-m", "-overwrite_original", "-api", "LargeFileSupport=1",
            "-ItemList:Genre=%s" % genre,
            path,
        ])
        if not sn.same_audio(before_audio, sn.audio_mark(path)) or sn.chapter_titles(path) != before_chapters:
            sn.die("The genre changed more than the word: %s" % os.path.basename(path))
        if sn.cover_len(path) != before_cover:
            sn.die("The cover changed: %s" % os.path.basename(path))
        if sn.tag_value(path, "genre") != genre:
            sn.die("The genre was not written: %s" % os.path.basename(path))
        sn.clear_xattr(path)
    except SystemExit:
        os.replace(backup, path)
        raise
    os.remove(backup)


def write_mp3(path, genre):
    sn = narrator()
    before_audio = sn.audio_mark(path)
    before_title = sn.tag_value(path, "title")
    picture = sn.has_picture(path)
    partial = path + ".building"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-i", path, "-map", "0:a:0",
    ]
    if picture:
        cmd += ["-map", "0:v:0", "-disposition:v:0", "attached_pic"]
    cmd += [
        "-c", "copy", "-map_metadata", "0", "-id3v2_version", "4",
        "-metadata", "genre=%s" % genre,
        "-f", "mp3", partial,
    ]
    try:
        sn.run(cmd)
        if not sn.same_audio(before_audio, sn.audio_mark(partial)):
            sn.die("The genre changed more than the word: %s" % os.path.basename(path))
        if sn.tag_value(partial, "title") != before_title or sn.tag_value(partial, "genre") != genre:
            sn.die("The genre was not written: %s" % os.path.basename(path))
        if picture and not sn.has_picture(partial):
            sn.die("The cover changed: %s" % os.path.basename(path))
        os.replace(partial, path)
        sn.clear_xattr(path)
    except SystemExit:
        if os.path.isfile(partial):
            os.remove(partial)
        raise


def cmd_write():
    genre = chosen()
    info = narrator().locate()
    print(narrator().nfc(os.path.basename(info["path"])), flush=True)
    write_m4b(info["path"], genre)
    total = len(info["mp3"])
    for index, path in enumerate(info["mp3"], 1):
        print("[%d/%d] %s" % (index, total, narrator().nfc(os.path.basename(path))), flush=True)
        write_mp3(path, genre)
    print("Genre  %s" % genre)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"list", "word", "plan", "write"}:
        narrator().die("usage: set-genre.py list|word|plan|write")
    action = sys.argv[1]
    if action == "list":
        cmd_list()
    elif action == "word":
        cmd_word()
    elif action == "plan":
        cmd_plan()
    else:
        cmd_write()


if __name__ == "__main__":
    main()
