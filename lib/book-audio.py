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


def folder_label(name):
    # A folder sometimes keeps a file ending, as in "….mp3". That ending is not part of the title.
    name = unicodedata.normalize("NFC", name or "").strip()
    stem, ext = os.path.splitext(name)
    if ext.lower() in AUDIO_EXT | {".m4b"}:
        return stem.strip()
    return name


def guess_folder(name):
    name = folder_label(name)
    # "Сеченов И.М. · Рефлексы головного мозга [Олег Федоров] · 1863"
    pieces = [part.strip() for part in name.split(DOT)]
    if len(pieces) >= 3 and re.fullmatch(r"\d{4}", pieces[-1] or ""):
        body = DOT.join(pieces[1:-1]).strip()
        bracket = re.fullmatch(r"(.+?) \[(.+)\]", body)
        if bracket:
            title = bracket.group(1).strip()
            narrator = bracket.group(2).strip()
        else:
            title = body
            narrator = ""
        if title:
            return {
                "title": title,
                "author": pieces[0],
                "narrator": narrator,
                "year": pieces[-1],
            }
    match = re.match(
        r"^(?P<author>.+?)\s+[-–—]\s+(?P<title>.+?)(?:\s*\[(?P<narrator>[^\]]+)\])?\s*$",
        name,
    )
    if match:
        title = match.group("title").strip()
        narrator = (match.group("narrator") or "").strip()
        # "Басни (Алексей Борзунов)": the parentheses are the reader, not the title.
        if not narrator:
            paren = re.fullmatch(r"(.+?)\s*\(([^)]+)\)", title)
            if paren and person_name(paren.group(2)):
                title = paren.group(1).strip()
                narrator = paren.group(2).strip()
        return {
            "title": title,
            "author": match.group("author").strip(),
            "narrator": narrator,
            "year": "",
        }
    # "Повесть _ Утро помещика" — the word before _ is the kind of book, not the title.
    kind = re.fullmatch(
        r"(?:повесть|роман|трактат|публицистика|рассказ|басни)\s*[_|]\s*(.+)",
        name,
        re.IGNORECASE,
    )
    if kind:
        title = kind.group(1).strip()
        narrator = ""
        bracket = re.fullmatch(r"(.+?)\s*\[([^\]]+)\]", title)
        if bracket:
            title = bracket.group(1).strip()
            narrator = bracket.group(2).strip()
        return {"title": title, "author": "", "narrator": narrator, "year": ""}
    return {"title": "", "author": "", "narrator": "", "year": ""}


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
    r"(?i)(?<![0-9A-Za-zА-Яа-яЁё])"
    r"(части|часть|главы|глава|фрагменты|фрагмент|parts|part|chapters|chapter|fragments|fragment)"
    r"\s+(\d+(?:\s*[-–—]\s*\d+)?|[IVXLCDM]+)"
)
CREDIT_WORD = re.compile(
    r"(?i)(?:читает|читал|читала|исполняет|начитыва\w*|озвучива\w*|narrated\s+by|read\s+by|narrator)\b"
)
STRUCT_NAMES = {
    "часть": "Часть",
    "части": "Части",
    "глава": "Глава",
    "главы": "Главы",
    "фрагмент": "Фрагмент",
    "фрагменты": "Фрагменты",
    "part": "Part",
    "parts": "Parts",
    "chapter": "Chapter",
    "chapters": "Chapters",
    "fragment": "Fragment",
    "fragments": "Fragments",
}


def pretty_number(token):
    token = re.sub(r"\s+", "", token or "")
    span = re.fullmatch(r"(\d+)[-–—](\d+)", token)
    if span:
        return "%s-%s" % (int(span.group(1)), int(span.group(2)))
    if token.isdigit():
        return str(int(token))
    return token.upper()


def structure_label(text):
    parts = []
    for match in STRUCT_WORD.finditer(text or ""):
        word = STRUCT_NAMES.get(match.group(1).casefold(), match.group(1).capitalize())
        parts.append("%s %s" % (word, pretty_number(match.group(2))))
    return omit_fragment_one(". ".join(parts))


def omit_fragment_one(text):
    # "Фрагмент 1" is the start of the chapter. "Фрагмент 2" stays, so the pieces differ.
    parts = re.split(r"\. ", text or "")
    if len(parts) < 2:
        return text or ""
    kept = [part for part in parts if part not in ("Фрагмент 1", "Fragment 1")]
    if not kept or len(kept) == len(parts):
        return text or ""
    return ". ".join(kept)


YEAR_LEAD = re.compile(r"^(?:1[0-9]{3}|20[0-2][0-9])\s*[.\-–—:]+\s*")
# "01_01. 1904 - Одумайтесь!" is a part number, then the year and the title.
INDEX_PREFIX = re.compile(r"^(\d{1,3}(?:[-._]\d{1,3})+)\.?\s+")
TITLE_GAP = r"(?:\s*[.\-–—:·|]+\s*|\s+)"


def strip_leading_title(body, title):
    body = unicodedata.normalize("NFC", body or "").strip()
    title = unicodedata.normalize("NFC", title or "").strip()
    if not body or not title:
        return body
    if fold(body) == fold(title):
        return ""
    pattern = re.compile(r"^\s*" + re.escape(title) + TITLE_GAP, re.IGNORECASE)
    match = pattern.match(body)
    if not match:
        return body
    return body[match.end():].strip()


def peel_book(body, title):
    # "1889 - КРЕЙЦЕРОВА СОНАТА. Глава 1" keeps the chapter. The year and the book go.
    body = unicodedata.normalize("NFC", body or "").strip()
    for _ in range(3):
        nxt = YEAR_LEAD.sub("", body).strip()
        nxt = strip_leading_title(nxt, title)
        nxt = nxt.strip(" .-–—·")
        if nxt == body:
            break
        body = nxt
    return re.sub(r"\s+", " ", body).strip(" .-–—·")


def only_book_and_numbers(body, title):
    folded = fold(body)
    book = fold(title)
    if not book or book not in folded:
        return False
    leftover = re.sub(r"[^0-9a-zа-яё]+", " ", folded.replace(book, " ", 1)).strip()
    if not leftover:
        return True
    return all(token.isdigit() for token in leftover.split())


def same_opening_word(body, title):
    # "Мысли мудрый людей - 01 Январь" still starts as the book. It is not a chapter.
    def words(text):
        return [word for word in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", fold(text)) if len(word) > 2]
    left = words(body)
    right = words(title)
    if not left or not right:
        return False
    return left[0] == right[0]


def pretty_structure(text):
    def repl(match):
        word = STRUCT_NAMES.get(match.group(1).casefold(), match.group(1).capitalize())
        return "%s %s" % (word, pretty_number(match.group(2)))
    return STRUCT_WORD.sub(repl, text)


def source_chapter(stem, book_title, narrator=""):
    # The file name after the book title is the chapter, including "Эпилог" and "- 1".
    # A bare 02-01 is not a title: the script asks what those numbers mean.
    body = peel_book(stem_body(repair_text(stem or "")), book_title)
    if re.fullmatch(r"\([^)]+\)", (body or "").strip()):
        body = ""
    if not body or (book_title and fold(body) == fold(book_title)):
        return ""
    if re.fullmatch(r"\d{1,4}", body) or re.fullmatch(r"\d{1,3}(?:[-._]\d{1,3})+", body):
        return ""
    if is_credit(body, narrator) or looks_broken(body) or only_book_and_numbers(body, book_title):
        return ""
    prefixed = re.match(r"^.+?\.\s*[-–—]\s*(.+)$", body)
    if prefixed and prefixed.group(1).strip():
        body = prefixed.group(1).strip()
    if not structure_label(body) and same_opening_word(body, book_title):
        return ""
    shown = omit_fragment_one(pretty_structure(body))
    shown = re.sub(r"\s+", " ", shown).strip(" .-–—·")
    if not shown or (book_title and fold(shown) == fold(book_title)):
        return ""
    return unicodedata.normalize("NFC", shown)


PART_WORDS = {
    "притча": "Притча",
    "день": "День",
    "часть": "Часть",
    "глава": "Глава",
    "фрагмент": "Фрагмент",
    "part": "Part",
    "day": "Day",
    "chapter": "Chapter",
}
PART_NAME = re.compile(
    r"(?:\s+-\s+|\s+)(притча|день|часть|глава|фрагмент|part|day|chapter)\s+(\d+)\s*$",
    re.IGNORECASE,
)
TRAIL_NUM = re.compile(r"\s+(\d{1,2})\s*$")
TRAIL_NOTE = re.compile(r"\s*\(([^)]+)\)\s*$")
TRAIL_NARR = re.compile(r"\s*\[([^\]]+)\]\s*$")


def structure_only(text):
    cleaned = STRUCT_WORD.sub(" ", text or "")
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "", cleaned) == ""


def coded_index(stem):
    body = unicodedata.normalize("NFC", stem_body(repair_text(stem or "")))
    match = INDEX_PREFIX.match(body)
    if not match:
        return []
    return re.split(r"[-._]", match.group(1))


def file_year_and_body(stem, book_title):
    body = unicodedata.normalize("NFC", stem_body(repair_text(stem or "")))
    match = INDEX_PREFIX.match(body)
    if match:
        body = body[match.end():].strip()
    match = YEAR_LEAD.match(body)
    if not match:
        return "", ""
    year = re.match(r"(\d{4})", match.group(0)).group(1)
    return year, peel_book(body, book_title)


def piece_bits(body):
    body = re.sub(r"\s+", " ", body or "").strip()
    narrator = ""
    match = TRAIL_NARR.search(body)
    if match:
        narrator = match.group(1).strip()
        body = body[:match.start()].strip()
    note = ""
    match = TRAIL_NOTE.search(body)
    if match:
        note = match.group(1).strip()
        body = body[:match.start()].strip()
    word = ""
    num = 0
    match = PART_NAME.search(body)
    if match:
        word = PART_WORDS.get(match.group(1).casefold(), match.group(1).capitalize())
        num = int(match.group(2))
        body = body[:match.start()].strip(" -")
    else:
        match = TRAIL_NUM.search(body)
        if match:
            num = int(match.group(1))
            body = body[:match.start()].strip()
    return body, word, num, note, narrator


def piece_key(year, work, note, narrator):
    return (year, fold(work), fold(note), fold(narrator))


def bare_number(stem, book_title):
    # "1856 - Утро помещика 01" leaves only the trailing number.
    body = unicodedata.normalize("NFC", stem_body(repair_text(stem or "")))
    body = YEAR_LEAD.sub("", body).strip()
    title = unicodedata.normalize("NFC", book_title or "").strip()
    if title:
        body = re.sub(
            r"^\s*" + re.escape(title) + r"[!?…]*\s*",
            "",
            body,
            count=1,
            flags=re.IGNORECASE,
        )
    body = body.strip(" .-–—·!?…")
    match = re.fullmatch(r"0*(\d{1,3})", body)
    if not match:
        return 0
    return int(match.group(1))


def part_word():
    # "-" leaves the number out. One word, such as Глава, names it. Otherwise it stays Часть.
    raw = os.environ.get("BOOK_CHAPTER_LABELS", "").strip()
    if raw == "-":
        return ""
    labels = [part for part in chapter_labels() if part]
    if len(labels) == 1:
        return labels[0]
    return "Часть"


def bare_part(stem, book_title):
    number = bare_number(stem, book_title)
    if not number:
        return ""
    word = part_word()
    if not word:
        return ""
    return "%s %s" % (word, number)


def collection_piece(path, book_title):
    # "01 1908 - Волк" keeps the title and that year. Split files become Часть.
    # "Притча" and "День" stay those words. A chapter of the same book does not.
    stem = repair_text(os.path.splitext(os.path.basename(path))[0])
    year, body = file_year_and_body(stem, book_title)
    if not year or not body or structure_only(body):
        return ""
    if book_title and fold(body) == fold(book_title):
        return ""
    work, word, num, note, narrator = piece_bits(body)
    if not work:
        return ""
    groups = coded_index(stem)
    index_num = int(groups[-1]) if groups and groups[-1].isdigit() else 0
    as_part = False
    if not num and not word and index_num:
        num = index_num
    if num and not word:
        key = piece_key(year, work, note, narrator)
        count = 0
        for other in chapters(os.path.dirname(path)):
            other_stem = repair_text(os.path.splitext(os.path.basename(other))[0])
            other_year, other_body = file_year_and_body(other_stem, book_title)
            if not other_year or not other_body or structure_only(other_body):
                continue
            other_work, other_word, other_num, other_note, other_narrator = piece_bits(other_body)
            other_groups = coded_index(other_stem)
            other_index = int(other_groups[-1]) if other_groups and other_groups[-1].isdigit() else 0
            if other_num:
                counted = not other_word
            else:
                counted = not other_word and other_index
            if counted and piece_key(other_year, other_work, other_note, other_narrator) == key:
                count += 1
        as_part = count >= 2
        if index_num and not piece_bits(body)[2]:
            num = index_num if as_part else 0
    label = work
    if narrator:
        label = "%s [%s]" % (label, narrator)
    if note:
        label = "%s (%s)" % (label, note)
    if word and num:
        label = "%s. %s %s" % (label, word, num)
    elif as_part:
        label = "%s. Часть %s" % (label, num)
    elif num:
        label = "%s %s" % (label, num)
    return unicodedata.normalize("NFC", "%s%s%s" % (label, DOT, year))


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
    # "-" means the number is only the file order. It is not written.
    if not raw.strip() or raw.strip() == "-":
        return []
    # A blank before the comma skips that number: ", Глава" names only the last one.
    return [part.strip() for part in raw.split(",")]


def column_indexes(rows):
    # A number that is the same on every file is not a chapter. 04_01_01 keeps 01_01.
    if not rows:
        return []
    width = len(rows[0])
    if width < 2 or any(len(row) != width for row in rows):
        return []
    vary = [index for index in range(width) if len({row[index] for row in rows}) > 1]
    if len(vary) >= 2:
        return vary
    return list(range(width))


def number_rows(files):
    rows = []
    for path in files:
        stem = repair_text(os.path.splitext(os.path.basename(path))[0])
        groups, _sample = number_groups(stem)
        if len(groups) < 2:
            return []
        rows.append(groups)
    if not rows or not column_indexes(rows):
        return []
    return rows


def number_label(stem, folder=""):
    groups, _sample = number_groups(stem)
    rows = number_rows(chapters(folder)) if folder else []
    if rows:
        groups = [groups[index] for index in column_indexes(rows)]
    labels = chapter_labels()
    if not groups or not any(labels):
        return ""
    # One word names the last number. Earlier numbers are left out.
    if len(labels) < len(groups) and all(labels):
        labels = [""] * (len(groups) - len(labels)) + labels
    if len(labels) != len(groups):
        return ""
    parts = []
    for label, number in zip(labels, groups):
        if not label:
            continue
        shown = str(int(number)) if number.isdigit() else number
        parts.append("%s %s" % (label, shown))
    if not parts:
        return ""
    return omit_fragment_one(". ".join(parts))


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
        # A comment that is only the reader's name is not a chapter.
        if key == "comment" and person_name(text):
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
    numbered = number_label(stem, os.path.dirname(path))
    if numbered:
        return numbered
    title = reserved[0] if reserved else ""
    narrator = reserved[3] if reserved and len(reserved) > 3 else ""
    part = bare_part(stem, title)
    if part:
        return part
    piece = collection_piece(path, title)
    if piece:
        return piece
    found = source_chapter(stem, title, narrator)
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
    rows = [groups for groups, _sample in tails]
    if rows and all(len(groups) >= 2 for groups in rows):
        indexes = column_indexes(rows)
        if len(indexes) >= 2 and len({len(groups) for groups in rows}) == 1:
            sample = "_".join(rows[0][index] for index in indexes)
            return "numbers", sample, len(indexes)
    title = env("BOOK_TITLE")
    if title and not any(labels):
        numbers = []
        for path in files:
            stem = repair_text(os.path.splitext(os.path.basename(path))[0])
            number = bare_number(stem, title)
            if not number:
                numbers = []
                break
            numbers.append(number)
        if len(numbers) >= 2:
            return "tail", "%02d" % numbers[0], 1
    return "", "", 0


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
        if info[0] != first[0] or info[1] != first[1]:
            die(
                "%s is %s Hz %s ch %s; the first file is %s Hz %s ch %s"
                % (
                    os.path.basename(path), info[1], info[2], info[0],
                    first[1], first[2], first[0],
                )
            )
    return first


def stereo_inputs(files, tmp):
    # The book is stereo. A mono file next to stereo files is widened here,
    # because the join step needs the same channel count.
    infos = [audio_info(path) for path in files]
    first = same_audio(files)
    if all(info[2] == first[2] for info in infos):
        return files
    prepared = []
    for index, (path, info) in enumerate(zip(files, infos)):
        if info[2] == "2":
            prepared.append(path)
            continue
        dest = os.path.join(tmp, "stereo-%02d.mp3" % index)
        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-i", path, "-c:a", "libmp3lame", "-ac", "2", "-ar", first[1],
            "-b:a", MP3_RATE, dest,
        ]
        if run(cmd) != 0 or not os.path.isfile(dest):
            die("Could not make this file stereo: %s" % os.path.basename(path))
        prepared.append(dest)
    return prepared


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
    # One letter й, so Books does not split the same reader into two groups.
    name = unicodedata.normalize("NFC", name or "")
    parts = name_parts(name)
    if len(parts) < 2:
        return name.strip()
    return unicodedata.normalize("NFC", "%s %s" % (parts[-1], parts[0]))


def person_name(text):
    # A comment may say who reads. A site address is not a name.
    # й stored as two marks is still the same name.
    text = unicodedata.normalize("NFC", repair_text(text or ""))
    text = CREDIT_WORD.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .:-–,")
    if not text or re.search(r"www\.|https?://|@", text, re.IGNORECASE):
        return ""
    words = name_parts(text)
    if not 2 <= len(words) <= 3:
        return ""
    for word in words:
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*", word):
            return ""
    return " ".join(words)


def artist_label(text):
    # A reader is Имя Фамилия. One word, such as Сборник, is the book artist
    # when the files do not share one reader. Books says Unknown Author if it is empty.
    text = unicodedata.normalize("NFC", (text or "").strip())
    named = person_name(text)
    if named:
        return named, "person"
    if re.fullmatch(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*", text):
        return text, "label"
    return "", ""


def same_person(left, right):
    left = (left or "").strip()
    right = (right or "").strip()
    if not left or not right:
        return False
    forms = {fold(left), fold(surname_given(left))}
    return fold(right) in forms or fold(surname_given(right)) in forms


def file_narrator(path):
    # Brackets in the file name are Имя Фамилия. A comment is kept as written.
    stem = repair_text(os.path.splitext(os.path.basename(path))[0])
    match = re.search(r"\[([^\]]+)\]", unicodedata.normalize("NFC", stem))
    if match:
        return surname_given(match.group(1).strip())
    return person_name(track_tags(path).get("comment", ""))


def narrator_mode(files):
    # Empty when the folder names no reader and the files do not share one.
    named = []
    seen = []
    for path in files:
        name = file_narrator(path)
        named.append(name)
        if name and name not in seen:
            seen.append(name)
    if not seen:
        return "ask", []
    if len(seen) == 1 and all(named):
        return "ask", seen
    return "each", seen


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


def shared_file_year(files):
    # Every file named "1856 - Title 01" offers 1856. Mixed years stay blank.
    years = []
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        year, _body = file_year_and_body(stem, "")
        if not year:
            return ""
        years.append(year)
    if years and len(set(years)) == 1:
        return years[0]
    return ""


def cmd_year():
    named = guess_folder(os.path.basename(os.getcwd())).get("year") or ""
    if re.fullmatch(r"\d{4}", named):
        print("year\t%s" % named)
        print("note\tfolder")
        return
    year = year_written(env("BOOK_AUTHOR"), env("BOOK_TITLE"))
    note = "written" if year else ""
    if not year:
        year = shared_file_year(chapters(os.getcwd()))
        note = "files" if year else ""
    print("year\t%s" % year)
    print("note\t%s" % note)


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
    elif kind == "tail":
        print("tail\t%s" % value)


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


def reader_sort(name, surname_first):
    parts = name_parts(name)
    if not parts:
        return ""
    return parts[0] if surname_first else parts[-1]


def write_m4b(folder, files, title, author, author_full, narrator, genre, year):
    info = same_audio(files)
    info = (info[0], info[1], "2")
    heading = book_heading(author, title, year)
    person = surname_given(narrator) if narrator else ""
    person_sort = reader_sort(narrator, False) if narrator else ""
    out_name = book_dirname(author, title, narrator, year) + ".m4b"
    final = os.path.join(folder, out_name)
    durations = [duration_ms(path) for path in files]
    cursor = 0
    lines = [
        ";FFMETADATA1",
        "title=%s" % meta_escape(heading),
        "album=%s" % meta_escape(heading),
        "genre=%s" % meta_escape(genre),
    ]
    if person:
        lines.insert(3, "artist=%s" % meta_escape(person))
        lines.insert(4, "album_artist=%s" % meta_escape(person))
        lines.insert(5, "composer=%s" % meta_escape(person))
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
    encoder = "aac_at" if has_encoder("aac_at") else "aac"
    with tempfile.TemporaryDirectory() as tmp:
        audio_files = stereo_inputs(files, tmp)
        concat = []
        for path in audio_files:
            escaped = os.path.abspath(path).replace("'", r"'\''")
            concat.append("file '%s'" % escaped)
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
            "-ItemList:SortName=%s" % heading,
            "-ItemList:SortAlbum=%s" % heading,
        ]
        if person:
            sort_cmd += [
                "-ItemList:Artist=%s" % person,
                "-ItemList:AlbumArtist=%s" % person,
                "-ItemList:Performer=%s" % person,
                "-ItemList:Narrator=%s" % person,
                "-ItemList:Composer=%s" % person,
                "-XMP:Artist=%s" % person,
                "-XMP:Composer=%s" % person,
                "-ItemList:SortArtist=%s" % person_sort,
                "-ItemList:SortAlbumArtist=%s" % person_sort,
                "-ItemList:SortComposer=%s" % person_sort,
            ]
        sort_cmd.append(final)
        if run(sort_cmd) != 0:
            die("exiftool could not write sort fields")
        clear_xattr(final)
    else:
        print("exiftool not found: sort fields were not written", file=sys.stderr)
    print(out_name, flush=True)


def track_filename(index, total, title, year, chapter=""):
    width = max(2, len(str(total)))
    number = "%0*d" % (width, index)
    shown = safe_filename(title)
    detail = safe_filename(chapter) if chapter else ""
    if detail and fold(detail) != fold(shown):
        tail = detail
    else:
        tail = year
    return "%s. %s%s%s.mp3" % (number, shown, DOT, tail)


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
    # One narrator: the album artist is that reader. A word such as Сборник is
    # only the book artist. Many readers: the author, so the album stays one piece.
    # Each file's artist is still its own reader.
    one_reader = artist_label(narrator)[1] == "person"
    if one_reader:
        album_person = surname_given(narrator)
        album_sort = reader_sort(narrator, False)
    else:
        album_person = author_full
        album_sort = reader_sort(author_full, True)
    reserved = (title, heading, author, narrator, genre, year)
    first_name = ""
    for index, path in enumerate(files, 1):
        name = readable(chapter_name(path, index, reserved) or title)
        label = contents_line(index, total, name, year, title)
        readable(label)
        dest = os.path.join(dest_dir, track_filename(index, total, title, year, name))
        if index == 1:
            first_name = os.path.basename(dest)
        own = file_narrator(path)
        if one_reader and (not own or same_person(own, album_person)):
            person = album_person
            person_sort = album_sort
        elif own:
            person = own
            person_sort = reader_sort(own, True)
        else:
            person = ""
            person_sort = ""
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
            "-metadata", "album-sort=%s" % heading,
            "-metadata", "title-sort=%s" % label,
            "-metadata", "author=%s" % author_full,
            "-metadata", "track=%d/%d" % (index, total),
            "-metadata", "genre=%s" % genre,
        ]
        if album_person:
            cmd += ["-metadata", "album_artist=%s" % album_person]
        if person_sort or album_sort:
            cmd += ["-metadata", "artist-sort=%s" % (person_sort or album_sort)]
        if person:
            cmd += [
                "-metadata", "artist=%s" % person,
                "-metadata", "narrator=%s" % person,
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
    if action == "kind":
        _text, kind = artist_label(" ".join(sys.argv[2:]))
        if not kind:
            sys.exit(1)
        print(kind)
        return
    if action == "narrators":
        files = chapters(os.getcwd())
        mode, names = narrator_mode(files)
        if env("BOOK_NARRATOR"):
            mode = "ask"
        print("mode\t%s" % mode)
        for name in names:
            print("reader\t%s" % name)
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
