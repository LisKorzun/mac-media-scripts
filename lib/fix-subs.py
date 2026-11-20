#!/usr/bin/env python3
# Fix lookalike letters in text subtitles, and turn all-caps English into sentences.
# Video and audio are copied.
import json, os, re, subprocess, sys, tempfile

# Cyrillic letters that look like Latin, and the Latin letter to use.
TO_LATIN = {
    "а": "a", "А": "A",
    "е": "e", "Е": "E",
    "о": "o", "О": "O",
    "с": "c", "С": "C",
    "р": "p", "Р": "P",
    "х": "x", "Х": "X",
    "у": "y", "У": "Y",
    "и": "u", "И": "U",
    "т": "m",
    "Т": "T",
    "і": "i",
}
TO_CYRILLIC = {
    "a": "а", "A": "А",
    "e": "е", "E": "Е",
    "o": "о", "O": "О",
    "c": "с", "C": "С",
    "p": "р", "P": "Р",
    "x": "х", "X": "Х",
    "y": "у", "Y": "У",
}
IMAGE = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
VOWELS = set("aeiouAEIOUаеёиоуыэюяАЕЁИОУЫЭЮЯ")
# Whole words that stay capitals inside an otherwise sentence-cased line.
ACRONYMS = {
    "AI", "ASAP", "ATM", "BBC", "CD", "CEO", "CIA", "CNN", "DIY", "DNA", "DVD",
    "EU", "FAQ", "FBI", "GIF", "GPS", "HD", "HDMI", "HBO", "HDR", "HIV", "ICU",
    "ID", "MRI", "MTV", "NASA", "NBA", "NFL", "NHL", "NYC", "OK", "PC", "PDF",
    "PTSD", "RIP", "SD", "TV", "UFO", "UK", "UN", "URL", "USA", "USB", "VIP",
    "WIFI", "WWII",
}
TAG_RE = re.compile(r"(<[^>\n]+>|\{[^}\n]*\})")
TS_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}")
INDEX_RE = re.compile(r"^\d+$")


def norm_lang(lang):
    lang = (lang or "").strip().lower()
    if lang in ("en", "eng", "english"):
        return "eng"
    if lang in ("ru", "rus", "russian"):
        return "rus"
    return ""


def map_char(ch, prev, nxt, lang):
    if lang == "eng":
        if ch == "І":
            if prev.isalpha():
                return "l"
            if nxt.isalpha() and nxt in VOWELS:
                return "l"
            return "I"
        return TO_LATIN.get(ch, ch)
    if lang == "rus":
        return TO_CYRILLIC.get(ch, ch)
    return ch


def fix_token(token, lang):
    chars = list(token)
    out = []
    for i, ch in enumerate(chars):
        prev = chars[i - 1] if i else ""
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        out.append(map_char(ch, prev, nxt, lang))
    return "".join(out)


def visible(text):
    return TAG_RE.sub("", text)


def mostly_caps(text):
    letters = [ch for ch in visible(text) if ch.isalpha()]
    if len(letters) < 2:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / float(len(letters)) >= 0.8


def map_visible(text, fn):
    parts = TAG_RE.split(text)
    out = []
    for part in parts:
        if part.startswith("<") or part.startswith("{"):
            out.append(part)
        else:
            out.append(fn(part))
    return "".join(out)


def restore_i(text):
    return re.sub(r"\bi(?:'(?:m|ll|d|ve))?\b", lambda m: "I" + m.group(0)[1:], text)


def restore_marks(text):
    def word(m):
        key = m.group(0).upper()
        return key if key in ACRONYMS else m.group(0)

    text = re.sub(r"\b[A-Za-z]{2,}\b", word, text)
    return re.sub(r"(?<=\d)(\s*)(am|pm)\b", lambda m: m.group(1) + m.group(2).upper(), text, flags=re.I)


def fold_chars(text, cap):
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            out.append(ch)
            i += 1
            j = i
            while j < len(text) and text[j] in " \t":
                j += 1
            if j < len(text) and text[j] in "-–—♪>":
                cap = True
            continue
        if ch.isalpha():
            out.append(ch.upper() if cap else ch)
            cap = False
        else:
            out.append(ch)
            if ch in ".!?…":
                cap = True
            elif ch == ":":
                prev = "".join(out).rstrip()
                if not prev or not prev[-1].isdigit():
                    cap = True
        i += 1
    return "".join(out), cap


def sentence_case(text):
    parts = TAG_RE.split(text)
    cap = True
    buf = []
    for part in parts:
        if part.startswith("<") or part.startswith("{"):
            buf.append(part)
            continue
        folded, cap = fold_chars(part.lower(), cap)
        buf.append(folded)
    body = map_visible("".join(buf), restore_i)
    return map_visible(body, restore_marks)


def is_meta(line):
    s = line.strip()
    return bool(INDEX_RE.match(s) or TS_RE.search(s))


def fold_block(block):
    lines = block.split("\n")
    text_idx = [i for i, line in enumerate(lines) if line.strip() and not is_meta(line)]
    if not text_idx:
        return block
    text = "\n".join(lines[i] for i in text_idx)
    if not mostly_caps(text):
        return block
    folded = sentence_case(text).split("\n")
    if len(folded) != len(text_idx):
        return block
    for i, line in zip(text_idx, folded):
        lines[i] = line
    return "\n".join(lines)


def fold_english_caps(text):
    newline = "\r\n" if "\r\n" in text else "\n"
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"(\n{2,})", norm)
    out = []
    for block in blocks:
        if block.startswith("\n"):
            out.append(block)
        else:
            out.append(fold_block(block))
    result = "".join(out)
    if newline == "\r\n":
        result = result.replace("\n", "\r\n")
    return result


def fix_text(text, lang):
    lang = norm_lang(lang)
    if not lang:
        return text, 0, "skip-lang"
    parts = re.split(r"(\s+)", text)
    looked = []
    for part in parts:
        if not part or part.isspace():
            looked.append(part)
        else:
            looked.append(fix_token(part, lang))
    fixed = "".join(looked)
    if lang == "eng":
        fixed = fold_english_caps(fixed)
    orig = re.split(r"(\s+)", text)
    new = re.split(r"(\s+)", fixed)
    changed = 0
    if len(orig) == len(new):
        for a, b in zip(orig, new):
            if a and not a.isspace() and a != b:
                changed += 1
    elif fixed != text:
        changed = 1
    return fixed, changed, "ok"


def probe(path):
    raw = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=index,codec_name,codec_type",
            "-show_entries", "stream_tags=language",
            "-of", "json", path,
        ],
        text=True,
    )
    data = json.loads(raw)
    subs = []
    for stream in data.get("streams") or []:
        if stream.get("codec_type") != "subtitle":
            continue
        codec = stream.get("codec_name") or ""
        lang = ((stream.get("tags") or {}).get("language")) or ""
        kind = "image" if codec in IMAGE else "text"
        subs.append({
            "index": stream.get("index"),
            "codec": codec,
            "lang": norm_lang(lang),
            "raw_lang": lang,
            "kind": kind,
        })
    return subs


def extract_srt(path, index, dest):
    subprocess.check_call(
        ["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0:%d" % index, "-f", "srt", dest],
    )


def report(path):
    rows = []
    subs = probe(path)
    if not subs:
        return rows
    tmp = tempfile.mkdtemp(prefix="fix-subs-")
    try:
        n = 0
        for sub in subs:
            if sub["kind"] != "text":
                rows.append((sub, 0, "skip-image"))
                continue
            if not sub["lang"]:
                rows.append((sub, 0, "skip-lang"))
                continue
            srt = os.path.join(tmp, "%d.srt" % n)
            n += 1
            extract_srt(path, sub["index"], srt)
            text = open(srt, encoding="utf-8", errors="replace").read()
            _, words, status = fix_text(text, sub["lang"])
            rows.append((sub, words, status))
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)
    return rows


def apply_file(path):
    subs = probe(path)
    if not any(s["kind"] == "text" and s["lang"] for s in subs):
        notes = []
        for sub in subs:
            if sub["kind"] == "image":
                notes.append((sub, 0, "skip-image"))
            elif sub["kind"] == "text":
                notes.append((sub, 0, "skip-lang"))
        return 0, notes
    ext = os.path.splitext(path)[1].lower()
    sub_codec = "mov_text" if ext in (".mp4", ".m4v", ".mov") else "srt"
    tmp = tempfile.mkdtemp(prefix="fix-subs-")
    total = 0
    notes = []
    try:
        inputs = []
        for sub in subs:
            if sub["kind"] != "text" or not sub["lang"]:
                if sub["kind"] == "image":
                    notes.append((sub, 0, "skip-image"))
                elif sub["kind"] == "text":
                    notes.append((sub, 0, "skip-lang"))
                continue
            raw_path = os.path.join(tmp, "raw-%d.srt" % len(inputs))
            extract_srt(path, sub["index"], raw_path)
            text = open(raw_path, encoding="utf-8", errors="replace").read()
            fixed, words, status = fix_text(text, sub["lang"])
            total += words
            notes.append((sub, words, status))
            out_path = os.path.join(tmp, "fix-%d.srt" % len(inputs))
            open(out_path, "w", encoding="utf-8").write(fixed)
            inputs.append((sub, out_path))
        if total == 0:
            return 0, notes
        dest = path + ".fixing" + ext
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", path]
        for _, extra in inputs:
            cmd += ["-i", extra]
        cmd += ["-map", "0:V", "-map", "0:a"]
        si = 0
        text_i = 0
        for sub in subs:
            if sub["kind"] == "text" and sub["lang"]:
                cmd += ["-map", "%d:0" % (text_i + 1)]
                cmd += ["-c:s:%d" % si, sub_codec]
                cmd += ["-metadata:s:s:%d" % si, "language=%s" % sub["lang"]]
                text_i += 1
            elif sub["kind"] in ("image", "text"):
                cmd += ["-map", "0:%d" % sub["index"]]
                cmd += ["-c:s:%d" % si, "copy"]
                if sub["raw_lang"]:
                    cmd += ["-metadata:s:s:%d" % si, "language=%s" % sub["raw_lang"]]
            else:
                continue
            si += 1
        if si:
            cmd += ["-disposition:s:0", "default"]
        cmd += ["-c:v", "copy", "-c:a", "copy", dest]
        subprocess.check_call(cmd)
        os.replace(dest, path)
        return total, notes
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass
        leftover = path + ".fixing" + ext
        if os.path.exists(leftover):
            os.remove(leftover)


def self_test():
    got, n, status = fix_text("Thе Evеning", "eng")
    assert got == "The Evening" and n == 2 and status == "ok", (got, n, status)
    got, n, _ = fix_text("этoт мoмeнт", "rus")
    assert got == "этот момент" and n == 2, got
    got, n, _ = fix_text("Yои knоw", "eng")
    assert got == "You know", got
    got, n, _ = fix_text("таgiс", "eng")
    assert got == "magic", got
    got, n, _ = fix_text("привет", "rus")
    assert got == "привет" and n == 0, got
    got, n, status = fix_text("The", "")
    assert status == "skip-lang" and n == 0
    got, n, _ = fix_text("Іt аІІ Іife", "eng")
    assert got == "It all life", got
    got, n, _ = fix_text("HELLO THERE. I AM FINE.", "eng")
    assert got == "Hello there. I am fine." and n == 4, (got, n)
    got, _, _ = fix_text("JOHN WENT TO LONDON.", "eng")
    assert got == "John went to london.", got
    got, _, _ = fix_text("MARY: WHAT ARE YOU DOING?", "eng")
    assert got == "Mary: What are you doing?", got
    got, _, _ = fix_text("HELLO THERE\nMY FRIEND.", "eng")
    assert got == "Hello there\nmy friend.", got
    got, _, _ = fix_text("- WHAT?\n- NOTHING.", "eng")
    assert got == "- What?\n- Nothing.", got
    got, _, _ = fix_text("<i>HELLO THERE.</i>", "eng")
    assert got == "<i>Hello there.</i>", got
    got, _, _ = fix_text("THE FBI IS HERE. OK, MEET AT 8 AM.", "eng")
    assert got == "The FBI is here. OK, meet at 8 AM.", got
    got, _, _ = fix_text("I said NO.", "eng")
    assert got == "I said NO.", got
    got, _, _ = fix_text("ПРИВЕТ. КАК ДЕЛА?", "rus")
    assert got == "ПРИВЕТ. КАК ДЕЛА?", got
    got, _, _ = fix_text("THЕ EVENING IS HERE.", "eng")
    assert got == "The evening is here.", got
    srt = (
        "1\n00:00:01,000 --> 00:00:03,000\nHELLO THERE.\nI AM FINE.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nAlready fine.\n"
    )
    got, n, _ = fix_text(srt, "eng")
    assert "Hello there.\nI am fine." in got and "Already fine." in got and n > 0, got
    assert "00:00:01,000 --> 00:00:03,000" in got, got
    print("ok")


def main(argv):
    if len(argv) == 2 and argv[1] == "--self-test":
        self_test()
        return 0
    if len(argv) != 3 or argv[1] not in ("count", "apply"):
        print("usage: fix-subs.py count|apply FILE", file=sys.stderr)
        return 2
    path = argv[2]
    if argv[1] == "count":
        rows = report(path)
        if not rows:
            print("none")
            return 0
        for sub, words, status in rows:
            print("%s\t%s\t%s\t%s" % (sub["kind"], sub["lang"] or sub["raw_lang"] or "-", words, status))
        return 0
    total, notes = apply_file(path)
    print("fixed %d word(s)" % total)
    for sub, _, status in notes:
        if status == "skip-lang":
            print("no rus/eng mark (%s): left as is" % (sub["raw_lang"] or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
