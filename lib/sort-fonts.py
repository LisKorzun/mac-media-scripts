#!/usr/bin/env python3
# Root font files -> Family/Family-Style.ext. Subfolders are not read.
import os
import re
import sys

EXTS = {".woff", ".woff2", ".ttf", ".otf", ".otc", ".ttc"}
HEX_TAIL = re.compile(r"(?:\.[0-9a-fA-F]{12,})+$")
DIGIT_TAIL = re.compile(r"\d{8,}$")
HASH_TOKEN = re.compile(r"^(?:[0-9a-fA-F]{12,}|\d{8,})$")
BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
WORDS = tuple(sorted({
    "extrablack", "extrabold", "extralight", "extrathin",
    "ultrablack", "ultrabold", "ultralight", "ultrathin",
    "semibold", "semilight", "demibold", "hairline",
    "black", "bold", "medium", "regular", "normal",
    "light", "thin", "heavy", "book", "roman",
    "italic", "oblique",
    "condensed", "compressed", "expanded", "extended",
    "narrow", "wide", "upright",
}, key=len, reverse=True))


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


def clean_stem(stem):
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
        item["family"] = sanitize(item["family"])
        item["style"] = sanitize(item["style"])
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
