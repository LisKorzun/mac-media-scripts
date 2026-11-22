#!/usr/bin/env python3
# Find a Kinopoisk film, suggest a short title and a genre, save the poster as png.
import html as html_lib
import http.cookiejar
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


SHOW_TYPES = {"TvSeries", "TvShow", "MiniSeries"}


def as_show(movie):
    if not movie or movie.get("__typename") not in SHOW_TYPES:
        return None
    title = movie.get("title") or {}
    ru = (title.get("russian") or "").replace("\t", " ").strip()
    en = (title.get("original") or "").replace("\t", " ").strip()
    year = film_year(movie)
    if not ru or not re.fullmatch(r"\d{4}", year or ""):
        return None
    return {
        "id": movie.get("id"),
        "ru": ru,
        "en": en or "-",
        "year": year,
        "url": poster_url(movie),
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


def titles_for(keyword, cast):
    payload = gql(keyword)
    top = (((payload.get("data") or {}).get("suggest") or {}).get("top") or {})
    found = []
    global_hit = ((top.get("topResult") or {}).get("global")) or {}
    movie = cast(global_hit)
    if movie:
        found.append(movie)
    for item in top.get("movies") or []:
        movie = cast((item or {}).get("movie"))
        if movie:
            found.append(movie)
    return found


def films_for(keyword):
    return titles_for(keyword, as_film)


def shows_for(keyword):
    return titles_for(keyword, as_show)


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
    ("мульт", "Мультфильмы"),
    ("анимац", "Мультфильмы"),
    ("биограф", "Биографии"),
    ("всей семь", "Для всей семьи"),
    ("семейн", "Для всей семьи"),
    ("психоз", "Психоз"),
    ("психологическ", "Психоз"),
    ("фантаст", "Фантастика"),
    ("триллер", "Триллеры"),
    ("комед", "Комедии"),
    ("катастроф", "Катастрофы"),
    ("драм", "Драмы"),
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


def search_show(keyword):
    ordered = []
    seen = set()
    for show in shows_for(keyword):
        if show["id"] in seen:
            continue
        seen.add(show["id"])
        ordered.append(show)
    if not ordered:
        print("NONE")
        return
    hits = [show for show in ordered if title_hit(show, keyword, keyword)]
    if len(hits) == 1:
        print("SURE")
        write_show_row(hits[0])
    print("LIST")
    ranked = hits + [show for show in ordered if show not in hits]
    for show in ranked[:8]:
        write_show_row(show)


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


def write_show_row(show):
    sys.stdout.write(
        "%s\t%s\t%s\t%s\t%s\n"
        % (show["ru"], show["en"], show["year"], show["url"], show["id"])
    )


def map_genre(text):
    folded = norm(text)
    for needle, label in GENRE_RULES:
        token = norm(needle)
        if re.search(r"(?<![0-9a-zа-я])%s" % re.escape(token), folded):
            return label
    return ""


def choose_genre(description, infobox):
    desc = norm(description)
    box = norm(infobox)
    if "мульт" in desc or "анимац" in desc or "мульт" in box or "анимац" in box:
        return "Мультфильмы"
    return map_genre(infobox) or map_genre(description)


def wiki_get(params, lang="ru"):
    url = "https://%s.wikipedia.org/w/api.php?" % lang + urllib.parse.urlencode(params)
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
        return "Мультфильмы"
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


def wiki_genre(ru, year, kind="film"):
    if kind == "show":
        queries = [
            '"%s" %s сериал' % (ru, year),
            "%s %s сериал" % (ru, year),
            '"%s" сериал' % ru,
        ]
    else:
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


def plain_html(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return " ".join(html_lib.unescape(text).split())


def http_text(opener, url, data=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Accept": "text/html",
        "Accept-Language": "ru",
    })
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        final = resp.geturl()
    head = raw[:500].decode("latin-1", "replace").lower()
    charset = "cp1251" if ("windows-1251" in head or "cp1251" in head) else "utf-8"
    return final, raw.decode(charset, "replace")


_KP_OPENER = None


def kp_opener():
    global _KP_OPENER
    if _KP_OPENER is None:
        jar = http.cookiejar.CookieJar()
        _KP_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return _KP_OPENER


def kp_get(url):
    opener = kp_opener()
    final, text = http_text(opener, url)
    if "sso.passport" in final or text.lstrip().startswith("<body></body><script"):
        final, text = http_text(opener, url)
    return text


def episode_title(ru, en):
    ru = (ru or "").strip()
    en = (en or "").strip()
    if ru and en and norm(ru) != norm(en):
        return "%s [%s]" % (ru, en)
    return ru or en


def kp_episode_titles(page):
    text = html_lib.unescape(page or "")
    found = {}
    links = re.compile(
        r'href="[^"]*?/season-(\d+)/episode-(\d+)"[^>]*>(.*?)</a>',
        re.I | re.S,
    )
    for match in links.finditer(text):
        tail = text[match.end():match.end() + 800]
        english = re.search(
            r'class=["\']episodesOriginalName["\'][^>]*>(.*?)</span>',
            tail,
            re.I | re.S,
        )
        nxt = re.search(r'href="[^"]*?/season-\d+/episode-\d+"', tail)
        en = ""
        if english and (not nxt or english.start() < nxt.start()):
            en = plain_html(english.group(1))
        ru = plain_html(match.group(3))
        if episode_title(ru, en):
            found[(int(match.group(1)), int(match.group(2)))] = (ru, en)
    return found


def kp_episode_text(page):
    match = re.search(
        r"<h2[^>]*>\s*\d+\s*сезон,\s*\d+\s*серия\b.*?</h2>\s*<p[^>]*>(.*?)</p>",
        page or "",
        re.I | re.S,
    )
    if not match:
        return ""
    return plain_html(match.group(1))


def kp_season_counts(page):
    found = {}
    text = html_lib.unescape(page or "")
    for season, episode in re.findall(r"/season-(\d+)/episode-(\d+)", text):
        season = int(season)
        episode = int(episode)
        found[season] = max(found.get(season, 0), episode)
    return found


def kp_notes(film_id, season, wanted, titles_only=False):
    counts = {}
    notes = {}
    titles = {}
    try:
        page = kp_get("https://www.kinopoisk.ru/film/%s/episodes/" % film_id)
        counts = kp_season_counts(page)
        all_titles = kp_episode_titles(page)
        for number in wanted:
            title = all_titles.get((int(season), int(number)))
            if title:
                titles[number] = title
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        counts = {}
    if titles_only:
        return notes, counts, titles
    for number in wanted:
        try:
            page = kp_get(
                "https://www.kinopoisk.ru/series/%s/season-%s/episode-%s/"
                % (film_id, season, number)
            )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        text = kp_episode_text(page)
        if text:
            notes[number] = text
    return notes, counts, titles


def kt_blocked(final, text):
    return "/robot/" in (final or "") or "captcha_input" in (text or "")


def kt_post(query):
    try:
        data = urllib.parse.urlencode({"text": query}, encoding="cp1251").encode("ascii")
    except UnicodeEncodeError:
        return "", ""
    opener = urllib.request.build_opener()
    try:
        return http_text(opener, "https://www.kino-teatr.ru/search/", data)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "", ""


def kt_choose(page, ru, en, year):
    best = None
    best_score = 0
    seen = set()
    for match in re.finditer(
        r'href="(?:https?://www\.kino-teatr\.ru)?(/kino/movie/([a-z0-9-]+)/(\d+)/)',
        page or "",
        re.I,
    ):
        key = (match.group(2), match.group(3))
        if key in seen:
            continue
        seen.add(key)
        around = norm(plain_html(page[max(0, match.start() - 160):match.end() + 240]))
        score = 0
        if ru and norm(ru) in around:
            score += 3
        if en and en not in ("-", ru) and norm(en) in around:
            score += 2
        if year and str(year) in around:
            score += 2
        if score > best_score:
            best_score = score
            best = key
    if best_score < 3:
        return None
    return best


def kt_parse(page):
    chunk = page or ""
    match = re.search(r'class="big_content_block">(.*)', chunk, re.I | re.S)
    if match:
        chunk = match.group(1)
    end = len(chunk)
    for marker in ('class="footer"', 'id="footer"', "дополнительная информация"):
        at = chunk.find(marker)
        if at > 0:
            end = min(end, at)
    chunk = chunk[:end]
    chunk = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.I)
    chunk = re.sub(r"</(?:p|div|h\d|li|tr)>", "\n", chunk, flags=re.I)
    chunk = re.sub(r"<[^>]+>", "", chunk)
    chunk = html_lib.unescape(chunk)
    notes = {}
    started = set()
    current = None
    buf = []
    head = re.compile(r"^(\d+)(?:\s*-\s*[а-я])?\s*сери[яию]\b[\s.:|–-]*(.*)$", re.I)

    def flush():
        if current is None:
            return
        text = " ".join(buf).strip()
        if text:
            notes[current] = text

    for line in chunk.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        found = head.match(line)
        if found:
            flush()
            current = int(found.group(1))
            if current in started:
                if current == 1 and started != {1}:
                    return {}
                current = None
                buf = []
                continue
            started.add(current)
            buf = []
            rest = found.group(2).strip(" |–-:")
            if len(rest) > 40:
                buf.append(rest)
            continue
        if current is not None:
            buf.append(line)
    flush()
    return notes


def kt_notes(ru, en, year):
    for query in (ru, en):
        if not query or query == "-":
            continue
        final, page = kt_post(query)
        if not page or kt_blocked(final, page):
            continue
        chosen = kt_choose(page, ru, en, year)
        if not chosen:
            continue
        section, film_id = chosen
        opener = urllib.request.build_opener()
        url = "https://www.kino-teatr.ru/kino/movie/%s/%s/content/" % (section, film_id)
        try:
            final, content = http_text(opener, url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        if kt_blocked(final, content):
            continue
        notes = kt_parse(content)
        if notes:
            return notes
    return {}


def kt_pick(notes, season, episode, counts):
    if season <= 1:
        return notes.get(episode, "")
    if any(step not in counts for step in range(1, season)):
        return ""
    absolute = sum(counts[step] for step in range(1, season)) + episode
    return notes.get(absolute, "")


def sub_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "ru",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def sub_year(value):
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def sub_pick(hits, ru, en, year):
    best = None
    best_score = 0
    for hit in hits or []:
        src = hit.get("_source") or {}
        if src.get("post_type") != "series":
            continue
        start = sub_year(src.get("year"))
        if year and start and start != str(year):
            continue
        title_ru = src.get("title_ru") or ""
        title_en = src.get("title_en") or src.get("title_orig") or ""
        score = 0
        if ru and norm(title_ru) == norm(ru):
            score += 4
        elif ru and loose(norm(ru), norm(title_ru)):
            score += 2
        if en and en not in ("-", ru) and norm(title_en) == norm(en):
            score += 3
        elif en and en not in ("-", ru) and loose(norm(en), norm(title_en)):
            score += 1
        if year and start == str(year):
            score += 2
        if score > best_score:
            best_score = score
            best = src
    if best_score < 4:
        return None
    return best


def sub_show(ru, en, year):
    for query in (ru, en):
        if not query or query == "-":
            continue
        url = "https://2sub.movie/wp-json/yo/v1/search?" + urllib.parse.urlencode({"q": query})
        try:
            data = sub_get(url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            continue
        chosen = sub_pick(data.get("hits") or [], ru, en, year)
        if chosen and chosen.get("slug"):
            return chosen
    return None


def sub_text(row):
    for key in ("overview", "description", "ruOverview", "plot"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def sub_episodes(slug):
    slug = (slug or "").strip().strip("/")
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        return {}
    opener = urllib.request.build_opener()
    _final, page = http_text(opener, "https://2sub.movie/series/%s/" % slug)
    match = re.search(r"this\.data = (\[.*?\]);", page or "", re.S)
    if not match:
        return {}
    try:
        rows = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    found = {}
    for row in rows:
        try:
            season = int(row.get("season_number"))
            number = int(row.get("episode_number"))
        except (TypeError, ValueError):
            continue
        ru_title = (row.get("ruTitle") or row.get("displayTitle") or "").strip()
        en_title = (row.get("title") or "").strip()
        text = sub_text(row)
        if episode_title(ru_title, en_title) or text:
            found[(season, number)] = (ru_title, en_title, text)
    return found


def sub_fill(ru, en, year, season, wanted, notes, titles):
    show = sub_show(ru, en, year)
    if not show:
        return
    episodes = sub_episodes(show.get("slug"))
    for number in wanted:
        ru_title, en_title, text = episodes.get((int(season), int(number)), ("", "", ""))
        if episode_title(ru_title, en_title):
            titles[number] = (ru_title, en_title)
        if text:
            notes[number] = text


def episode_notes(film_id, season, wanted, ru, en, year, titles_only=False):
    notes = {}
    titles = {}
    counts = {}
    try:
        sub_fill(ru, en, year, season, wanted, notes, titles)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        pass
    need_text = [] if titles_only else [number for number in wanted if number not in notes]
    need_title = [number for number in wanted if number not in titles]
    if (need_text or need_title) and str(film_id).isdigit():
        try:
            kp_text, counts, kp_titles = kp_notes(film_id, season, wanted, titles_only)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            kp_text, counts, kp_titles = {}, {}, {}
        for number in need_title:
            title = kp_titles.get(number)
            if title and episode_title(title[0], title[1]):
                titles[number] = title
        for number in need_text:
            text = kp_text.get(number, "")
            if text:
                notes[number] = text
    if titles_only:
        return notes, titles
    missing = [number for number in wanted if number not in notes]
    if missing and (ru or en):
        try:
            extra = kt_notes(ru, en, year)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError):
            extra = {}
        for number in missing:
            text = kt_pick(extra, season, number, counts)
            if text:
                notes[number] = text
    return notes, titles


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
        if cmd == "show-search" and len(sys.argv) == 3:
            search_show(sys.argv[2])
            return
        if cmd == "show-genre" and len(sys.argv) == 4:
            print(wiki_genre(sys.argv[2], sys.argv[3], "show") or "NONE")
            return
        if cmd == "episode-notes" and len(sys.argv) in (8, 9):
            titles_only = False
            if len(sys.argv) == 9:
                if sys.argv[8] != "names":
                    raise SystemExit(2)
                titles_only = True
            wanted = []
            for piece in sys.argv[4].split(","):
                piece = piece.strip()
                if piece.isdigit():
                    wanted.append(int(piece))
            notes, titles = episode_notes(
                sys.argv[2],
                int(sys.argv[3]),
                wanted,
                sys.argv[5],
                sys.argv[6],
                sys.argv[7],
                titles_only,
            )
            if titles_only:
                numbers = sorted(
                    number
                    for number, pair in titles.items()
                    if episode_title(pair[0], pair[1])
                )
                if not numbers:
                    print("NONE")
                    return
                for number in numbers:
                    ru_title, en_title = titles[number]
                    sys.stdout.write("%d\t%s\t%s\n" % (number, cell(ru_title), cell(en_title)))
                return
            numbers = sorted(set(notes) | set(titles))
            if not numbers:
                print("NONE")
                return
            for number in numbers:
                ru_title, en_title = titles.get(number, ("", ""))
                title = episode_title(ru_title, en_title).replace("\t", " ")
                line = notes.get(number, "").replace("\t", " ")
                sys.stdout.write(
                    "%d\t%s\t%s\t%s\t%s\n"
                    % (number, title, line, cell(ru_title), cell(en_title))
                )
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
