#!/usr/bin/env python3
# Word / DjVu -> plain text paragraphs for Pages. Does not touch the source file.
import re, subprocess, sys

JUNK = ("INCLUDEPICTURE", "HYPERLINK", "MERGEFORMAT")
SPACED = re.compile(
    r"(?<!\w)(?:[A-Za-zА-Яа-яЁё]\s){3,}[A-Za-zА-Яа-яЁё](?!\w)"
)


class NeedTool(Exception):
    def __init__(self, message):
        self.message = message


def extract(path):
    low = path.lower()
    if low.endswith((".doc", ".docx", ".rtf")):
        try:
            return subprocess.check_output(
                ["textutil", "-convert", "txt", "-stdout", path],
                text=True, errors="replace",
            )
        except FileNotFoundError:
            raise NeedTool("textutil not found")
    if low.endswith(".djvu"):
        try:
            return subprocess.check_output(
                ["djvutxt", path],
                text=True, errors="replace",
            )
        except FileNotFoundError:
            raise NeedTool("djvutxt not found. Install it with: brew install djvulibre")
    raise NeedTool("not a Word or DjVu file")


def is_page_number(line):
    s = line.strip()
    if re.fullmatch(r"\d{1,4}", s):
        return True
    if re.fullmatch(r"[—–\-]\s*\d{1,4}\s*[—–\-]", s):
        return True
    return False


def is_junk(line):
    return any(tok in line for tok in JUNK)


def normalize(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u00ad", "")
    text = text.replace("\u2028", "\n\n").replace("\u2029", "\n\n").replace("\v", "\n\n")
    return text


def page_lines(page):
    lines = []
    for raw in page.split("\n"):
        if not raw.strip():
            lines.append({"blank": True})
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        text = raw.strip()
        if is_junk(text) or is_page_number(text) or SIGNATURE.match(text):
            continue
        lines.append({"blank": False, "text": text, "indent": indent})
    return lines


def first_short(lines):
    for item in lines:
        if item["blank"]:
            continue
        if len(item["text"]) <= 80:
            return item["text"]
        return None
    return None


def running_headers(pages):
    counts = {}
    for lines in pages:
        key = first_short(lines)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {key for key, n in counts.items() if n >= 5}


def is_full(text, width):
    return width >= 45 and len(text) >= int(width * 0.8)


def ends_sentence(text):
    t = strip_tail_marks(text)[0].rstrip().rstrip("\"»”')]}>")
    return t.endswith((".", "!", "?", "…", "..."))


NEW_BLOCK = re.compile(r"^(Глава|Часть|Предисловие|ПРЕДИСЛОВИЕ|ОГЛАВЛЕНИЕ|Содержание)\b")
SECTION_LABEL = re.compile(r"^[А-ЯA-Z]\.\s+\S")


def ends_page_number(text):
    return bool(re.search(r"\d{1,4}\s*$", text))


def starts_new_block(text):
    # A contents line and a chapter title begin their own paragraph.
    return bool(NEW_BLOCK.match(text) or SECTION_LABEL.match(text))


def continues_sentence(text):
    # A line that starts with a small letter is the same sentence.
    # List marks like «б)» stay their own paragraph.
    if re.match(r"^[а-яё]\)", text):
        return False
    return text[:1].islower()


def is_short_heading(text, width):
    limit = 24
    if width >= 45:
        limit = max(24, int(width * 0.45))
    return len(text) <= limit


def marker_at_start(line, mode):
    # A note at the bottom of the page starts with its mark. The same mark in a sentence does not.
    s = line.strip()
    if mode == "stars":
        m = re.match(r"^(\d+\*+|\*+)(.*)$", s)
        if not m:
            return None
        rest = m.group(2)
        # «*r~'» is scan noise. A real note is «* Текст» or a whole line of words.
        if rest and rest[0].isalnum() and len(re.findall(r"[A-Za-zА-Яа-яЁё]", rest)) < 8:
            return None
        rest = rest.lstrip(" \t•·*")
        return m.group(1), rest.strip()
    if mode == "numbers":
        m = re.match(r"^(\d{1,3})[.)]\s+(\S.*)$", s)
        if not m:
            return None
        return m.group(1), m.group(2).strip()
    return None


def marker_regex(key, mode):
    if mode == "stars":
        if re.fullmatch(r"\d+\*+", key):
            return re.compile(r"(?<!\d)" + re.escape(key) + r"(?!\*)")
        return re.compile(r"(?<!\*)" + re.escape(key) + r"(?!\*)")
    return re.compile(r"\[" + re.escape(key) + r"\]|(?<=\w)" + re.escape(key) + r"(?!\d)")


def peel_page_notes(page, mode, found):
    lines = page.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and is_page_number(lines[-1].strip()):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    idxs = [i for i, line in enumerate(lines) if marker_at_start(line, mode)]
    # A long note starts higher than the last lines of the page. Keep marks that belong to that block.
    near = [i for i in idxs if i >= len(lines) - 30]
    if not near:
        return "\n".join(lines)
    start = near[0]
    for i in reversed(idxs):
        if i >= start:
            continue
        if start - i <= 20:
            start = i
        else:
            break
    block = [ln.strip() for ln in lines[start:] if ln.strip()]
    body = lines[:start]
    parsed = []
    key = None
    buf = []
    for line in block:
        mark = marker_at_start(line, mode)
        if mark:
            if key is not None:
                parsed.append((key, " ".join(buf).strip()))
            key, rest = mark
            buf = [rest] if rest else []
        elif key is not None and not SIGNATURE.match(line) and not JUNK_NOTE.match(line):
            buf.append(line)
    if key is not None:
        parsed.append((key, " ".join(buf).strip()))
    # Longer marks first, so 2* is not eaten as *.
    parsed.sort(key=lambda item: len(item[0]), reverse=True)
    for key, note in parsed:
        note = tidy_commas(SIGNATURE_TAIL.sub("", collapse_spaced(note)).strip())
        if not note:
            # A bare 2* at the bottom is not a note.
            continue
        token = "\uE000%d\uE001" % len(found)
        pat = marker_regex(key, mode)
        plain = []
        on_hyphen = []
        for i, line in enumerate(body):
            for m in pat.finditer(line):
                hyphen_break = (
                    m.start() > 0
                    and line[m.start() - 1] == "-"
                    and line[m.end():].strip() == ""
                )
                item = (i, m.start(), m.end())
                (on_hyphen if hyphen_break else plain).append(item)
        placed = (plain or on_hyphen)
        if placed:
            i, start, end = placed[-1]
            line = body[i]
            body[i] = line[:start] + token + line[end:]
            found.append(note)
            continue
        # The mark was only at the bottom. Do not invent a note in the sentence.
        body.append(key + " " + note)
    return "\n".join(body)


def loosen_hyphen_marks(page):
    # «Ка-* / жется» is a broken word. A real note was already replaced by a token.
    return "\n".join(
        re.sub(r"-(?:\d+\*+|\*+)\s*$", "-", line) for line in page.split("\n")
    )


TAIL_MARKS = re.compile(r"(?:\uE000\d+\uE001)+$")
# Printer line at the bottom of the page: «2 К. Д. Ушинский, т. VIII». OCR moves the comma and the case.
SIGNATURE = re.compile(r"^(?:\d{1,3}|[бБ])\s+[Кк][.,]\s*[Дд][.,]\s*[Уу]шин")
JUNK_NOTE = re.compile(r"^\d{1,3}[A-Za-zА-Яа-яЁё]?$")
SIGNATURE_TAIL = re.compile(
    r"\s+(?:\d{1,3}|[бБ])\s+[Кк][.,]\s*[Дд][.,]\s*[Уу]шин\S*(?:,?\s*т\.\s*\S+)?(?:\s+\d{1,4})?\s*$"
)


def strip_tail_marks(text):
    m = TAIL_MARKS.search(text)
    if not m:
        return text, ""
    return text[:m.start()], m.group(0)


def can_dehyphen(buf, nxt):
    core, _ = strip_tail_marks(buf)
    if not core.endswith("-") or core.endswith("--"):
        return False
    if len(core) >= 2 and not core[-2].isalnum():
        return False
    return bool(nxt) and nxt[0].isalpha()


def join_hyphen(buf, nxt):
    # A mark sitting on the hyphen belongs after the whole word: ста-* / новится.
    core, marks = strip_tail_marks(buf)
    word, rest = re.match(r"(\S+)(.*)$", nxt).groups()
    return core[:-1] + word + marks + rest


def collapse_spaced(text):
    return SPACED.sub(lambda m: re.sub(r"\s+", "", m.group(0)), text)


LEADER = set(" \t.*•·*,…")


def strip_toc_leader(text):
    # «6^|» is a page number the scan broke. Leave the digit so the line stays a contents line.
    if text.startswith("Глава "):
        text = re.sub(r"(\d{1,4})[|^~\\_]+\s*$", r"\1", text)
    # «Припоминание. . . . , 338» and «ощущения • 324». A real period stays: «организма. 67».
    m = re.search(r"(\d{1,4})\s*$", text)
    if not m:
        return text
    head = text[:m.start()]
    i = len(head)
    while i > 0 and head[i - 1] in LEADER:
        i -= 1
    leader = head[i:]
    if not leader.strip():
        return text
    if leader.strip(" ") == "." and not leader[:1].isspace():
        return text
    return head[:i].rstrip() + " " + m.group(1)


def repair_scan(text):
    # «Еесь» is «весь». «lacntb физиологическая» is «Часть физиологическая».
    text = text.replace("lacntb", "Часть")
    text = text.replace("Еесь", "весь")
    return text


def tidy_commas(text):
    # «науки,но» and «слово ,но» — the comma keeps one space after it, not before.
    text = re.sub(r"[ \t]+,", ",", text)
    text = re.sub(r",{2,}", ",", text)
    text = re.sub(r",(?=[A-Za-zА-Яа-яЁё])", ", ", text)
    # «т.е.» is written «т. е.». The same for т. д., т. п., т. к., т. н.
    text = re.sub(r"\b([Тт])\.([едкипн])\.", r"\1. \2.", text)
    # «сочиненийК.» and «Д.Ушинского».
    text = re.sub(r"([а-яё])([А-ЯЁ]\.)", r"\1 \2", text)
    text = re.sub(r"([А-ЯЁ]\.)([А-ЯЁ])", r"\1 \2", text)
    # «какаянибудь» is «какая-нибудь». A space there would be wrong.
    text = re.sub(r"([а-яё])(нибудь|либо)\b", r"\1-\2", text)
    # «к. приему» — the dot is not part of the word.
    text = re.sub(r"(?<=\b[КкВвСсОоУу])\. (?=[а-яё])", " ", text)
    # «. 2. Всякий» and «20. .Всякий» — the extra dot is not a sentence.
    text = re.sub(r"^\.\s+(?=\d{1,2}\.\s)", "", text)
    text = re.sub(r"(\d{1,2})\. \.(?=[А-ЯЁ])", r"\1. ", text)
    # «утвер- ?кдающая» and «ме?кду» — the scan drew «ж» as «?к».
    text = re.sub(r"([А-Яа-яЁё])-\s+\?к", r"\1ж", text)
    text = re.sub(r"(?<=[А-Яа-яЁё])\?к(?=[А-Яа-яЁё])", "ж", text)
    text = text.replace("щЗтазнает", "признает")
    return repair_scan(text)


def norm_word(word):
    word = word.lower().replace("ё", "е")
    if word.endswith("ии"):
        word = word[:-2] + "ий"
    return word


def title_words(text):
    return [norm_word(w) for w in re.findall(r"[А-Яа-яЁё]+", text)]


def contents_titles(paragraphs):
    # The first contents block already has «Глава I. Об организмах вообще 61».
    titles = []
    seen = set()
    for para in paragraphs:
        if len(para) > 180:
            continue
        m = re.match(r"^Глава\s+(\S+)\.\s+(.+?)\s+\d{1,4}\S*$", para)
        if not m:
            continue
        if m.group(1) in seen:
            break
        seen.add(m.group(1))
        titles.append(m.group(2).strip().rstrip("."))
    return titles


def best_title(para, titles):
    head = set(title_words(para)[:18])
    best = None
    for title in titles:
        tw = title_words(title)
        if not tw:
            continue
        hit = sum(1 for w in tw if w in head)
        ratio = hit / float(len(tw))
        if ratio < 0.6:
            continue
        cand = (hit, ratio, title)
        if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
            best = cand
    return best[2] if best else None


def strip_title_prefix(para, title):
    # The chapter line already carries the name. Drop the same name shouted in capitals.
    tw = title_words(title)
    pw = title_words(para)
    if pw and len(pw) <= len(tw) + 1 and tw and len(set(pw) & set(tw)) / float(len(tw)) >= 0.6 and set(pw) <= set(tw):
        return ""
    parts = re.findall(r"[А-Яа-яЁё]+|[^А-Яа-яЁё]+", para)
    matched = 0
    last = -1
    started = False
    for i, part in enumerate(parts):
        if not re.fullmatch(r"[А-Яа-яЁё]+", part):
            continue
        if matched >= len(tw):
            break
        if norm_word(part) == tw[matched]:
            started = True
            last = i
            matched += 1
        elif not started:
            return para
        else:
            break
    if not tw or matched / float(len(tw)) < 0.6 or last < 0:
        return para
    return "".join(parts[last + 1:]).strip(" .,;:—–-")


def caps_title(para):
    # «ПРОТИВОРЕЧИЕ ДУАЛИЗМА И МОНИЗМА Мнение…» — the capitals are the chapter name.
    parts = re.findall(r"[А-Яа-яЁё]+|[^А-Яа-яЁё]+", para)
    taken = []
    for part in parts:
        if re.fullmatch(r"[А-Яа-яЁё]+", part):
            letters = [ch for ch in part if ch.isalpha()]
            if letters and all(ch.isupper() for ch in letters):
                taken.append(part)
                continue
            break
        elif taken:
            taken.append(part)
    raw = "".join(taken).strip(" ,.;:—–-")
    if len(title_words(raw)) < 2:
        return None
    low = raw.lower()
    return low[:1].upper() + low[1:]


def chapter_label(para):
    # «Глава XXXIV М» — the lone letter is a mark the scan misread, not part of the number.
    m = re.match(r"^Глава\s+(\S+)(?:\s+[А-ЯЁ])?$", para)
    if not m or len(para) > 40:
        return None
    return "Глава " + m.group(1)


def name_chapters(paragraphs):
    # «Глава I» plus the name from the contents, so the Pages list shows both.
    titles = contents_titles(paragraphs)
    out = []
    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        label = chapter_label(para)
        if label and i + 1 < len(paragraphs):
            nxt = paragraphs[i + 1]
            title = best_title(nxt, titles) or caps_title(nxt)
            if title:
                out.append(label + ". " + title)
                rest = strip_title_prefix(nxt, title)
                if rest:
                    out.append(rest)
                i += 2
                continue
        out.append(para)
        i += 1
    return out


def _plan_prefix(prefix):
    prefix = prefix.strip()
    if len(prefix) < 15 or len(prefix) > 900:
        return False
    if "—" not in prefix and "–" not in prefix:
        return False
    return bool(re.search(r"[\(\{]\d{1,2}", prefix))


def _unwrap_plan(plan, rest):
    # Drop a shouted chapter name and a caps label glued to the list.
    plan = strip_leading_caps(plan.strip())
    plan = re.sub(r"\s*\([А-ЯЁ][А-ЯЁ\s]{3,}\)\s*$", "", plan).strip()
    rest = rest.strip()
    out = [plan] if plan else []
    # «Противоречие причины 1. Мы уже» — the short line, then the numbered text.
    m = re.match(r"(\S+(?:\s+\S+){0,5})\s+1(\.\s+|\s+)(\S.*)$", rest)
    if m and len(m.group(1)) <= 42 and not re.search(r"\d", m.group(1)):
        out.append(m.group(1))
        gap = ". " if "." in m.group(2) else " "
        out.append("1" + gap + m.group(3))
    elif rest:
        out.append(rest)
    return out


def strip_leading_caps(para):
    title = caps_title(para)
    if not title:
        return para
    return strip_title_prefix(para, title) or para


def peel_plan(para):
    # The list at the top of a chapter is its own paragraph, even if the scan ran it into the text.
    found = list(re.finditer(r"\(\d{1,2}", para))
    m = re.search(r"\)\s+(?=1(?:\.\s+|\s+)\S)", para)
    if m and len([item for item in found if item.start() < m.start()]) >= 2 and ("—" in para[:m.end()] or "–" in para[:m.end()]):
        return _unwrap_plan(para[:m.start() + 1], para[m.end():])
    m = re.search(
        r"\)\s+(?=(?:[А-ЯЁ][а-яё]+(?:\s+[а-яё]+){0,4}\s+)?1(?:\.\s+|\s+)\S)",
        para,
    )
    if m and m.start() < 900 and _plan_prefix(para[:m.start() + 1]):
        return _unwrap_plan(para[:m.start() + 1], para[m.end():])
    m = re.search(r"\)\s+(?=[А-ЯЁ][а-яё])", para)
    if (
        m
        and m.start() < 700
        and len(para) - m.end() > 80
        and not re.match(r"^\d{1,2}\.\s", para)
        and _plan_prefix(para[:m.start() + 1])
    ):
        return _unwrap_plan(para[:m.start() + 1], para[m.end():])
    return [para]


def split_numbered(paragraphs):
    # «организма. 3. Таким» — the next point is its own paragraph.
    # «S. 4. Далее» and «Г. 3. Гинзбург» stay: that dot is not the end of a sentence.
    pat = re.compile(r"([а-яё][.!?…]|[\*\]»])\s+(?=\d{1,2}\.\s+[А-ЯЁ])")
    out = []
    for para in paragraphs:
        start = 0
        for m in pat.finditer(para):
            head = (para[start:m.start()] + m.group(1)).strip()
            if head:
                out.append(head)
            start = m.end()
        tail = para[start:].strip()
        if tail:
            out.append(tail)
    return out


def split_plans(paragraphs):
    # The numbered plan at the start of a chapter stays its own paragraph.
    out = []
    for para in paragraphs:
        out.extend(peel_plan(para))
    return out


def is_plan(para):
    if re.match(r"^\d{1,2}\.\s", para):
        return False
    if len(para) > 900:
        return False
    return len(re.findall(r"\(\d{1,2}", para)) >= 2 and ("—" in para or "–" in para)


def plan_items(para):
    # «(1). — Определение». A dash inside (1—4) stays.
    parts = re.split(r"(?<=[.!?…)])\s*[—–]\s*", para.strip())
    items = [re.sub(r"[.\s]+$", "", part.strip()) for part in parts if part.strip(" .")]
    if len(items) < 2:
        return [para.strip()]
    return items


def plan_entries(para):
    # Each topic, and the section numbers that used to sit in parentheses.
    items = []
    for item in plan_items(para):
        m = re.search(r"\s*[\(\{]([^(){}]{0,24})[\)\}]\s*$", item)
        if not m:
            clean = item.strip(" .")
            if clean:
                items.append((clean, ""))
            continue
        topic = item[:m.start()].strip(" .")
        ref = re.sub(r"\s*[—–]\s*", "–", m.group(1).strip())
        ref = re.sub(r"\s{2,}", " ", ref)
        if topic or ref:
            items.append((topic, ref))
    return items


def plan_sentence(para):
    # Topic, then its section numbers. A marker splits the two looks.
    pieces = []
    for topic, ref in plan_entries(para):
        piece = topic
        if ref:
            piece += "\uE011" + ref
        if piece:
            pieces.append(piece)
    if len(pieces) < 2:
        return pieces[0] if pieces else ""
    return "\uE010".join(pieces)


def is_opener_plan(para, prev):
    # The list on the chapter's own page. A name in the index is not one.
    if not prev or not (heading_parts(prev) or is_chapter_heading(prev)):
        return False
    if is_plan(para):
        return True
    if len(para) > 900 or re.match(r"^\d{1,2}\.\s", para):
        return False
    return bool(re.search(r"[\(\{]\d{1,2}", para) and re.search(r"[—–]", para))


def is_subhead(para, nxt=""):
    # «Постижение предметов» — a short bold line inside the chapter, not a contents entry.
    if len(para) < 4 or len(para) > 42 or para.isupper():
        return False
    if re.search(r"[\d.!?…,:;()\[\]«»]", para):
        return False
    words = para.split()
    if not 1 <= len(words) <= 6:
        return False
    if not re.match(r"^[А-ЯЁ]", para):
        return False
    for word in words[1:]:
        if re.match(r"[А-ЯЁ]", word):
            return False
    # A name in the index is followed by «стр.». A real subhead is followed by the chapter text.
    if not nxt or len(nxt) < 40 or "стр." in nxt[:80] or nxt.startswith("Глава "):
        return False
    return bool(re.match(r"^(?:\d{1,2}\.\s|[А-ЯЁ])", nxt))


def clean_text(text, notes="none", found=None):
    if found is None:
        found = []
    parts = []
    for part in normalize(text).split("\f"):
        if notes in ("stars", "numbers"):
            part = peel_page_notes(part, notes, found)
        if notes == "stars":
            part = loosen_hyphen_marks(part)
        parts.append(part)
    pages = [page_lines(part) for part in parts]
    headers = running_headers(pages)
    paragraphs = []
    buf = ""
    prev_full = False

    def flush():
        nonlocal buf, prev_full
        piece = tidy_commas(strip_toc_leader(collapse_spaced(re.sub(r"[ \t]+", " ", buf).strip())))
        if piece:
            paragraphs.append(piece)
        buf = ""
        prev_full = False

    for lines in pages:
        lengths = [len(item["text"]) for item in lines if not item["blank"]]
        width = sorted(lengths)[int(len(lengths) * 0.7)] if lengths else 0
        while lines and lines[-1]["blank"]:
            lines.pop()
        seen = False
        for item in lines:
            if item["blank"]:
                if seen:
                    flush()
                continue
            text = repair_scan(item["text"])
            if not seen:
                seen = True
                if text in headers:
                    continue
            if not buf:
                buf = text
                prev_full = is_full(text, width)
                continue
            if item["indent"] >= 3 and not can_dehyphen(buf, text):
                flush()
                buf = text
                prev_full = is_full(text, width)
                continue
            if can_dehyphen(buf, text):
                buf = join_hyphen(buf, text)
                prev_full = True
                continue
            if starts_new_block(text):
                flush()
                buf = text
                prev_full = is_full(text, width)
                continue
            # «Глава VIII. Мускулы. Чувство.» + «Орган голоса 132»
            if buf.startswith("Глава") and ends_page_number(text) and not ends_page_number(buf):
                buf = buf + " " + text
                prev_full = is_full(text, width)
                continue
            if continues_sentence(text):
                buf = buf + " " + text
                prev_full = is_full(text, width)
                continue
            # A short line ends a paragraph only when the sentence ends there.
            # A wrapped line with no . ! ? stays in the same paragraph.
            if prev_full or (not ends_sentence(buf) and not is_short_heading(buf, width)):
                buf = buf + " " + text
                prev_full = is_full(text, width)
                continue
            flush()
            buf = text
            prev_full = is_full(text, width)
    flush()
    paragraphs = split_numbered(split_plans(name_chapters(paragraphs)))
    if not paragraphs:
        return ""
    return "\n\n".join(paragraphs) + "\n"


def convert(path, notes="none"):
    found = []
    text = clean_text(extract(path), notes, found)
    return text, found


def renumber_notes(text, notes):
    # Superscripts in the file follow the sentence, not the order the marks were found.
    seen = []

    def repl(m):
        idx = int(m.group(1))
        if idx not in seen:
            seen.append(idx)
        return "\uE000%d\uE001" % seen.index(idx)

    return re.sub(r"\uE000(\d+)\uE001", repl, text), [notes[i] for i in seen]


def render_txt(text, notes):
    text, notes = renumber_notes(text, notes)

    def repl(m):
        return "[%d]" % (int(m.group(1)) + 1)
    body = re.sub(r"\uE000(\d+)\uE001", repl, text)
    if not notes:
        return body
    lines = [body.rstrip(), "", "Сноски", ""]
    for i, note in enumerate(notes, 1):
        lines.append("%d. %s" % (i, note))
    lines.append("")
    return "\n".join(lines)


CHAPTER_HEADING = re.compile(r"^Глава\s+\S+")


def heading_parts(para):
    # «Глава I. Об организмах вообще» → number and name, so each can have its own look.
    if not is_chapter_heading(para):
        return None
    m = re.match(r"^(Глава\s+\S+)\.\s+(\S.*)$", para)
    if not m:
        return None
    return m.group(1), m.group(2)


def is_chapter_heading(para):
    # «Глава I. Об организмах вообще» is the chapter.
    # A contents line keeps its page number. A long synopsis stays plain text.
    s = para.strip()
    if s in ("ОГЛАВЛЕНИЕ", "Содержание", "ПРЕДИСЛОВИЕ", "Предисловие", "Предисловие."):
        return True
    if re.search(r"\d{1,4}\s*$", s):
        return False
    if len(s) <= 40 and "." not in s and "?" not in s and "!" not in s:
        return bool(CHAPTER_HEADING.match(s))
    if (
        s.startswith("Глава ")
        and len(s) <= 90
        and "(" not in s
        and not re.search(r"\d", s[-8:])
    ):
        return True
    return False


def xml_escape(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def text_runs(text, kind):
    fonts = '<w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/>'
    if kind == "heading":
        run_pr = "<w:rPr>%s<w:b/><w:sz w:val=\"36\"/></w:rPr>" % fonts
    elif kind == "title":
        run_pr = "<w:rPr>%s<w:sz w:val=\"28\"/></w:rPr>" % fonts
    elif kind == "subhead":
        run_pr = "<w:rPr>%s<w:b/><w:sz w:val=\"26\"/></w:rPr>" % fonts
    elif kind == "plan":
        run_pr = "<w:rPr>%s<w:i/><w:sz w:val=\"22\"/></w:rPr>" % fonts
    elif kind == "label":
        # Heavy capitals on the page. The heading style stays as the contents list uses it.
        # Bold on the family Pages actually uses. Wide letter-spacing made the word look light.
        run_pr = "<w:rPr>%s<w:b/><w:caps/><w:spacing w:val=\"16\"/><w:sz w:val=\"48\"/></w:rPr>" % fonts
    elif kind == "display":
        light = '<w:rFonts w:ascii="Avenir Next Ultra Light" w:hAnsi="Avenir Next Ultra Light" w:cs="Avenir Next Ultra Light"/>'
        run_pr = "<w:rPr>%s<w:b w:val=\"0\"/><w:sz w:val=\"60\"/></w:rPr>" % light
    elif kind == "deck":
        run_pr = "<w:rPr>%s<w:i w:val=\"0\"/><w:sz w:val=\"24\"/></w:rPr>" % fonts
    else:
        run_pr = "<w:rPr>%s<w:sz w:val=\"24\"/></w:rPr>" % fonts
    # Middle dot sits on the line’s center. Section numbers in the description are bold, in brackets.
    dot_pr = "<w:rPr>%s<w:sz w:val=\"24\"/></w:rPr>" % fonts
    num_pr = "<w:rPr>%s<w:b/><w:sz w:val=\"24\"/></w:rPr>" % fonts
    lead = ""
    if kind == "body":
        # «3. Таким» — the paragraph number is bold, the same weight as «Глава».
        m = re.match(r"(\d{1,2}\.)(?=\s)", text)
        if m:
            lead = m.group(1)
            text = text[m.end():]
    parts = re.split(r"\uE000(\d+)\uE001", text)
    out = []

    def add_text(part):
        if kind != "deck" or ("\uE010" not in part and "\uE011" not in part):
            out.append("<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (run_pr, xml_escape(part)))
            return
        bits = part.split("\uE010")
        for j, bit in enumerate(bits):
            topic, _, ref = bit.partition("\uE011")
            if topic:
                out.append("<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (run_pr, xml_escape(topic)))
            if ref:
                out.append("<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (num_pr, xml_escape("\u00a0[" + ref + "]")))
            if j != len(bits) - 1:
                out.append(
                    "<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>"
                    % (dot_pr, xml_escape("\u00a0\u00b7 "))
                )

    if lead:
        out.append(
            "<w:r><w:rPr>%s<w:b/><w:sz w:val=\"24\"/></w:rPr><w:t xml:space=\"preserve\">%s</w:t></w:r>"
            % (fonts, xml_escape(lead))
        )
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(
                "<w:r><w:rPr>%s<w:vertAlign w:val=\"superscript\"/></w:rPr>"
                "<w:footnoteReference w:id=\"%d\"/></w:r>"
                % (fonts, int(part) + 1)
            )
        elif part:
            add_text(part)
    return "".join(out)


AVENIR = '<w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/>'
AVENIR_LIGHT = (
    '<w:rFonts w:ascii="Avenir Next Ultra Light" w:hAnsi="Avenir Next Ultra Light" w:cs="Avenir Next Ultra Light"/>'
)
AVENIR_HEAVY = (
    '<w:rFonts w:ascii="Avenir Next Heavy" w:hAnsi="Avenir Next Heavy" w:cs="Avenir Next Heavy"/>'
)
# Em dashes are in the text font, so the line stays one sepia color. The star stays.
ORNAMENT = "\u2014\u2014\u2014\u2014\u2014 \u2727 \u2014\u2014\u2014\u2014\u2014"
# Body text is 12pt. These sizes are fractions of that, so a phone does not turn the chapter into a poster.
SEPIA = "B8A88E"
LABEL = "2B2B2B"
INK = "1F1F1F"
SLATE = "4A4A4A"


def docx_run(text, rpr):
    return "<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (rpr, xml_escape(text))


def docx_p(ppr, runs):
    return "<w:p><w:pPr>%s</w:pPr>%s</w:p>" % (ppr, runs)


def fleuron_paragraph():
    return docx_p(
        '<w:spacing w:before="360" w:after="0" w:line="288" w:lineRule="auto"/>'
        '<w:ind w:firstLine="0"/><w:jc w:val="center"/>'
        '<w:rPr><w:sz w:val="29"/></w:rPr>',
        docx_run(
            "❧",
            "<w:rPr>%s<w:b w:val=\"0\"/><w:sz w:val=\"29\"/><w:color w:val=\"%s\"/></w:rPr>" % (AVENIR, SEPIA),
        ),
    )


def write_docx(text, path, notes=None):
    import zipfile
    if notes is None:
        notes = []
    text, notes = renumber_notes(text, notes)
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = []
    started = False
    after_opener = False
    for i, para in enumerate(paras):
        prev = paras[i - 1] if i else ""
        nxt = paras[i + 1] if i + 1 < len(paras) else ""
        opener = is_opener_plan(nxt, para)
        parts = heading_parts(para)
        if parts:
            number, name = parts
            # The page starts with this paragraph. The ornament is its first line, so it
            # stays with «Глава». The chapter text breaks only after the closing ornament.
            head_pr = (
                '<w:pStyle w:val="Heading1"/>'
                '<w:spacing w:before="480" w:after="288" w:line="240" w:lineRule="auto"/>'
                '<w:ind w:firstLine="0"/><w:jc w:val="center"/>'
                '<w:rPr><w:sz w:val="28"/></w:rPr>'
            )
            if started:
                head_pr += "<w:pageBreakBefore/>"
            runs = docx_run(
                ORNAMENT,
                "<w:rPr>%s<w:b w:val=\"0\"/><w:i w:val=\"0\"/><w:spacing w:val=\"32\"/>"
                "<w:sz w:val=\"22\"/><w:szCs w:val=\"22\"/><w:color w:val=\"%s\"/></w:rPr>"
                % (AVENIR, SEPIA),
            )
            runs += "<w:r><w:br/></w:r>"
            # 4.5em between the ornament and «Глава». Word adds the previous margin, so this line is the gap.
            runs += docx_run(
                "\u00a0",
                "<w:rPr>%s<w:b w:val=\"0\"/><w:i w:val=\"0\"/><w:sz w:val=\"108\"/><w:szCs w:val=\"108\"/>"
                "<w:color w:val=\"%s\"/></w:rPr>" % (AVENIR, SEPIA),
            )
            runs += "<w:r><w:br/></w:r>"
            runs += docx_run(
                number,
                "<w:rPr>%s<w:b/><w:i w:val=\"0\"/><w:caps/>"
                "<w:sz w:val=\"28\"/><w:szCs w:val=\"28\"/><w:color w:val=\"%s\"/></w:rPr>"
                % (AVENIR_HEAVY, LABEL),
            )
            body.append(docx_p(head_pr, runs))
            body.append(docx_p(
                '<w:pStyle w:val="Heading2"/><w:keepNext w:val="0"/>'
                '<w:spacing w:before="0" w:after="672" w:line="240" w:lineRule="auto"/>'
                '<w:ind w:firstLine="0"/><w:jc w:val="center"/>'
                '<w:rPr><w:sz w:val="37"/></w:rPr>',
                docx_run(
                    name,
                    "<w:rPr>%s<w:b w:val=\"0\"/><w:i w:val=\"0\"/><w:spacing w:val=\"7\"/><w:sz w:val=\"37\"/><w:color w:val=\"%s\"/></w:rPr>"
                    % (AVENIR_LIGHT, INK),
                ),
            ))
            started = True
            if not opener:
                body.append(fleuron_paragraph())
                after_opener = True
            continue
        if is_opener_plan(para, prev):
            runs = docx_run(
                "❖",
                "<w:rPr>%s<w:b w:val=\"0\"/><w:i w:val=\"0\"/><w:sz w:val=\"23\"/><w:color w:val=\"%s\"/></w:rPr>"
                % (AVENIR, SEPIA),
            )
            plain = "<w:rPr>%s<w:b w:val=\"0\"/><w:i w:val=\"0\"/><w:sz w:val=\"23\"/><w:color w:val=\"%s\"/></w:rPr>" % (AVENIR, SLATE)
            bold = "<w:rPr>%s<w:b/><w:i w:val=\"0\"/><w:sz w:val=\"23\"/><w:color w:val=\"%s\"/></w:rPr>" % (AVENIR, LABEL)
            for topic, ref in plan_entries(para):
                chunk = ""
                if topic:
                    chunk += " " + topic
                if ref:
                    if chunk:
                        runs += docx_run(chunk, plain)
                        chunk = ""
                    runs += docx_run(" [%s]" % ref, bold)
                elif chunk:
                    runs += docx_run(chunk, plain)
            body.append(docx_p(
                '<w:pStyle w:val="ChapterPlan"/>'
                '<w:spacing w:before="0" w:after="360" w:line="384" w:lineRule="auto"/>'
                '<w:ind w:firstLine="0" w:left="0"/>'
                '<w:jc w:val="left"/>'
                '<w:rPr><w:sz w:val="23"/></w:rPr>',
                runs,
            ))
            body.append(fleuron_paragraph())
            after_opener = True
            started = True
            continue
        if is_chapter_heading(para):
            kind = "heading"
            pr = '<w:pStyle w:val="Heading1"/>'
            if opener:
                pr += '<w:spacing w:before="2400" w:after="200"/><w:jc w:val="center"/>'
                kind = "label"
            if started:
                pr += "<w:pageBreakBefore/>"
        elif is_plan(para):
            kind = "plan"
            pr = '<w:pStyle w:val="ChapterPlan"/>'
        elif is_subhead(para, nxt):
            kind = "subhead"
            pr = '<w:pStyle w:val="Subhead"/>'
        else:
            kind = "body"
            pr = '<w:pStyle w:val="Normal"/>'
        if after_opener and "pageBreakBefore" not in pr:
            pr += "<w:pageBreakBefore/>"
        after_opener = False
        started = True
        body.append(
            "<w:p><w:pPr>%s</w:pPr>%s</w:p>" % (pr, text_runs(para, kind))
        )
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
%s
<w:sectPr>
<w:pgSz w:w="11906" w:h="16838"/>
<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
</w:sectPr>
</w:body>
</w:document>
""" % "\n".join(body)
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal">
<w:name w:val="Normal"/>
<w:qFormat/>
<w:pPr><w:spacing w:after="360" w:line="240" w:lineRule="auto"/><w:ind w:firstLine="567"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/><w:sz w:val="24"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Heading1">
<w:name w:val="heading 1"/>
<w:basedOn w:val="Normal"/>
<w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="360" w:after="200"/><w:ind w:firstLine="0"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/><w:b/><w:sz w:val="36"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Heading2">
<w:name w:val="heading 2"/>
<w:basedOn w:val="Normal"/>
<w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="40" w:after="200"/><w:ind w:firstLine="0"/><w:outlineLvl w:val="1"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/><w:sz w:val="28"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Subhead">
<w:name w:val="Subhead"/>
<w:basedOn w:val="Normal"/>
<w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:ind w:firstLine="0"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/><w:b/><w:sz w:val="26"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="ChapterPlan">
<w:name w:val="Chapter plan"/>
<w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:before="80" w:after="160" w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/><w:i/><w:sz w:val="22"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="FootnoteText">
<w:name w:val="footnote text"/>
<w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="60" w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/><w:sz w:val="20"/></w:rPr>
</w:style>
</w:styles>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
%s
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    doc_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
%s
</Relationships>
"""
    footnote_override = ""
    footnote_rel = ""
    if notes:
        footnote_override = (
            '<Override PartName="/word/footnotes.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
        )
        footnote_rel = (
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" '
            'Target="footnotes.xml"/>'
        )
    content_types = content_types % footnote_override
    doc_rels = doc_rels % footnote_rel
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        if notes:
            zf.writestr("word/footnotes.xml", footnotes_xml(notes))
    return sum(1 for p in paras if is_chapter_heading(p))


def footnotes_xml(notes):
    fonts = '<w:rFonts w:ascii="Avenir Next" w:hAnsi="Avenir Next" w:cs="Avenir Next"/>'
    items = [
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>',
        '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>',
    ]
    for i, note in enumerate(notes, 1):
        items.append(
            '<w:footnote w:id="%d"><w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>'
            '<w:r><w:rPr>%s<w:vertAlign w:val="superscript"/></w:rPr><w:footnoteRef/></w:r>'
            '<w:r>%s<w:t xml:space="preserve"> %s</w:t></w:r></w:p></w:footnote>'
            % (i, fonts, '<w:rPr>%s<w:sz w:val="20"/></w:rPr>' % fonts, xml_escape(note))
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        + "\n".join(items)
        + "\n</w:footnotes>\n"
    )


def self_test():
    long_a = "А" * 80
    long_b = "Б" * 80
    got = clean_text(long_a + "\u2028" + long_b)
    assert got == long_a + "\n\n" + long_b + "\n", got

    got = clean_text("Воспита-\nние детей.")
    assert got == "Воспитание детей.\n", got

    got = clean_text("слово —\nдругое")
    assert "—" in got and "слово —" in got, got

    wrapped = "\n".join([
        "Это необходимое окисление крови после того как",
        "она из венозной превращается в яркокрасную дальше",
        "наполняющего грудь.",
        "9. Из млечных сосудов материал крови поступает в вены",
    ])
    got = clean_text(wrapped)
    parts = got.strip().split("\n\n")
    assert len(parts) == 2, got
    assert parts[0].startswith("Это необходимое") and parts[0].endswith("грудь."), parts[0]
    assert parts[1].startswith("9. Из млечных"), parts[1]

    crossed = "Это необходимое окисление крови после того как\fтакое продолжение той же фразы до конца."
    got = clean_text(crossed)
    assert got.count("\n\n") == 0, got
    assert "как такое продолжение" in got, got

    glued = "Конец фразы ещё не здесь.\fтой же фразы продолжение до точки."
    got = clean_text(glued)
    assert "\n\n" not in got.strip(), got
    assert "здесь. той же" in got, got

    listed = "Конец пункта.\nб) Следующий пункт начинается со строчной и идёт дальше."
    got = clean_text(listed)
    assert got.strip().split("\n\n")[1].startswith("б)"), got

    toc = "\n".join([
        "Глава И. Существенные свойства растительного",
        "организма. 67",
        "Глава VIII. Мускулы. Мускульное чувство.",
        "Орган голоса 132",
        "Глава III. Растительный организм в животном 76",
        "Часть психологическая",
        "А. Сознание",
        "Глава XIX. Процесс внимания 284",
    ])
    got = clean_text(toc)
    parts = got.strip().split("\n\n")
    assert parts[0] == "Глава И. Существенные свойства растительного организма. 67", parts
    assert parts[1] == "Глава VIII. Мускулы. Мускульное чувство. Орган голоса 132", parts
    assert parts[2].startswith("Глава III."), parts
    assert parts[3] == "Часть психологическая", parts
    assert parts[4] == "А. Сознание", parts
    assert parts[5].startswith("Глава XIX."), parts

    got = clean_text("положений науки,но только собрание. И отступления ,в область.")
    assert "науки, но только" in got and "отступления, в область" in got, got

    leaders = "\n".join([
        "Глава XXII. Припоминание. . . . , 338",
        "Глава XXI. Появление ощущения • 324",
        "Глава XXIII. Ассоциация представлений. • . . 346",
        "Глава XXV11I. Воображение пассивное. . . . 408",
        "Глава XV. значение привычек * . . . . 226",
        "Глава XXXI. Рассудочный процесс . 442",
    ])
    got = clean_text(leaders)
    parts = got.strip().split("\n\n")
    assert parts[0] == "Глава XXII. Припоминание 338", parts
    assert parts[1] == "Глава XXI. Появление ощущения 324", parts
    assert parts[2] == "Глава XXIII. Ассоциация представлений 346", parts
    assert parts[3] == "Глава XXV11I. Воображение пассивное 408", parts
    assert parts[4] == "Глава XV. значение привычек 226", parts
    assert parts[5] == "Глава XXXI. Рассудочный процесс 442", parts

    got = clean_text(". 2. Всякий, без сомнения, заметил это.\n\n20. .Всякий, наблюдая над собою.")
    assert got.strip().split("\n\n")[0].startswith("2. Всякий"), got
    assert "20. Всякий, наблюдая" in got, got

    got = clean_text(
        "Это конец первого пункта про целый организм. 3. Таким образом, вдумываясь внимательнее, видим иное.\n"
        "См. издание Leipzig. 1863. S. 4. Далее еще доктор пишет. Редактор Г. 3. Гинзбург."
    )
    parts = got.strip().split("\n\n")
    assert parts[0].endswith("организм."), parts
    assert parts[1].startswith("3. Таким образом"), parts
    assert any("S. 4. Далее" in p and "Г. 3. Гинзбург" in p for p in parts), parts

    got = clean_text("собрания сочиненийК. Д.Ушинского и всмысле, т.е. какаянибудь мысль к. приему.")
    assert "сочинений К. Д. Ушинского" in got, got
    assert "т. е." in got and "т.е." not in got, got
    assert "какая-нибудь" in got, got
    assert "к приему" in got, got

    got = clean_text(
        "в человеческой власти, щЗтазнает цель достижимою или нет.\n"
        "посылка, утвер-\n?кдающая, что достижение данной цели желательно."
    )
    assert "власти, признает цель" in got, got
    assert "утверждающая" in got and "?к" not in got, got

    got = clean_text(
        "вещество, облегающее почти Еесь остов человека и сверху.\n\n"
        "2. Хотя мускульное вещество облегает почти Еесь\n"
        "остов человека, но оно не составляет.\n"
        "ПЕДАГОГИЧЕСКАЯ АНТРОПОЛОГИЯ\n"
        "lacntb физиологическая\n"
        "Глава I"
    )
    assert got.count("почти весь остов") == 2 and "Еесь" not in got, got
    assert "Часть физиологическая" in got.split("\n\n") and "lacntb" not in got, got
    assert heading_parts("Глава I. Об организмах вообще") == (
        "Глава I",
        "Об организмах вообще",
    )
    assert heading_parts("Глава I. Об организмах вообще 61") is None

    named = "\n".join([
        "Глава I. Об организмах вообще 61",
        "Глава V. Потребность сна и отдыха 86",
        "Глава I",
        "ОБ ОРГАНИЗМАХ ВООБЩЕ",
        "Дальше текст этой главы про организмы.",
        "Глава V",
        "ПОТРЕБНОСТЬ ОТДЫХА И СНА",
        "Текст про сон.",
    ])
    got = clean_text(named)
    parts = got.strip().split("\n\n")
    assert "Глава I. Об организмах вообще" in parts, parts
    assert any(p.startswith("Дальше текст") for p in parts), parts
    assert "Глава V. Потребность сна и отдыха" in parts, parts
    assert is_chapter_heading("Глава I. Об организмах вообще")
    assert is_chapter_heading("Глава XXI. Что такое значит сознавать? Появление ощущения")
    assert not is_chapter_heading("Глава I. Об организмах вообще 61")

    missed = "\n".join([
        "Глава XXXIV. Постижение предметов и явлений, причин и законов 478",
        "Глава XLVIII. Противоречие дуализма и монизма 6^|",
        "Глава XXXIV М",
        "ПОСТИЖЕНИЕ ПРЕДМЕТОВ И ЯВЛЕНИИ, ПРИЧИН И ЗАКОНОВ Предметы умственные (1—4).— Общий вывод (23) 1. В предыдущих главах мы видели это.",
        "Постижение предметов",
        "Глава XLVIII",
        "ПРОТИВОРЕЧИЕ ДУАЛИЗМА И МОНИЗМА Мнение Лотце о дуализме (1—4).— Значение идей (5—12) 1. Не только психологически.",
    ])
    got = clean_text(missed)
    parts = got.strip().split("\n\n")
    assert "Глава XXXIV. Постижение предметов и явлений, причин и законов" in parts, parts
    assert "Глава XLVIII. Противоречие дуализма и монизма" in parts, parts
    assert any(p.startswith("1. В предыдущих") for p in parts), parts
    assert is_plan(next(p for p in parts if p.startswith("Предметы умственные")))
    assert plan_items(
        "Что такое воспитание? (1). — Определение организма (2 и 3). — Сила развития (4)."
    ) == [
        "Что такое воспитание? (1)",
        "Определение организма (2 и 3)",
        "Сила развития (4)",
    ]
    assert plan_sentence(
        "Что такое воспитание? (1). — Определение организма (2 и 3)."
    ) == "Что такое воспитание?\uE0111\uE010Определение организма\uE0112 и 3"
    peeled = peel_plan(
        "Значение навыка в учении (1 — 2). — Наш взгляд (3— 7).— Значение навыка (8) "
        "Уяснив природу привычки, обратимся теперь к нравственному и педагогическому ее значению в деле воспитания."
    )
    assert peeled[0].endswith("(8)"), peeled
    assert peeled[1].startswith("Уяснив природу"), peeled
    peeled = peel_plan(
        "Противоречие причины (1).— Противоречие идеи свободы (2) Противоречие причины "
        "1. Мы уже несколько знакомы с этим противоречием и можем идти дальше."
    )
    assert peeled[0].endswith("(2)"), peeled
    assert peeled[1] == "Противоречие причины", peeled
    assert peeled[2].startswith("1. Мы уже"), peeled
    assert is_subhead("Постижение предметов", "2. Всякий, без сомнения, заметил, что предметы различны по своей природе.")
    assert not is_subhead("Бенеке Фридрих-Эдуард")
    assert not is_subhead("Гуфеланд", "Христофор (1762) — врач — стр. 184. Дальше биография.")

    got = clean_text("Глава\n INCLUDEPICTURE \"http://x\" \\* MERGEFORMATINET \nТекст главы")
    assert "INCLUDEPICTURE" not in got and "Глава" in got and "Текст главы" in got, got

    body = "В" * 60
    pages = "\f".join("Ушинский\n%s\nконец" % body for _ in range(5))
    got = clean_text(pages)
    assert "Ушинский" not in got, got
    assert body in got and "конец" in got, got

    got = clean_text("См. R e a d далее")
    assert got == "См. Read далее\n", got

    one = clean_text("Глава\n12\nТекст")
    assert "12" not in one.split(), one

    preface = "Глава L. Что же такое сознание? 664\fПРЕДИСЛОВИЕ\nИскусство воспитания имеет ту особенность."
    got = clean_text(preface)
    assert "ПРЕДИСЛОВИЕ" in got.split("\n\n"), got

    assert is_chapter_heading("Глава I")
    assert is_chapter_heading("Глава XXXIV М")
    assert is_chapter_heading("ПРЕДИСЛОВИЕ")
    assert not is_chapter_heading("Глава I. Об организмах вообще 61")
    assert not is_chapter_heading("Предисловие И")

    sample = "\n".join([
        "Это признак теории искусства*. Но как было бы дальше в этой строке",
        "достаточно текста чтобы это был абзац книги а не заголовок.",
        "* «Где говорят в правилах». Mill.",
        "13",
    ])
    found = []
    got = clean_text(sample, "stars", found)
    assert len(found) == 1 and "Mill" in found[0], found
    assert "\uE0000\uE001" in got and "«Где говорят" not in got, got

    long_note = ["Слово о силе развития*. Дальше идёт обычный абзац этой страницы."]
    long_note.append("* Строка сноски про силу развития организма и ещё текст.")
    long_note += ["продолжение сноски %d без новой пометки." % i for i in range(17)]
    long_note.append("70")
    found = []
    got = clean_text("\n".join(long_note), "stars", found)
    assert len(found) == 1 and "Строка сноски" in found[0] and "продолжение сноски 16" in found[0], found
    assert "силе развития\uE0000\uE001" in got and "Строка сноски" not in got, got

    both = "\n".join([
        "Первая мысль*. Вторая мысль2* остаётся в абзаце и идёт дальше по строке.",
        "* Первая сноска.",
        "2* Вторая сноска.",
        "14",
    ])
    found = []
    got = clean_text(both, "stars", found)
    got, found = renumber_notes(got, found)
    assert found == ["Первая сноска.", "Вторая сноска."], found
    assert got.index("\uE0000\uE001") < got.index("\uE0001\uE001"), got

    numbered = "\n".join([
        "Слово о воспитании[1] продолжается здесь дальше по строке текста.",
        "1. См. Милль, логика.",
        "15",
    ])
    found = []
    got = clean_text(numbered, "numbers", found)
    assert found == ["См. Милль, логика."], found
    assert "[1]" not in got and "\uE0000\uE001" in got

    broken = "\f".join([
        "мышления, но ста-*\n* Текст сноски про слово.\n16",
        "новится вредною в этой книге.",
    ])
    found = []
    got = clean_text(broken, "stars", found)
    assert found == ["Текст сноски про слово."], found
    assert "становится\uE0000\uE001 вредною" in got, got
    assert "ста-" not in got

    signed = "\n".join([
        "все здание жизни*; но это декартовское пока",
        "необходимым периодом в процессе мышления, но ста-*",
        "новится вредною дальше по строке текста книги.",
        "* Oeuvres de Descartes.",
        "18 к. Д. Ушинсшш, т. VIII",
        "17",
    ])
    found = []
    got = clean_text(signed, "stars", found)
    assert found == ["Oeuvres de Descartes."], found
    assert "Ушинский" not in got and "Ушинсшш" not in " ".join(found)
    assert "становится вредною" in got, got
    assert "жизни\uE0000\uE001;" in got, got

    glued = "\f".join([
        "Воспитание? Ка-*\nжется, люди думали обо всем, кроме воспитания.",
        "тот, кто смотрел бы на человека только как на эконо-\n3 К. Д. Ушинский, т. VIII\n33",
        "мическую единицу и ничего больше в этой книге.",
    ])
    found = []
    got = clean_text(glued, "stars", found)
    assert "Кажется, люди" in got, got
    assert "экономическую единицу" in got, got
    assert "Ушинский" not in got

    orphan = "\f".join([
        "историю зарождений преступных и ве-\n3*\n3S",
        "ликих мыслей, историю развития всякой страсти.",
    ])
    found = []
    got = clean_text(orphan, "stars", found)
    assert found == [], found
    assert "великих мыслей" in got, got

    bare = "\n".join([
        "Строка без сноски внизу страницы, просто текст абзаца.",
        "2*",
        "18",
    ])
    found = []
    got = clean_text(bare, "stars", found)
    assert found == [], found
    assert "\uE000" not in got

    noise = "\n".join([
        "Конец указателя на этой странице книги.",
        "*r~' • С i",
        "ОГЛАВЛЕНИЕ",
        "Предисловие 11",
    ])
    found = []
    got = clean_text(noise, "stars", found)
    assert found == [], found
    assert "ОГЛАВЛЕНИЕ" in got, got
    print("ok")


def note_mode(argv):
    if argv and argv[-1] in ("stars", "numbers", "none"):
        return argv[:-1], argv[-1]
    return argv, "none"


def main(argv):
    if len(argv) == 2 and argv[1] == "--self-test":
        self_test()
        return 0
    argv, notes = note_mode(argv)
    if len(argv) == 3 and argv[1] == "preview":
        try:
            text, found = convert(argv[2], notes)
        except NeedTool as exc:
            print(exc.message, file=sys.stderr)
            return 2
        text, found = renumber_notes(text, found)
        paras = [p for p in text.split("\n\n") if p.strip()]
        print("paragraphs\t%d\t%d" % (len(paras), len(found)))

        def show_marks(para):
            return re.sub(r"\uE000(\d+)\uE001", lambda m: "[%d]" % (int(m.group(1)) + 1), para)

        for para in paras[:2]:
            shown = show_marks(para)
            if len(shown) > 600:
                shown = shown[:600] + "…"
            print("---")
            print(shown)
        return 0
    if len(argv) == 4 and argv[1] == "write":
        try:
            text, found = convert(argv[2], notes)
        except NeedTool as exc:
            print(exc.message, file=sys.stderr)
            return 2
        if not text.strip():
            print("nothing to write", file=sys.stderr)
            return 3
        open(argv[3], "w", encoding="utf-8").write(render_txt(text, found))
        n = len([p for p in text.split("\n\n") if p.strip()])
        print("%d paragraph(s), %d note(s)" % (n, len(found)))
        return 0
    if len(argv) == 4 and argv[1] == "write-docx":
        try:
            text, found = convert(argv[2], notes)
        except NeedTool as exc:
            print(exc.message, file=sys.stderr)
            return 2
        if not text.strip():
            print("nothing to write", file=sys.stderr)
            return 3
        n = write_docx(text, argv[3], found)
        print("%d chapter heading(s), %d note(s)" % (n, len(found)))
        return 0
    print("usage: to-txt.py preview|write|write-docx FILE [OUT] stars|numbers|none", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
