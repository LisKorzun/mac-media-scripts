#!/usr/bin/env python3
# Root font files -> Family/Family-Style.ext. Subfolders are not read.
import os
import re
import sys
import urllib.parse

EXTS = {".woff", ".woff2", ".ttf", ".otf", ".otc", ".ttc"}
HEX_TAIL = re.compile(r"(?:\.[0-9a-fA-F]{12,})+$")
DIGIT_TAIL = re.compile(r"\d{8,}$")
HASH_TOKEN = re.compile(r"^(?:[0-9a-fA-F]{12,}|\d{8,})$")
BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
CANON = {
    "extrablack": "ExtraBlack",
    "extrabold": "ExtraBold",
    "extralight": "ExtraLight",
    "extrathin": "ExtraThin",
    "ultrablack": "UltraBlack",
    "ultrabold": "UltraBold",
    "ultralight": "UltraLight",
    "ultrathin": "UltraThin",
    "semibold": "SemiBold",
    "semilight": "SemiLight",
    "demibold": "DemiBold",
    "hairline": "Hairline",
    "black": "Black",
    "bold": "Bold",
    "medium": "Medium",
    "regular": "Regular",
    "normal": "Normal",
    "light": "Light",
    "thin": "Thin",
    "heavy": "Heavy",
    "book": "Book",
    "roman": "Roman",
    "italic": "Italic",
    "oblique": "Oblique",
    "condensed": "Condensed",
    "compressed": "Compressed",
    "expanded": "Expanded",
    "extended": "Extended",
    "narrow": "Narrow",
    "wide": "Wide",
    "upright": "Upright",
}
WORDS = tuple(sorted(CANON, key=len, reverse=True))


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def field(text):
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def sanitize(text):
    text = BAD.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    if text in {"", ".", ".."}:
        return ""
    return text


def is_style(token):
    low = token.casefold()
    if not low or HASH_TOKEN.fullmatch(token):
        return False
    i = 0
    while i < len(low):
        for word in WORDS:
            if low.startswith(word, i):
                i += len(word)
                break
        else:
            return False
    return True


def camel(text):
    parts = [part for part in re.sub(r"\s+", " ", text).strip().split(" ") if part]
    if not parts:
        return ""
    out = []
    for word in parts:
        if word[:1].islower():
            word = word[:1].upper() + word[1:]
        out.append(word)
    return "".join(out)


def canon_style(token):
    low = token.casefold()
    i = 0
    parts = []
    while i < len(low):
        matched = False
        for word in WORDS:
            if low.startswith(word, i):
                parts.append(CANON[word])
                i += len(word)
                matched = True
                break
        if not matched:
            if token[:1].islower():
                return token[:1].upper() + token[1:]
            return token
    return "".join(parts)


def flat_name(text):
    letters = [char for char in text if char.isalpha()]
    return len(letters) > 1 and not any(char.isupper() for char in letters[1:])


def ps_family(path):
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return ""
    try:
        font = TTFont(path, fontNumber=0)
    except Exception:
        return ""
    try:
        ps = ""
        for rec in font["name"].names:
            if rec.nameID != 6:
                continue
            try:
                val = rec.toUnicode().strip()
            except Exception:
                continue
            if not val:
                continue
            ps = val
            if rec.platformID == 3:
                break
    finally:
        font.close()
    if not ps:
        return ""
    family, _style = split_family(ps.replace(" ", "").replace("_", "-"))
    return family


def prettify(family, style, path):
    family = camel(family)
    if style:
        style = canon_style(style)
    if family and flat_name(family):
        ps = camel(ps_family(path))
        folded = lambda name: re.sub(r"[\s-]", "", name).casefold()
        if ps and folded(ps) == folded(family):
            family = ps
    return family, style


def clean_stem(stem):
    stem = urllib.parse.unquote(stem).replace("%", "")
    prev = None
    while stem != prev:
        prev = stem
        stem = HEX_TAIL.sub("", stem)
    stem = DIGIT_TAIL.sub("", stem)
    stem = stem.replace("_", "-")
    stem = BAD.sub("", stem)
    stem = re.sub(r"-{2,}", "-", stem).strip("-. ")
    return stem


def peel_glued(token):
    best = None
    for cut in range(1, len(token)):
        if not token[cut].isupper() or token[cut - 1].isupper():
            continue
        suffix = token[cut:]
        if is_style(suffix) and (best is None or len(suffix) > len(best[0])):
            best = (suffix, cut)
    if not best:
        return token, ""
    return token[:best[1]], best[0]


def split_family(stem):
    parts = [part for part in stem.split("-") if part]
    styles = []
    while len(parts) >= 2 and is_style(parts[-1]):
        styles.append(parts.pop())
    styles.reverse()
    if parts:
        head, glued = peel_glued(parts[-1])
        if glued:
            parts[-1] = head
            styles.insert(0, glued)
    return "-".join(parts), "".join(styles)


def peel_shared(items, known):
    groups = {}
    for item in items:
        if item["style"] or "-" not in item["stem"]:
            continue
        head, _, tail = item["stem"].rpartition("-")
        if not head or not tail or HASH_TOKEN.fullmatch(tail):
            continue
        groups.setdefault(head.casefold(), []).append((item, head, tail))
    for key, rows in groups.items():
        if len(rows) < 2 and key not in known:
            continue
        spelling = known.get(key, rows[0][1])
        for item, _head, tail in rows:
            item["family"] = spelling
            item["style"] = tail


def canon_names(items, existing):
    buckets = {}
    for item in items:
        buckets.setdefault(item["family"].casefold(), []).append(item["family"])
    canon = {}
    for key, names in buckets.items():
        if key in existing:
            canon[key] = existing[key]
        else:
            canon[key] = max(set(names), key=lambda name: (names.count(name), name))
    for item in items:
        item["family"] = canon[item["family"].casefold()]


def build(folder):
    existing = {}
    files = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if os.path.isdir(path) and not os.path.islink(path):
            existing[name.casefold()] = name
            continue
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        ext = os.path.splitext(name)[1]
        if ext.lower() not in EXTS:
            continue
        files.append(name)

    items = []
    skips = []
    for name in files:
        path = os.path.join(folder, name)
        stem, ext = os.path.splitext(name)
        stem = sanitize(clean_stem(stem))
        if not stem:
            skips.append((name, "no name"))
            continue
        family, style = split_family(stem)
        family = sanitize(family)
        style = sanitize(style)
        if not family:
            skips.append((name, "no name"))
            continue
        items.append({
            "src": name,
            "path": path,
            "stem": stem,
            "family": family,
            "style": style,
            "ext": ext,
        })

    known = dict(existing)
    for item in items:
        if item["style"]:
            known.setdefault(item["family"].casefold(), item["family"])
    peel_shared(items, known)
    for item in items:
        family, style = prettify(
            sanitize(item["family"]),
            sanitize(item["style"]),
            item["path"],
        )
        item["family"] = family
        item["style"] = style
    items = [item for item in items if item["family"]]
    canon_names(items, existing)

    pairs = []
    for item in items:
        base = item["family"] + ("-" + item["style"] if item["style"] else "")
        filename = base + item["ext"]
        rel = item["family"] + "/" + filename
        src_abs = os.path.abspath(os.path.join(folder, item["src"]))
        dest_abs = os.path.abspath(os.path.join(folder, rel))
        if src_abs == dest_abs:
            continue
        pairs.append((item["src"], rel))

    moves = []
    used = {}
    for src, rel in sorted(pairs, key=lambda row: (row[1].casefold(), row[0].casefold())):
        key = rel.casefold()
        if key in used:
            skips.append((src, "same name as " + used[key]))
            continue
        dest_abs = os.path.join(folder, rel)
        src_abs = os.path.join(folder, src)
        if os.path.exists(dest_abs) and os.path.abspath(dest_abs) != os.path.abspath(src_abs):
            skips.append((src, "already exists"))
            continue
        used[key] = src
        moves.append((src, rel))
    return moves, skips, len(files)


def apply(folder, moves):
    done = 0
    for src, rel in moves:
        src_abs = os.path.join(folder, src)
        dest_abs = os.path.join(folder, rel)
        if os.path.exists(dest_abs) and os.path.abspath(dest_abs) != os.path.abspath(src_abs):
            print("skip\t%s\talready exists" % field(src))
            continue
        parent = os.path.dirname(dest_abs)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            os.rename(src_abs, dest_abs)
        except OSError as err:
            print("skip\t%s\t%s" % (field(src), field(err.strerror or "failed")))
            continue
        done += 1
    print("done\t%d" % done)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in {"plan", "apply"}:
        die("usage: sort-fonts.py plan|apply FOLDER")
    action, folder = sys.argv[1], sys.argv[2]
    if not os.path.isdir(folder):
        die("folder not found")
    moves, skips, seen = build(folder)
    if action == "plan":
        if seen == 0:
            sys.exit(2)
        for src, rel in moves:
            print("move\t%s\t%s" % (field(src), field(rel)))
        for src, reason in skips:
            print("skip\t%s\t%s" % (field(src), field(reason)))
        return
    apply(folder, moves)


if __name__ == "__main__":
    main()
