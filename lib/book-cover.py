#!/usr/bin/env python3
# Portrait + title -> cover.jpg in the style of the owner's book covers.
import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CANVAS_W = 1200
CANVAS_H = 1800
# Measured on the 682×1024 samples.
PHOTO_RATIO = 759 / 1024
MARGIN_RATIO = 60 / 682
CAP_RATIO = 51 / 1024
GAP_OF_CAP = 34 / 42
PAD_RATIO = 32 / 1024
PORTRAIT_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".heif")
FONT_DIRS = (
    Path.home() / "Library/Fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
)


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def font_names(path):
    data = path.read_bytes()
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return []
    count = struct.unpack(">H", data[4:6])[0]
    names = []
    for index in range(count):
        tag, _csum, offset, length = struct.unpack(">4sIII", data[12 + index * 16:28 + index * 16])
        if tag != b"name":
            continue
        table = data[offset:offset + length]
        _fmt, records, storage = struct.unpack(">HHH", table[:6])
        for rec in range(records):
            plat, enc, _lang, name_id, ln, noff = struct.unpack(
                ">HHHHHH", table[6 + rec * 12:18 + rec * 12]
            )
            if name_id not in (1, 2, 4, 16):
                continue
            raw = table[storage + noff:storage + noff + ln]
            if plat in (0, 3) or enc in (1, 10):
                try:
                    text = raw.decode("utf-16-be")
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode("latin1", "replace")
            names.append(text)
        break
    return names


def is_angelica(names):
    for name in names:
        folded = name.casefold().strip()
        if folded == "angelica" or folded.startswith("angelica ") or folded.startswith("angelica-"):
            return True
    return False


def font_rank(names):
    blob = " ".join(names).casefold()
    if "italic" in blob or "oblique" in blob:
        return 3
    if "regular" in blob or "roman" in blob or "book" in blob or "normal" in blob:
        return 0
    if "bold" in blob or "black" in blob:
        return 2
    return 1


def find_angelica():
    found = []
    for root in FONT_DIRS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            try:
                names = font_names(path)
            except (OSError, struct.error):
                continue
            if is_angelica(names):
                found.append((font_rank(names), path))
    if not found:
        return None
    found.sort(key=lambda item: (item[0], str(item[1]).lower()))
    return found[0][1]


def find_portrait(folder):
    by_name = {}
    for name in os.listdir(folder):
        by_name[name.casefold()] = name
    for ext in PORTRAIT_EXTS:
        key = ("portrait" + ext).casefold()
        if key in by_name:
            return os.path.join(folder, by_name[key])
    return ""


def guess_title(folder_name):
    match = re.match(
        r"^(?P<author>.+?)\s+[-–—]\s+(?P<title>.+?)(?:\s*\[(?P<narrator>[^\]]+)\])?\s*$",
        folder_name,
    )
    if not match:
        return ""
    return match.group("title").strip()


def open_portrait(path):
    try:
        image = Image.open(path)
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")
    except Exception:
        if Path(path).suffix.lower() not in {".heic", ".heif"}:
            die("Cannot open portrait: %s" % os.path.basename(path))
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", path, "--out", tmp],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            die("Cannot open portrait: %s" % os.path.basename(path))
        image = Image.open(tmp)
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)


def cover_crop(image, width, height):
    src_w, src_h = image.size
    scale = max(width / src_w, height / src_h)
    resized = image.resize(
        (max(width, round(src_w * scale)), max(height, round(src_h * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def explicit_lines(title):
    if "/" not in title:
        line = re.sub(r"\s+", " ", title).strip().upper()
        return [line] if line else None
    parts = [re.sub(r"\s+", " ", part).strip(" ") for part in title.split("/")]
    if not parts or any(not part for part in parts) or len(parts) > 3:
        die("Use 1 to 3 lines. Separate them with /")
    return [part.upper() for part in parts]


def words_of(title):
    return [word for word in re.sub(r"\s+", " ", title).strip().upper().split(" ") if word]


def best_split(words, lines, width_of):
    best = None
    best_width = None

    def walk(start, left, acc):
        nonlocal best, best_width
        if left == 1:
            chosen = acc + [" ".join(words[start:])]
            width = max(width_of(line) for line in chosen)
            if best_width is None or width < best_width:
                best = chosen
                best_width = width
            return
        remain = len(words) - start
        for take in range(1, remain - left + 2):
            walk(start + take, left - 1, acc + [" ".join(words[start:start + take])])

    if len(words) < lines:
        return None
    walk(0, lines, [])
    return best


def line_box(font, line):
    box = font.getbbox(line)
    return box[2] - box[0], box[3] - box[1], box[0], box[1]


def block_fits(font, lines, max_width, max_height):
    widths = []
    heights = []
    for line in lines:
        width, height, _ox, _oy = line_box(font, line)
        widths.append(width)
        heights.append(height)
    cap = line_box(font, "Н")[1]
    gap = cap * GAP_OF_CAP if len(lines) > 1 else 0
    block = sum(heights) + gap * (len(lines) - 1)
    return max(widths) <= max_width and block <= max_height


def choose_lines(title, font_path, canvas_w, canvas_h):
    manual = explicit_lines(title)
    words = words_of(title) if manual is None else []
    if manual is None and not words:
        die("Enter a title")
    margin = canvas_w * MARGIN_RATIO
    max_width = canvas_w - 2 * margin
    photo_h = round(canvas_h * PHOTO_RATIO)
    max_height = (canvas_h - photo_h) - 2 * (canvas_h * PAD_RATIO)
    preferred = cap_size(font_path, canvas_h * CAP_RATIO)

    def width_of(line):
        font = ImageFont.truetype(str(font_path), preferred)
        return line_box(font, line)[0]

    if manual:
        splits = [manual]
    else:
        splits = []
        for count in (1, 2, 3):
            if len(words) < count:
                continue
            chosen = best_split(words, count, width_of)
            if chosen:
                splits.append(chosen)
        for chosen in splits:
            font = ImageFont.truetype(str(font_path), preferred)
            if block_fits(font, chosen, max_width, max_height):
                return chosen, preferred
    lines = splits[-1]
    low, high = 8, preferred
    fit = low
    while low <= high:
        mid = (low + high) // 2
        font = ImageFont.truetype(str(font_path), mid)
        if block_fits(font, lines, max_width, max_height):
            fit = mid
            low = mid + 1
        else:
            high = mid - 1
    return lines, fit


def cap_size(font_path, target):
    low, high = 8, 400
    best = low
    while low <= high:
        mid = (low + high) // 2
        font = ImageFont.truetype(str(font_path), mid)
        cap = line_box(font, "Н")[1]
        if cap <= target:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def render(portrait_path, title, font_path, dest):
    lines, size = choose_lines(title, font_path, CANVAS_W, CANVAS_H)
    font = ImageFont.truetype(str(font_path), size)
    photo_h = round(CANVAS_H * PHOTO_RATIO)
    margin = round(CANVAS_W * MARGIN_RATIO)
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (255, 255, 255))
    photo = cover_crop(open_portrait(portrait_path), CANVAS_W, photo_h)
    canvas.paste(photo, (0, 0))
    draw = ImageDraw.Draw(canvas)
    metrics = [line_box(font, line) for line in lines]
    cap = line_box(font, "Н")[1]
    gap = cap * GAP_OF_CAP if len(lines) > 1 else 0
    block = sum(item[1] for item in metrics) + gap * (len(lines) - 1)
    y = photo_h + ((CANVAS_H - photo_h) - block) / 2
    for line, (_width, height, ox, oy) in zip(lines, metrics):
        draw.text((margin - ox, y - oy), line, font=font, fill=(0, 0, 0))
        y += height + gap
    canvas.save(dest, "JPEG", quality=95, subsampling=0)
    return lines


def cmd_check_font():
    path = find_angelica()
    if path is None:
        die(
            "Need font Angelica.\n"
            "Install it once: open the font file and click Install Font,\n"
            "or copy Angelica.otf / Angelica.ttf into ~/Library/Fonts"
        )
    print(path)


def cmd_guess():
    folder = os.getcwd()
    portrait = find_portrait(folder)
    print("portrait\t%s" % (os.path.basename(portrait) if portrait else ""))
    print("title\t%s" % guess_title(os.path.basename(folder)))


def cmd_lines():
    title = os.environ.get("BOOK_TITLE", "").strip()
    font = find_angelica()
    if font is None:
        die("Need font Angelica")
    lines, _size = choose_lines(title, font, CANVAS_W, CANVAS_H)
    for index, line in enumerate(lines, 1):
        print("%d\t%s" % (index, line))


def cmd_write():
    title = os.environ.get("BOOK_TITLE", "").strip()
    portrait = os.environ.get("PORTRAIT", "").strip()
    if not title:
        die("Enter a title")
    if not portrait or not os.path.isfile(portrait):
        die("Portrait file is missing")
    font = find_angelica()
    if font is None:
        die("Need font Angelica")
    dest = os.path.join(os.getcwd(), "cover.jpg")
    lines = render(portrait, title, font, dest)
    for line in lines:
        print(line)
    print("cover.jpg")


def main():
    if len(sys.argv) < 2:
        die("usage: book-cover.py check-font|guess|lines|write")
    action = sys.argv[1]
    if action == "check-font":
        cmd_check_font()
    elif action == "guess":
        cmd_guess()
    elif action == "lines":
        cmd_lines()
    elif action == "write":
        cmd_write()
    else:
        die("unknown action: %s" % action)


if __name__ == "__main__":
    main()
