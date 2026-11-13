#!/usr/bin/env python3
# Fix lookalike letters in text subtitles. Video and audio are copied.
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


def fix_text(text, lang):
    lang = norm_lang(lang)
    if not lang:
        return text, 0, "skip-lang"
    parts = re.split(r"(\s+)", text)
    changed = 0
    out = []
    for part in parts:
        if not part or part.isspace():
            out.append(part)
            continue
        new = fix_token(part, lang)
        if new != part:
            changed += 1
        out.append(new)
    return "".join(out), changed, "ok"


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
