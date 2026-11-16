#!/usr/bin/env python3
# Find a Kinopoisk film, suggest a short title and a genre, save the poster as png.
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
GQL = "https://graphql.kinopoisk.ru/graphql/?operationName=SuggestSearch"
QUERY = (
    "query SuggestSearch($keyword: String!, $yandexCityId: Int, $limit: Int, "
    "$withUserData: Boolean!) { suggest(keyword: $keyword) { top(yandexCityId: "
    "$yandexCityId, limit: $limit) { topResult { global { ...SuggestMovieItem "
    "...SuggestPersonItem ...SuggestCinemaItem ...SuggestMovieListItem __typename } "
    "__typename } movies { movie { ...SuggestMovieItem __typename } __typename } "
    "persons { person { ...SuggestPersonItem __typename } __typename } cinemas "
    "{ cinema { ...SuggestCinemaItem __typename } __typename } movieLists "
    "{ movieList { ...SuggestMovieListItem __typename } __typename } __typename } "
    "__typename } } fragment ShortImage on Image { avatarsUrl fallbackUrl __typename } "
    "fragment MovieHdVerticalPoster on Movie { gallery { posters { hdVertical: "
    "vertical(override: OTT_WHEN_EXISTS) { ...ShortImage __typename } __typename } "
    "__typename } __typename } fragment MovieKpVerticalPoster on Movie { gallery "
    "{ posters { kpVertical: vertical { ...ShortImage __typename } __typename } "
    "__typename } __typename } fragment TicketOption on Movie { ticketOption "
    "{ purchasable releaseAnnounce { available releaseDate { accuracy date __typename } "
    "__typename } __typename } __typename } fragment SuggestMovieItem on Movie { id "
    "contentId title { russian original __typename } rating { kinopoisk { isActive value "
    "__typename } __typename } ...MovieHdVerticalPoster ...MovieKpVerticalPoster "
    "viewOption { buttonText isAvailableOnline: isWatchable(filter: {anyDevice: false, "
    "anyRegion: false}) purchasabilityStatus contentPackageToBuy { billingFeatureName "
    "__typename } subscriptionBadge { image { avatarsUrl __typename } __typename } type "
    "availabilityAnnounce { groupPeriodType announcePromise availabilityDate type "
    "__typename } __typename } ...TicketOption userData @include(if: $withUserData) "
    "{ isPlannedToWatch __typename } ... on Film { productionYear __typename } "
    "... on TvSeries { releaseYears { end start __typename } __typename } "
    "... on TvShow { releaseYears { end start __typename } __typename } "
    "... on MiniSeries { releaseYears { end start __typename } __typename } __typename } "
    "fragment SuggestPersonItem on Person { id name originalName birthDate poster "
    "{ avatarsUrl fallbackUrl __typename } __typename } fragment SuggestCinemaItem on "
    "Cinema { id ctitle: title city { id name geoId __typename } __typename } "
    "fragment SuggestMovieListItem on MovieListMeta { id cover { avatarsUrl __typename } "
    "coverBackground { avatarsUrl __typename } name url description movies(limit: 0) "
    "{ total __typename } __typename }"
)


def norm(text):
    text = unicodedata.normalize("NFC", text or "").casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return " ".join(text.split())


def loose(query, name):
    if not query or not name:
        return False
    return name == query or name.startswith(query + " ")


def title_hit(film, ru, en):
    names = (norm(film["ru"]), norm(film["en"]))
    for query in (norm(ru), norm(en)):
        for name in names:
            if loose(query, name):
                return True
    return False


def sized(url):
    if url.startswith("//"):
        url = "https:" + url
    url = url.split("?", 1)[0].rstrip("/")
    url = re.sub(r"/(?:\d+x\d+|orig|x\d+)$", "", url)
    return url + "/600x900"


def poster_url(movie):
    posters = ((movie.get("gallery") or {}).get("posters") or {})
    for key in ("kpVertical", "hdVertical"):
        raw = ((posters.get(key) or {}).get("avatarsUrl")) or ""
        if raw:
            return sized(raw)
    return ""


def film_year(movie):
    if movie.get("productionYear"):
        return str(movie["productionYear"])
    years = movie.get("releaseYears") or {}
    if isinstance(years, dict):
        return str(years.get("start") or years.get("end") or "")
    if isinstance(years, list) and years:
        first = years[0] or {}
        return str(first.get("start") or first.get("end") or "")
    return ""


def as_film(movie):
    if not movie or movie.get("__typename") != "Film":
        return None
    url = poster_url(movie)
    year = film_year(movie)
    title = movie.get("title") or {}
    ru = (title.get("russian") or "").replace("\t", " ").strip()
    en = (title.get("original") or "").replace("\t", " ").strip()
    if not url or not ru:
        return None
    return {
        "id": movie.get("id"),
        "ru": ru,
        "en": en or "-",
        "year": year or "-",
        "url": url,
    }


def gql(keyword):
    body = json.dumps({
        "operationName": "SuggestSearch",
        "variables": {
            "keyword": keyword,
            "yandexCityId": 6,
            "limit": 10,
            "withUserData": False,
        },
        "query": QUERY,
    }).encode("utf-8")
    req = urllib.request.Request(GQL, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": UA,
        "service-id": "25",
        "source-id": "1",
        "x-preferred-language": "ru",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("errors") and not payload.get("data"):
        raise RuntimeError("search failed")
    return payload


def films_for(keyword):
    payload = gql(keyword)
    top = (((payload.get("data") or {}).get("suggest") or {}).get("top") or {})
    found = []
    global_hit = ((top.get("topResult") or {}).get("global")) or {}
    movie = as_film(global_hit)
    if movie:
        found.append(movie)
    for item in top.get("movies") or []:
        movie = as_film((item or {}).get("movie"))
        if movie:
            found.append(movie)
    return found


JUNK = {
    "1080p", "720p", "2160p", "480p", "4k", "uhd", "hdr", "sdr", "10bit", "8bit",
    "bluray", "bdrip", "brrip", "bdremux", "remux", "hdrip", "dvdrip", "webrip",
    "web", "dl", "webdl", "web-dl", "webdlrip", "web-dlrip", "hdtv", "dvb",
    "dvbrip", "x264", "x265", "h264", "h265", "hevc", "avc", "xvid", "divx",
    "dts", "aac", "ac3", "eac3", "truehd", "flac", "atmos", "rus", "eng", "ukr",
    "lostfilm", "hdclub", "extended", "unrated", "directors", "director",
    "director's", "cut", "edition", "theatrical", "hybrid", "proper", "rep",
    "rip", "apple", "lgc2", "dub", "dubbed", "sub", "subs", "limited",
    "internal", "remastered",
}
ROMAN_TO_INT = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
}
GENRE_RULES = (
    ("ужас", "Фильмы ужасов"),
    ("мульт", "Мультфильм"),
    ("анимац", "Мультфильм"),
    ("фантаст", "Фантастика"),
    ("фэнтези", "Фэнтези"),
    ("фэнтез", "Фэнтези"),
    ("боевик", "Боевик"),
    ("триллер", "Триллер"),
    ("детектив", "Детектив"),
    ("криминал", "Криминал"),
    ("мелодрам", "Мелодрама"),
    ("романт", "Мелодрама"),
    ("комед", "Комедия"),
    ("приключен", "Приключения"),
    ("воен", "Военный"),
    ("семейн", "Семейный"),
    ("драм", "Драма"),
)
WIKI_UA = "tag-movie/1.0 (local; film tags)"
STRUCT_NAME = re.compile(r"^(?P<ru>.+?) \[(?P<en>[^\[\]]+)\] (?P<year>\d{4})$")


def cell(text):
    text = (text or "").replace("\t", " ").replace("\n", " ").strip()
    return " ".join(text.split())


def clean_piece(text):
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[/:\[\]?*\"<>|\\]", " ", text)
    return " ".join(text.split())


def is_junk(token):
    key = token.casefold().strip("[](){}")
    if key in JUNK or re.fullmatch(r"\d{3,4}p", key):
        return True
    return bool(re.search(r"(?:bdrip|brrip|web-?dl|bluray|remux|x26[45]|h26[45])", key))


def split_ru(ru):
    main = re.split(r"\s*[:—–]\s*", ru or "", maxsplit=1)[0].strip()
    match = re.match(r"^(.*\S)\s+([1-9]\d?)$", main)
    if match:
        return clean_piece(match.group(1)), match.group(2)
    return clean_piece(main), ""


def split_en(en):
    if not en or en == "-":
        return ""
    main = re.split(r"\s*[:—–]\s*", en, maxsplit=1)[0].strip()
    main = re.sub(
        r"(?i)\s+(?:part|pt)\.?\s+(?:[ivx]{1,4}|\d{1,2})$", "", main
    ).strip()
    main = re.sub(r"\s+(?:[IVX]{1,4}|\d{1,2})$", "", main).strip()
    return clean_piece(main) or clean_piece(en)


def filename_query(name):
    base = os.path.splitext(os.path.basename(name))[0]
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"(?i)[_ ](?:apple|lgc2)$", "", base)
    structured = STRUCT_NAME.match(base)
    if structured:
        ru, part = split_ru(structured.group("ru"))
        return ru, clean_piece(structured.group("en")), structured.group("year"), part
    paren_years = []

    def grab_paren(match):
        paren_years.extend(re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", match.group(1)))
        return " "

    text = re.sub(r"\(([^)]*)\)", grab_paren, base)
    text = re.sub(r"\[([^\]]*)\]", grab_paren, text)
    raw = [tok for tok in re.split(r"[._\s+'’·]+", text) if tok]
    year = ""
    year_at = None
    for index, tok in enumerate(raw):
        if re.fullmatch(r"(?:19|20)\d{2}", tok) and 1900 <= int(tok) <= 2035:
            year = tok
            year_at = index
            break
    if year_at is None:
        title_tokens = raw
        if paren_years:
            year = paren_years[0]
    else:
        title_tokens = raw[:year_at]
    cleaned = []
    for tok in title_tokens:
        if is_junk(tok):
            if cleaned:
                break
            continue
        cleaned.append(tok)
    part = ""
    if len(cleaned) >= 2:
        last = cleaned[-1]
        if re.fullmatch(r"[1-9]\d?", last):
            part = last
            cleaned.pop()
        elif last.casefold() in ROMAN_TO_INT and re.fullmatch(r"[IVXivx]+", last):
            part = ROMAN_TO_INT[last.casefold()]
            cleaned.pop()
    return clean_piece(" ".join(cleaned)), "", year, part


def keywords_for(ru, en, year, part):
    raw = [ru]
    if part:
        raw.append("%s %s" % (ru, part))
    if year:
        raw.append("%s %s" % (ru, year))
    if en and en != ru:
        raw.append(en)
        if year:
            raw.append("%s %s" % (en, year))
    found = []
    for item in raw:
        item = " ".join((item or "").split())
        if item and item != year and item not in found:
            found.append(item)
    return found


def year_hits(ordered, ru, en, year):
    return [
        film for film in ordered
        if film["year"] == str(year) and title_hit(film, ru, en)
    ]


def ends_token(name, token):
    return bool(token) and (name == token or name.endswith(" " + token))


def sequel_hit(film, part):
    if not part:
        return False
    roman = ""
    for key, value in ROMAN_TO_INT.items():
        if value == part:
            roman = key
            break
    names = (norm(film["ru"]), norm(film["en"]))
    for name in names:
        if ends_token(name, part) or (roman and ends_token(name, roman)):
            return True
    return False


def collect(ru, en, year, part=""):
    seen = {}
    ordered = []
    for keyword in keywords_for(ru, en, year, part):
        try:
            batch = films_for(keyword)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError):
            if ordered:
                break
            raise
        for film in batch:
            if film["id"] in seen:
                continue
            seen[film["id"]] = film
            ordered.append(film)
        if year and len(year_hits(ordered, ru, en, year)) == 1:
            break
    return ordered


def search(ru, en, year):
    ordered = collect(ru, en, year)
    hits = year_hits(ordered, ru, en, year) if year else []
    if not ordered:
        print("NONE")
        return
    if len(hits) == 1:
        print("SURE")
        write_row(hits[0])
    print("LIST")
    ranked = hits + [film for film in ordered if film not in hits]
    for film in ranked[:8]:
        write_row(film)


def write_row(film):
    sys.stdout.write("%s\t%s\t%s\t%s\n" % (film["ru"], film["en"], film["year"], film["url"]))


def map_genre(text):
    folded = norm(text)
    for needle, label in GENRE_RULES:
        if norm(needle) in folded:
            return label
    return ""


def choose_genre(description, infobox):
    desc = norm(description)
    box = norm(infobox)
    if "мульт" in desc or "анимац" in desc or "мульт" in box or "анимац" in box:
        return "Мультфильм"
    return map_genre(infobox) or map_genre(description)


def wiki_get(params):
    url = "https://ru.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    delay = 1.0
    for attempt in range(4):
        req = urllib.request.Request(url, headers={
            "User-Agent": WIKI_UA,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def wiki_plain(text):
    text = re.sub(r"<ref[^>]*>.*?</ref>", " ", text or "", flags=re.I | re.S)
    text = re.sub(r"<ref[^>]*/>", " ", text, flags=re.I)
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def infobox_genre(text):
    match = re.search(r"(?im)^\s*\|\s*жанр(?:ы)?\s*=\s*(.+)$", text or "")
    if not match:
        return ""
    return wiki_plain(match.group(1))


def page_genre(title, text):
    folded = norm(title)
    if "мульт" in folded or "анимац" in folded:
        return "Мультфильм"
    return choose_genre("", infobox_genre(text))


def page_matches_film(title, ru):
    page = norm(title)
    film = norm(ru)
    main = norm(split_ru(ru)[0])
    if film and film in page:
        return True
    if not main or main not in page:
        return False
    if len(main) < 4:
        return False
    return True


def wiki_genre(ru, year):
    queries = [
        '"%s" %s фильм' % (ru, year),
        "%s %s фильм" % (ru, year),
        '"%s" фильм' % ru,
    ]
    try:
        seen = set()
        for query in queries:
            data = wiki_get({
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": "4",
                "gsrnamespace": "0",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "rvsection": "0",
                "format": "json",
                "utf8": "1",
            })
            pages = ((data.get("query") or {}).get("pages")) or {}
            ordered = sorted(pages.values(), key=lambda page: page.get("index") or 99)
            found_here = False
            for page in ordered:
                title = page.get("title") or ""
                if title in seen:
                    continue
                seen.add(title)
                low = title.casefold()
                if (
                    "франшиз" in low
                    or "саундтрек" in low
                    or "серия фильмов" in low
                    or low.startswith("список")
                    or not page_matches_film(title, ru)
                ):
                    continue
                revs = page.get("revisions") or []
                text = ""
                if revs:
                    text = (((revs[0].get("slots") or {}).get("main") or {}).get("*")) or ""
                genre = page_genre(title, text)
                if genre:
                    return genre
                found_here = True
            if found_here:
                return ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, ValueError):
        return ""
    return ""


def short_english(film, file_en, file_ru):
    kp = split_en(film["en"])
    base_ru, _part = split_ru(film["ru"])
    if file_en and file_ru and norm(base_ru) == norm(file_ru):
        return clean_piece(file_en)
    if not kp or norm(kp) == norm(base_ru):
        return ""
    return kp


def write_proposal(film, file_part, file_en, file_ru, use_file_part, with_genre):
    base_ru, part = split_ru(film["ru"])
    if (
        use_file_part
        and not part
        and file_part
        and not re.search(r"(?i)\s(?:[1-9]\d?|[ivx]{1,4})$", base_ru)
    ):
        part = file_part
    short_en = short_english(film, file_en, file_ru)
    year = film["year"] if re.fullmatch(r"\d{4}", film["year"] or "") else ""
    genre = wiki_genre(film["ru"], year) if with_genre and year else ""
    full_en = "" if film["en"] in ("", "-") else film["en"]
    cells = [base_ru, short_en, part, year, genre, film["url"], film["ru"], full_en]
    sys.stdout.write("\t".join(cell(item) for item in cells) + "\n")


def propose(filename):
    query, file_en, year, file_part = filename_query(filename)
    ru = query or file_en
    en = file_en or query
    if not ru:
        print("NONE")
        return
    ordered = collect(ru, en, year, file_part)
    if not ordered:
        print("NONE")
        return
    hits = year_hits(ordered, ru, en, year) if year else []
    sure = hits[0] if len(hits) == 1 else None
    if sure is None and year:
        near = []
        for film in ordered:
            if not title_hit(film, ru, en) or not re.fullmatch(r"\d{4}", film["year"] or ""):
                continue
            if abs(int(film["year"]) - int(year)) > 1:
                continue
            kp_part = split_ru(film["ru"])[1]
            if file_part and kp_part and kp_part != file_part:
                continue
            near.append(film)
        if len(near) == 1:
            sure = near[0]
    if sure is None and not year:
        titled = [film for film in ordered if title_hit(film, ru, en)]
        seq = [film for film in titled if sequel_hit(film, file_part)]
        if len(seq) == 1:
            sure = seq[0]
        elif len(titled) == 1 and not file_part:
            sure = titled[0]
    if sure is not None and not re.fullmatch(r"\d{4}", sure["year"] or ""):
        sure = None
    if sure is not None:
        print("SURE")
        write_proposal(sure, file_part, file_en, query, True, True)
    ranked = []
    if sure is not None:
        ranked.append(sure)
    for film in hits + ordered:
        if film not in ranked:
            ranked.append(film)
    print("LIST")
    for film in ranked[:8]:
        write_proposal(film, file_part, file_en, query, False, False)


def fetch(url, dest):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.kinopoisk.ru/",
        "Accept": "image/jpeg,image/png,image/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if data[:3] == b"\xff\xd8\xff":
        suffix = ".jpg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        suffix = ".png"
    else:
        sys.stderr.write("Downloaded file is not a picture.\n")
        raise SystemExit(1)
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.write(data)
    handle.close()
    try:
        folder = os.path.dirname(dest)
        if folder:
            os.makedirs(folder, exist_ok=True)
        subprocess.check_call(
            ["sips", "-s", "format", "png", handle.name, "--out", dest],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        try:
            os.remove(handle.name)
        except OSError:
            pass
    if not os.path.isfile(dest) or os.path.getsize(dest) < 1000:
        sys.stderr.write("Could not convert the picture.\n")
        raise SystemExit(1)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(2)
    cmd = sys.argv[1]
    try:
        if cmd == "search" and len(sys.argv) == 5:
            search(sys.argv[2], sys.argv[3], sys.argv[4])
            return
        if cmd == "propose" and len(sys.argv) == 3:
            propose(sys.argv[2])
            return
        if cmd == "genre" and len(sys.argv) == 4:
            print(wiki_genre(sys.argv[2], sys.argv[3]) or "NONE")
            return
        if cmd == "fetch" and len(sys.argv) == 4:
            fetch(sys.argv[2], sys.argv[3])
            return
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError):
        raise SystemExit(1)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
