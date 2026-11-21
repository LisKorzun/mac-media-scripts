#!/usr/bin/env python3
# woff/woff2 in this folder -> ttf with the same name. Subfolders are not read.
import os
import sys
import tempfile

EXTS = {".woff", ".woff2"}


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def field(text):
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def need_deps():
    try:
        import fontTools  # noqa: F401
        import brotli  # noqa: F401
    except ImportError:
        die("Need fonttools and brotli.\npip3 install fonttools brotli")


def curves_to_glyf(font):
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable

    order = list(font.getGlyphOrder())
    glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = {}
    glyphset = font.getGlyphSet()
    upem = font["head"].unitsPerEm or 1000
    err = max(1, upem / 1000)
    for name in order:
        pen = TTGlyphPen(glyphset)
        glyphset[name].draw(Cu2QuPen(pen, max_err=err, reverse_direction=True))
        glyf.glyphs[name] = pen.glyph()
    for name in order:
        glyph = glyf.glyphs[name]
        if glyph.numberOfContours:
            glyph.recalcBounds(glyf)
    font["glyf"] = glyf
    font["loca"] = newTable("loca")
    for tag in ("CFF ", "CFF2", "VORG", "DSIG"):
        if tag in font:
            del font[tag]
    maxp = font["maxp"]
    maxp.tableVersion = 0x00010000
    maxp.maxZones = 1
    maxp.maxTwilightPoints = 0
    maxp.maxStorage = 0
    maxp.maxFunctionDefs = 0
    maxp.maxInstructionDefs = 0
    maxp.maxStackElements = 0
    maxp.maxSizeOfInstructions = 0
    maxp.maxComponentElements = 0
    maxp.maxComponentDepth = 0
    font.sfntVersion = "\x00\x01\x00\x00"
    maxp.recalc(font)


def convert(src, dest):
    from fontTools.ttLib import TTFont

    font = TTFont(src)
    try:
        font.flavor = None
        if "glyf" not in font:
            curves_to_glyf(font)
        elif "DSIG" in font:
            del font["DSIG"]
        font.save(dest)
    finally:
        font.close()


def build(folder):
    files = []
    for name in os.listdir(folder):
        if name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in EXTS:
            files.append(name)

    chosen = {}
    skipped_dupes = []
    for name in sorted(files):
        stem, ext = os.path.splitext(name)
        key = stem.casefold()
        prev = chosen.get(key)
        if prev is None or ext.lower() == ".woff2":
            if prev is not None:
                skipped_dupes.append(prev)
            chosen[key] = name
        else:
            skipped_dupes.append(name)

    makes = []
    skips = []
    for name in skipped_dupes:
        skips.append((name, "same ttf as another file"))
    for name in sorted(chosen.values(), key=str.casefold):
        stem = os.path.splitext(name)[0]
        dest = stem + ".ttf"
        dest_abs = os.path.join(folder, dest)
        if os.path.exists(dest_abs):
            skips.append((name, "already exists"))
            continue
        makes.append((name, dest))
    return makes, skips, len(files)


def apply(folder, makes):
    done = 0
    for src, dest in makes:
        src_abs = os.path.join(folder, src)
        dest_abs = os.path.join(folder, dest)
        if os.path.exists(dest_abs):
            print("skip\t%s\talready exists" % field(src))
            continue
        fd, tmp = tempfile.mkstemp(prefix=".to-ttf-", suffix=".ttf", dir=folder)
        os.close(fd)
        try:
            convert(src_abs, tmp)
            os.replace(tmp, dest_abs)
        except Exception as err:
            if os.path.exists(tmp):
                os.remove(tmp)
            print("skip\t%s\t%s" % (field(src), field(str(err))))
            continue
        done += 1
    print("done\t%d" % done)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in {"plan", "apply"}:
        die("usage: to-ttf.py plan|apply FOLDER")
    need_deps()
    action, folder = sys.argv[1], sys.argv[2]
    if not os.path.isdir(folder):
        die("folder not found")
    makes, skips, seen = build(folder)
    if action == "plan":
        if seen == 0:
            sys.exit(2)
        for src, dest in makes:
            print("make\t%s\t%s" % (field(src), field(dest)))
        for src, reason in skips:
            print("skip\t%s\t%s" % (field(src), field(reason)))
        return
    apply(folder, makes)


if __name__ == "__main__":
    main()
