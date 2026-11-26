#!/usr/bin/env python3
# Facts for pack-books: which folders are books, and the names they should get.
import importlib.util
import os
import re
import sys
import unicodedata

ABOUT = "· Об авторе"
SOURCES = "· исходники"
SKIP = {ABOUT, SOURCES}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".heif"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac"}

_audio = None


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def audio():
    global _audio
    if _audio is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "book-audio.py")
        spec = importlib.util.spec_from_file_location("book_audio", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _audio = module
    return _audio


def nfc(text):
    return unicodedata.normalize("NFC", text)


def short_author(full):
    parts = [part for part in full.split() if part]
    if len(parts) < 2:
        return full.strip()
    initials = []
    for part in parts[1:]:
        if "." in part:
            initials.append(part if part.endswith(".") else part + ".")
        else:
            initials.append(part[0].upper() + ".")
    return parts[0] + " " + "".join(initials)


def image_names(folder):
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        stem, ext = os.path.splitext(name)
        if ext.lower() not in IMAGE_EXTS:
            continue
        if stem.casefold() == "cover":
            continue
        found.append(name)
    found.sort(key=lambda item: item.casefold())
    return found


def pick_image(names):
    for name in names:
        if os.path.splitext(name)[0].casefold() == "portrait":
            return name
    if len(names) == 1:
        return names[0]
    return ""


def folder_facts(folder):
    loose = []
    m4bs = []
    copies = 0
    cover = False
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        ext = os.path.splitext(name)[1].lower()
        if os.path.isfile(path):
            if ext in AUDIO_EXTS:
                loose.append(name)
            elif ext == ".m4b":
                m4bs.append(name)
            elif name.casefold() == "cover.jpg":
                cover = True
        elif os.path.isdir(path) and name not in SKIP:
            count = audio_count(path)
            if count > copies:
                copies = count
    loose.sort(key=audio().natural_key)
    pictures = image_names(folder)
    return {
        "loose": loose,
        "m4b": m4bs,
        "copies": copies,
        "cover": cover,
        "pictures": pictures,
        "picked": pick_image(pictures),
    }


def audio_count(folder):
    count = 0
    try:
        children = os.listdir(folder)
    except OSError:
        return 0
    for child in children:
        if child.startswith("."):
            continue
        if os.path.splitext(child)[1].lower() not in AUDIO_EXTS:
            continue
        if os.path.isfile(os.path.join(folder, child)):
            count += 1
    return count


def is_packed(facts):
    loose = len(facts["loose"])
    copies = facts["copies"]
    if not facts["m4b"]:
        return False
    if loose == 0:
        return copies > 0
    return copies == loose


def parse_work(name):
    guessed = audio().guess_folder(name)
    if guessed["title"]:
        return guessed["title"], guessed["narrator"]
    match = re.fullmatch(r"(.+?)\s*\[([^\]]+)\]", name.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    plain = audio().folder_label(name).strip()
    if plain:
        return plain, ""
    return "", ""


def list_works(folder):
    rows = []
    try:
        names = os.listdir(folder)
    except OSError as error:
        die("Cannot read this folder (%s)" % error)
    for name in names:
        if name.startswith(".") or name in SKIP:
            continue
        path = os.path.join(folder, name)
        if not os.path.isdir(path):
            continue
        title, narrator = parse_work(name)
        rows.append((name, title, narrator, folder_facts(path)))
    rows.sort(key=lambda row: nfc(row[0]).casefold())
    return rows


def cmd_where():
    folder = os.getcwd()
    facts = folder_facts(folder)
    works = list_works(folder)
    if facts["m4b"] or (facts["loose"] and not works):
        print("book")
    elif works:
        print("author")
    else:
        print("empty")


def cmd_works():
    for name, title, narrator, facts in list_works(os.getcwd()):
        packed = "yes" if is_packed(facts) else "no"
        cover = "yes" if facts["cover"] else "no"
        # A tab is whitespace, so an empty narrator would vanish and the file
        # count would land in the narrator field. Unit separator keeps the gap.
        print("\x1f".join([
            name,
            title,
            narrator,
            str(len(facts["loose"])),
            packed,
            cover,
            str(len(facts["pictures"])),
            facts["picked"],
        ]))


def shared_rows(folder):
    rows = [("root", name) for name in image_names(folder)]
    about = os.path.join(folder, ABOUT)
    if os.path.isdir(about):
        rows.extend(("about", name) for name in image_names(about))
    return rows


def shared_choice(rows):
    for place, name in rows:
        if place == "about" and os.path.splitext(name)[0].casefold() == "portrait":
            return place, name
    for place, name in rows:
        if os.path.splitext(name)[0].casefold() == "portrait":
            return place, name
    if len(rows) == 1:
        return rows[0]
    if rows:
        return rows[0]
    return "", ""


def cmd_images():
    rows = shared_rows(os.getcwd())
    chosen = shared_choice(rows)
    for place, name in rows:
        flag = "yes" if (place, name) == chosen else "no"
        print("%s\t%s\t%s" % (place, name, flag))


def cmd_pictures():
    if len(sys.argv) != 3:
        die("usage: pack-books.py pictures FOLDER")
    for name in image_names(sys.argv[2]):
        print(name)


def cmd_sources():
    for name in folder_facts(os.getcwd())["loose"]:
        print(name)


def cmd_short():
    print(short_author(os.environ.get("BOOK_AUTHOR", "")))


def cmd_preview():
    title = os.environ.get("BOOK_TITLE", "").strip()
    author = os.environ.get("BOOK_AUTHOR", "").strip()
    narrator = os.environ.get("BOOK_NARRATOR", "").strip()
    year = os.environ.get("BOOK_YEAR", "").strip()
    if not title or not author or not year:
        die("Need a title, author and year")
    module = audio()
    folder = "%s [%s]" % (title, narrator) if narrator else title
    print("folder\t%s" % module.safe_filename(folder))
    print("file\t%s.m4b" % module.book_filename(author, title, narrator, year))
    print("reader\t%s" % (module.surname_given(narrator) if narrator else ""))


def cmd_place():
    if len(sys.argv) != 3:
        die("usage: pack-books.py place CURRENT")
    current = sys.argv[2]
    title = os.environ.get("BOOK_TITLE", "").strip()
    narrator = os.environ.get("BOOK_NARRATOR", "").strip()
    if not title:
        die("Need a title")
    target = "%s [%s]" % (title, narrator) if narrator else title
    target = audio().safe_filename(target)
    want = nfc(target)
    found = ""
    for name in os.listdir(os.getcwd()):
        path = os.path.join(os.getcwd(), name)
        if os.path.isdir(path) and nfc(name) == want:
            found = name
            break
    if found:
        if nfc(found) == nfc(current):
            print("keep\t%s" % found)
        else:
            print("clash\t%s" % found)
        return
    print("rename\t%s" % target)


def main():
    if len(sys.argv) < 2:
        die("usage: pack-books.py where|works|images|pictures|sources|short|preview|place")
    action = sys.argv[1]
    if action == "where":
        cmd_where()
    elif action == "works":
        cmd_works()
    elif action == "images":
        cmd_images()
    elif action == "pictures":
        cmd_pictures()
    elif action == "sources":
        cmd_sources()
    elif action == "short":
        cmd_short()
    elif action == "preview":
        cmd_preview()
    elif action == "place":
        cmd_place()
    else:
        die("usage: pack-books.py where|works|images|pictures|sources|short|preview|place")


if __name__ == "__main__":
    main()
