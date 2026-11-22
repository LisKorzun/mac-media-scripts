#!/usr/bin/env python3
# One folder of chapter audio -> an Apple Books M4B, or tagged MP3 copies.
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac"}
M4B_RATE = "96k"
MP3_RATE = "128k"
# A chapter name already stored in the file. The filename is not one of these.
LABEL_KEYS = ("title", "subtitle", "description", "comment", "synopsis")
DOT = " · "
WRITING_WORD = re.compile(r"(написа\w*|созда\w*|wrote|written)", re.IGNORECASE)
YEAR_WORD = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b")


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


def book_heading(author, title, year):
    return "%s%s%s%s%s" % (author, DOT, title, DOT, year)


def book_filename(author, title, narrator, year):
    if narrator:
        return "%s%s%s [%s]%s%s" % (author, DOT, title, narrator, DOT, year)
    return book_heading(author, title, year)


def book_dirname(author, title, narrator, year):
    return safe_filename(book_filename(author, title, narrator, year))


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


def fold(text):
    return unicodedata.normalize("NFC", text or "").casefold()


def same_text(left, right):
    return unicodedata.normalize("NFC", left or "") == unicodedata.normalize("NFC", right or "")


def bare_index(text, index):
    token = text.strip()
    return token.isdigit() and int(token) == index


# "Читает …" stored as Windows-1251 and read as Latin-1 becomes "×èòàåò".
def repair_text(text):
    if not text or re.search(r"[А-Яа-яЁё]", text):
        return text
    try:
        fixed = text.encode("latin-1").decode("cp1251")
    except UnicodeError:
        return text
    letters = [ch for ch in fixed if ch.isalpha()]
    if not letters:
        return text
    cyrillic = [ch for ch in letters if re.match(r"[А-Яа-яЁё]", ch)]
    if len(cyrillic) * 2 >= len(letters):
        return fixed
    return text


def looks_broken(text):
    if repair_text(text) != text:
        return True
    if re.search(r"[А-Яа-яЁё]", text or ""):
        return False
    odd = re.findall(r"[\u00c0-\u00ff]", text or "")
    letters = re.findall(r"[A-Za-z\u00c0-\u00ff]", text or "")
    return len(letters) >= 4 and len(odd) * 2 >= len(letters)


def readable(text):
    text = repair_text(text)
    if looks_broken(text):
        die("The contents would show the wrong letters: %s" % text)
    return text


STRUCT_WORD = re.compile(
    r"(?i)(?<![0-9A-Za-zА-Яа-яЁё])(часть|глава|фрагмент|part|chapter|fragment)\s+([0-9]+|[IVXLCDM]+)"
)
CREDIT_WORD = re.compile(
    r"(?i)(?:читает|читал|читала|исполняет|начитыва\w*|озвучива\w*|narrated\s+by|read\s+by|narrator)\b"
)
STRUCT_NAMES = {
    "часть": "Часть",
    "глава": "Глава",
    "фрагмент": "Фрагмент",
    "part": "Part",
    "chapter": "Chapter",
    "fragment": "Fragment",
}


def structure_label(text):
    parts = []
    for match in STRUCT_WORD.finditer(text or ""):
        word = STRUCT_NAMES.get(match.group(1).casefold(), match.group(1).capitalize())
        number = match.group(2)
        if number.isdigit():
            number = str(int(number))
        else:
            number = number.upper()
        parts.append("%s %s" % (word, number))
    return ". ".join(parts)


def stem_body(stem):
    return re.sub(r"^\s*\d+\s*(?:[.\-–—]\s+|\s+)", "", stem or "").strip()


# 02-01 may sit at the start, in the middle, or at the end. A lone leading 01 is the file order.
NUMBER_GROUP = re.compile(r"(?<!\d)(\d{1,3}(?:[-._]\d{1,3})+)(?!\d)")


def number_groups(stem):
    body = stem_body(stem)
    if structure_label(stem):
        return [], ""
    found = NUMBER_GROUP.findall(body)
    if len(found) != 1:
        return [], ""
    sample = found[0]
    return re.split(r"[-._]", sample), sample


def chapter_labels():
    raw = os.environ.get("BOOK_CHAPTER_LABELS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def number_label(stem):
    groups, _sample = number_groups(stem)
    labels = chapter_labels()
    if not groups or len(groups) != len(labels):
        return ""
    parts = []
    for label, number in zip(labels, groups):
        shown = str(int(number)) if number.isdigit() else number
        parts.append("%s %s" % (label, shown))
    return ". ".join(parts)


def narrator_forms(narrator):
    narrator = (narrator or "").strip()
    if not narrator:
        return []
    forms = [narrator, surname_given(narrator)]
    parts = name_parts(narrator)
    if len(parts) >= 2:
        forms.append("%s %s" % (parts[0], parts[-1]))
        forms.append("%s %s" % (parts[-1], parts[0]))
    return forms


def is_credit(text, narrator):
    repaired = repair_text(text).strip()
    if not repaired:
        return False
    if CREDIT_WORD.match(repaired):
        return True
    blob = fold(repaired)
    for name in narrator_forms(narrator):
        folded = fold(name)
        if folded:
            blob = blob.replace(folded, " ")
    blob = CREDIT_WORD.sub(" ", blob)
    return re.sub(r"[^0-9a-zа-яё]+", "", blob) == ""


def chapter_description(path, index, reserved):
    stem = os.path.splitext(os.path.basename(path))[0]
    title = reserved[0] if reserved else ""
    narrator = reserved[3] if reserved and len(reserved) > 3 else ""
    book = fold(title)
    skip = {fold(stem)}
    for word in reserved:
        text = (word or "").strip()
        if text:
            skip.add(fold(text))
    tags = track_tags(path)
    for key in LABEL_KEYS:
        text = repair_text(tags.get(key, ""))
        found = structure_label(text)
        if found:
            return found
        if not text or fold(text) in skip or bare_index(text, index):
            continue
        match = re.match(r"^0*%d(?:\s*[.\-–—:]|\s+)\s*" % index, text)
        if match:
            text = text[match.end():].strip()
        if not text or bare_index(text, index):
            continue
        # "1886 - Смерть Ивана Ильича 1" repeats the book, it is not a chapter name.
        if book and book in fold(text):
            continue
        if is_credit(text, narrator) or looks_broken(text):
            continue
        return text
    return ""


def chapter_name(path, index, reserved):
    stem = repair_text(os.path.splitext(os.path.basename(path))[0])
    found = structure_label(stem) or number_label(stem)
    if found:
        return found
    return chapter_description(path, index, reserved)


def detect_pattern(files):
    labels = []
    tails = []
    for path in files:
        stem = repair_text(os.path.splitext(os.path.basename(path))[0])
        label = structure_label(stem)
        labels.append(label)
        tails.append(number_groups(stem))
    if labels and all(labels):
        return "words", labels[0], 0
    if any(labels):
        return "", "", 0
    counts = [len(groups) for groups, _sample in tails]
    if not counts or any(count < 2 for count in counts) or len(set(counts)) != 1:
        return "", "", 0
    return "numbers", tails[0][1], counts[0]


def contents_line(index, total, name, year, title=None):
    width = max(2, len(str(total)))
    number = "%0*d" % (width, index)
    shown = safe_filename(name)
    book = name if title is None else title
    # The year stays only beside the work title. A part or chapter has none.
    if fold(shown) == fold(safe_filename(book)):
        return "%s. %s%s%s" % (number, shown, DOT, year)
    return "%s. %s" % (number, shown)


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
    year = env("BOOK_YEAR")
    author_full = env("BOOK_AUTHOR_FULL") or author
    if not title or not author:
        die("Book title and author are required")
    if not re.fullmatch(r"\d{4}", year):
        die("Year is required")
    return title, author, author_full, narrator, genre, year


def wiki_search(query):
    params = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srlimit": "8",
        "srprop": "snippet",
    })
    request = urllib.request.Request(
        "https://ru.wikipedia.org/w/api.php?" + params,
        headers={"User-Agent": "mac-media-scripts/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        data = json.loads(response.read().decode())
    found = []
    for item in data.get("query", {}).get("search", []):
        plain = re.sub(r"<[^>]+>", "", item.get("snippet") or "")
        plain = plain.replace("&nbsp;", " ").replace("&quot;", '"').strip()
        found.append(((item.get("title") or "").strip(), plain))
    return found


def wiki_snippets(query):
    return [snippet for _title, snippet in wiki_search(query) if snippet]


def name_parts(name):
    return [part.strip(",.") for part in name.split() if part.strip(",.")]


def surname_given(name):
    parts = name_parts(name)
    if len(parts) < 2:
        return name.strip()
    return "%s %s" % (parts[-1], parts[0])


def author_full_name(author):
    surname = sort_key(author)
    if not surname:
        return ""
    try:
        hits = wiki_search(surname)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return ""
    best = ""
    for title, _snippet in hits:
        if "," not in title:
            continue
        plain = title.split("(")[0]
        plain = re.sub(r"\s+", " ", plain.replace(",", " ")).strip(" .")
        words = plain.split()
        if len(words) < 2 or len(words) > 4:
            continue
        if words[0].casefold() != surname.casefold():
            continue
        if any("." in word or not word[:1].isupper() for word in words):
            continue
        if len(words) > len(best.split()):
            best = plain
    return best


def cmd_author_full():
    print("full\t%s" % author_full_name(env("BOOK_AUTHOR")))


def year_written(author, title):
    title_key = title.casefold()
    queries = ['"%s" %s' % (title, author), "%s %s" % (title, author)]
    try:
        snippets = []
        for query in queries:
            snippets.extend(wiki_snippets(query))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return ""
    hits = []
    for plain in snippets:
        years = list(YEAR_WORD.finditer(plain))
        for index, match in enumerate(years):
            next_start = years[index + 1].start() if index + 1 < len(years) else len(plain)
            forward = plain[match.start():next_start]
            before = plain[max(0, match.start() - 25):match.start()]
            if title_key not in forward.casefold() and title_key not in before.casefold():
                continue
            if not WRITING_WORD.search(forward) and not WRITING_WORD.search(before):
                continue
            # A year only borrows the previous sentence when the verb or the title
            # actually sits next to this year, not entirely before it.
            if title_key in forward.casefold() or WRITING_WORD.search(forward):
                hits.append(match.group(1))
    if not hits:
        return ""
    return max(set(hits), key=hits.count)


def cmd_year():
    year = year_written(env("BOOK_AUTHOR"), env("BOOK_TITLE"))
    print("year\t%s" % year)
    print("note\t%s" % ("written" if year else ""))


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


def cmd_pattern():
    files = chapters(os.getcwd())
    if not files:
        die("No audio files in %s" % os.getcwd())
    kind, value, groups = detect_pattern(files)
    if kind == "words":
        print("words\t%s" % value)
    elif kind == "numbers":
        print("sample\t%s" % value)
        print("groups\t%d" % groups)


def cmd_contents():
    files = chapters(os.getcwd())
    if not files:
        die("No audio files in %s" % os.getcwd())
    title = env("BOOK_TITLE")
    year = env("BOOK_YEAR")
    narrator = env("BOOK_NARRATOR")
    if not title or not re.fullmatch(r"\d{4}", year or ""):
        die("Book title and year are required")
    reserved = (title, "", "", narrator, "", year)
    name = readable(chapter_name(files[0], 1, reserved) or title)
    print(contents_line(1, len(files), name, year, title))


def read_chapters(path):
    raw = capture([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", path,
    ])
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    found = []
    for chapter in data.get("chapters") or []:
        found.append((chapter.get("tags") or {}).get("title") or "")
    return found


def require_chapters(path, expected):
    stored = read_chapters(path)
    if len(stored) != len(expected) or any(not same_text(got, want) for got, want in zip(stored, expected)):
        shown = stored[0] if stored else ""
        die("The contents were stored with the wrong letters: %s" % shown)


def write_m4b(folder, files, title, author, author_full, narrator, genre, year):
    info = same_audio(files)
    heading = book_heading(author, title, year)
    person = surname_given(narrator or author)
    person_sort = name_parts(narrator or author)
    person_sort = person_sort[-1] if person_sort else person
    out_name = book_dirname(author, title, narrator, year) + ".m4b"
    final = os.path.join(folder, out_name)
    durations = [duration_ms(path) for path in files]
    cursor = 0
    lines = [
        ";FFMETADATA1",
        "title=%s" % meta_escape(heading),
        "album=%s" % meta_escape(heading),
        "artist=%s" % meta_escape(person),
        "album_artist=%s" % meta_escape(person),
        "composer=%s" % meta_escape(person),
        "genre=%s" % meta_escape(genre),
    ]
    reserved = (title, heading, author, narrator, genre, year)
    total = len(files)
    expected = []
    for index, (path, length) in enumerate(zip(files, durations), 1):
        end = cursor + length
        name = readable(chapter_name(path, index, reserved) or title)
        label = contents_line(index, total, name, year, title)
        readable(label)
        expected.append(label)
        lines.append("")
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append("START=%d" % cursor)
        lines.append("END=%d" % end)
        lines.append("title=%s" % meta_escape(label))
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
        require_chapters(partial, expected)
        shutil.move(partial, final)
    clear_xattr(final)
    if shutil.which("exiftool"):
        sort_cmd = [
            "exiftool", "-overwrite_original",
            "-ItemList:Author=%s" % author_full,
            "-ItemList:Artist=%s" % person,
            "-ItemList:AlbumArtist=%s" % person,
            "-ItemList:Performer=%s" % person,
            "-ItemList:Narrator=%s" % person,
            "-ItemList:Composer=%s" % person,
            "-XMP:Artist=%s" % person,
            "-XMP:Composer=%s" % person,
            "-ItemList:SortArtist=%s" % person_sort,
            "-ItemList:SortAlbumArtist=%s" % person_sort,
            "-ItemList:SortName=%s" % heading,
            "-ItemList:SortAlbum=%s" % heading,
            "-ItemList:SortComposer=%s" % person_sort,
            final,
        ]
        if run(sort_cmd) != 0:
            die("exiftool could not write sort fields")
        clear_xattr(final)
    else:
        print("exiftool not found: sort fields were not written", file=sys.stderr)
    print(out_name, flush=True)


def track_filename(index, total, title, year):
    return contents_line(index, total, title, year) + ".mp3"


def write_mp3(folder, files, title, author, author_full, narrator, genre, year):
    dest_dir = os.path.join(folder, book_dirname(author, title, narrator, year))
    os.makedirs(dest_dir, exist_ok=True)
    for name in os.listdir(dest_dir):
        if name.startswith("."):
            continue
        path = os.path.join(dest_dir, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() == ".mp3":
            os.remove(path)
    total = len(files)
    failed = []
    heading = book_heading(author, title, year)
    person = surname_given(narrator or author)
    person_sort = name_parts(narrator or author)
    person_sort = person_sort[-1] if person_sort else person
    reserved = (title, heading, author, narrator, genre, year)
    first_name = ""
    for index, path in enumerate(files, 1):
        name = readable(chapter_name(path, index, reserved) or title)
        label = contents_line(index, total, name, year, title)
        readable(label)
        dest = os.path.join(dest_dir, track_filename(index, total, title, year))
        if index == 1:
            first_name = os.path.basename(dest)
        codec = audio_info(path)[0]
        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-i", path, "-map", "0:a:0", "-map_metadata", "-1",
        ]
        if codec == "mp3":
            cmd += ["-c", "copy"]
        else:
            cmd += ["-c:a", "libmp3lame", "-b:a", MP3_RATE, "-ac", "2", "-ar", "44100"]
        cmd += [
            "-id3v2_version", "4",
            "-metadata", "title=%s" % label,
            "-metadata", "album=%s" % heading,
            "-metadata", "artist=%s" % person,
            "-metadata", "album_artist=%s" % person,
            "-metadata", "artist-sort=%s" % person_sort,
            "-metadata", "album-sort=%s" % heading,
            "-metadata", "title-sort=%s" % label,
            "-metadata", "author=%s" % author_full,
            "-metadata", "narrator=%s" % person,
            "-metadata", "track=%d/%d" % (index, total),
            "-metadata", "genre=%s" % genre,
        ]
        cmd += [
            "-metadata", "composer=%s" % person,
            "-metadata", "performer=%s" % person,
        ]
        cmd.append(dest)
        print("[%d/%d] %s" % (index, total, os.path.basename(dest)), flush=True)
        if run(cmd) != 0:
            failed.append(os.path.basename(path))
            if os.path.isfile(dest):
                os.remove(dest)
            continue
        stored = track_tags(dest).get("title", "")
        if not same_text(stored, label):
            os.remove(dest)
            die("The contents were stored with the wrong letters: %s" % stored)
        clear_xattr(dest)
    if failed:
        die("Failed: %s" % ", ".join(failed))
    print("%s/%s" % (os.path.basename(dest_dir), first_name))


def has_encoder(name):
    text = capture(["ffmpeg", "-hide_banner", "-encoders"])
    return bool(re.search(r"\b%s\b" % re.escape(name), text))


def main():
    if len(sys.argv) < 2:
        die("usage: book-audio.py guess|files|pattern|contents|sort-key|year|author-full|reader|m4b|mp3")
    action = sys.argv[1]
    if action == "guess":
        cmd_guess()
        return
    if action == "files":
        cmd_files()
        return
    if action == "pattern":
        cmd_pattern()
        return
    if action == "contents":
        cmd_contents()
        return
    if action == "sort-key":
        cmd_sort_key()
        return
    if action == "year":
        cmd_year()
        return
    if action == "author-full":
        cmd_author_full()
        return
    if action == "reader":
        print(surname_given(" ".join(sys.argv[2:])))
        return
    if action not in {"m4b", "mp3"}:
        die("unknown action: %s" % action)
    folder = os.getcwd()
    files = chapters(folder)
    if not files:
        die("No audio files in %s" % folder)
    title, author, author_full, narrator, genre, year = book_fields()
    if action == "m4b":
        write_m4b(folder, files, title, author, author_full, narrator, genre, year)
    else:
        write_mp3(folder, files, title, author, author_full, narrator, genre, year)


if __name__ == "__main__":
    main()
