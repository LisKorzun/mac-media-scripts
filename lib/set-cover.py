#!/usr/bin/env python3
# Write cover.jpg into the .m4b and into the chapter folder of the same name. Audio is copied.
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MARKS = ("MediaType", "SortArtist", "SortAlbumArtist", "SortName", "SortAlbum", "SortComposer")
MP3_TAGS = (
    "Title", "Artist", "Album", "Band", "PerformerSortOrder", "AlbumSortOrder",
    "TitleSortOrder", "Track", "Genre", "Composer", "Conductor",
)


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def natural_key(name):
    chunks = re.split(r"(\d+)", name.casefold())
    return [int(chunk) if chunk.isdigit() else chunk for chunk in chunks]


def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        text = result.stderr.strip()
        die(text or "command failed")


def capture(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if result.returncode != 0:
        die("command failed")
    return result.stdout.strip()


def clear_xattr(path):
    subprocess.run(["xattr", "-c", path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def find_cover(folder):
    exact = os.path.join(folder, "cover.jpg")
    if os.path.isfile(exact):
        return exact
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        if name.casefold() == "cover.jpg" and os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
    return ""


def m4b_files(folder):
    found = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.casefold().endswith(".m4b"):
            found.append(path)
    found.sort(key=lambda path: natural_key(os.path.basename(path)))
    return found


def mp3s_in(folder):
    found = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.casefold().endswith(".mp3"):
            found.append(path)
    found.sort(key=lambda path: natural_key(os.path.basename(path)))
    return found


def mp3_dirs(folder):
    stems = {os.path.splitext(os.path.basename(path))[0] for path in m4b_files(folder)}
    named = []
    legacy = []
    others = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if not os.path.isdir(path) or not mp3s_in(path):
            continue
        if name in stems:
            named.append(path)
        elif name == "MP3":
            legacy.append(path)
        else:
            others.append(path)
    chosen = named or legacy or others
    chosen.sort(key=lambda path: natural_key(os.path.basename(path)))
    return chosen


def mp3_files(folder):
    found = []
    for path in mp3_dirs(folder):
        found.extend(mp3s_in(path))
    return found


def jpeg_bytes(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:3] == b"\xff\xd8\xff":
        return data
    return None


def prepare_jpeg(src):
    data = jpeg_bytes(src)
    if data is not None:
        return data, src
    if not shutil.which("sips"):
        die("cover.jpg is not a jpeg, and sips is missing")
    handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    handle.close()
    run([
        "sips", "-s", "format", "jpeg", "-s", "formatOptions", "92",
        src, "--out", handle.name,
    ])
    data = jpeg_bytes(handle.name)
    if data is None:
        os.remove(handle.name)
        die("cover.jpg is not a jpeg")
    return data, handle.name


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


def chapter_count(path):
    text = capture([
        "ffprobe", "-v", "error", "-show_chapters", "-of", "csv=p=0", path,
    ])
    if not text:
        return 0
    return len(text.splitlines())


def m4b_marks(path):
    raw = capture(["exiftool", "-j", "-MediaType", "-SortArtist", "-SortAlbumArtist", "-SortName", "-SortAlbum", "-SortComposer", path])
    data = json.loads(raw)[0]
    return tuple(data.get(key) for key in MARKS)


def mp3_tags(path):
    raw = capture(["exiftool", "-j"] + ["-%s" % name for name in MP3_TAGS] + [path])
    data = json.loads(raw)[0]
    return tuple(data.get(name) for name in MP3_TAGS)


def picture_types(path):
    text = capture(["exiftool", "-s3", "-PictureType", path])
    if not text:
        return []
    return text.splitlines()


def cmd_plan():
    folder = os.getcwd()
    cover = find_cover(folder)
    print("cover\t%s" % (os.path.basename(cover) if cover else ""))
    for path in m4b_files(folder):
        print("m4b\t%s" % os.path.basename(path))
    for path in mp3_dirs(folder):
        print("dir\t%s" % os.path.basename(path))
    print("mp3\t%d" % len(mp3_files(folder)))


def write_m4b(path, jpeg_path, jpeg):
    before_audio = audio_mark(path)
    before_marks = m4b_marks(path)
    before_chapters = chapter_count(path)
    backup = path + ".cover-bak"
    shutil.copy2(path, backup)
    try:
        run([
            "exiftool", "-q", "-m", "-overwrite_original", "-api", "LargeFileSupport=1",
            "-CoverArt=", "-CoverArt<=%s" % jpeg_path, path,
        ])
        if not same_audio(before_audio, audio_mark(path)) or m4b_marks(path) != before_marks or chapter_count(path) != before_chapters:
            die("Cover changed more than the picture: %s" % os.path.basename(path))
        got = subprocess.run(["exiftool", "-b", "-CoverArt", path], check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        if got != jpeg:
            die("Cover was not written: %s" % os.path.basename(path))
        clear_xattr(path)
    except SystemExit:
        os.replace(backup, path)
        raise
    os.remove(backup)


def write_mp3(path, jpeg_path):
    before_audio = audio_mark(path)
    before_tags = mp3_tags(path)
    partial = path + ".building"
    try:
        run([
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-i", path, "-i", jpeg_path,
            "-map", "0:a:0", "-map", "1:v:0",
            "-c", "copy", "-id3v2_version", "4", "-map_metadata", "0",
            "-disposition:v:0", "attached_pic",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
            "-f", "mp3", partial,
        ])
        if not same_audio(before_audio, audio_mark(partial)) or mp3_tags(partial) != before_tags or picture_types(partial) != ["Front Cover"]:
            die("Cover changed more than the picture: %s" % os.path.basename(path))
        os.replace(partial, path)
        clear_xattr(path)
    except SystemExit:
        if os.path.isfile(partial):
            os.remove(partial)
        raise


def cmd_write():
    folder = os.getcwd()
    cover = find_cover(folder)
    if not cover:
        die("Put cover.jpg in this folder")
    books = m4b_files(folder)
    tracks = mp3_files(folder)
    if not books and not tracks:
        die("No .m4b and no chapter folder yet")
    jpeg, jpeg_path = prepare_jpeg(cover)
    extra = jpeg_path != cover
    failed = []
    try:
        for path in books:
            print(os.path.basename(path), flush=True)
            try:
                write_m4b(path, jpeg_path, jpeg)
            except SystemExit as exc:
                failed.append(os.path.basename(path))
                if exc.code not in (0, None, 1):
                    raise
        total = len(tracks)
        for index, path in enumerate(tracks, 1):
            print("[%d/%d] %s" % (index, total, os.path.basename(path)), flush=True)
            try:
                write_mp3(path, jpeg_path)
            except SystemExit:
                failed.append(os.path.basename(path))
    finally:
        if extra and os.path.isfile(jpeg_path):
            os.remove(jpeg_path)
    if failed:
        die("Failed: %s" % ", ".join(failed))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"plan", "write"}:
        die("usage: set-cover.py plan|write")
    if sys.argv[1] == "plan":
        cmd_plan()
    else:
        cmd_write()


if __name__ == "__main__":
    main()
