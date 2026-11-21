#!/usr/bin/env python3
# English subtitles from a WatchEnglish show season or a movie page.
# A ready episode button loads /api/core/media/<episode id>/subtitles?lang=en.
# A movie page loads the same path with the movie id.
# Those ids are already in the catalog, so this saves the response as srt.
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
ORIGIN = "https://watchenglish.tv"
FONT_RE = re.compile(r"</?font[^>]*>", re.I)
BR_RE = re.compile(r"<br\s*/?>", re.I)
BAD_NAME = re.compile(r'[/\\\0]')


def get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.load(resp)


def parse_page(url):
    parts = urllib.parse.urlparse(url.strip())
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "watchenglish.tv":
        raise SystemExit("Need a watchenglish.tv link")
    bits = [b for b in parts.path.split("/") if b]
    if len(bits) < 2 or bits[0] not in ("shows", "movies"):
        raise SystemExit(
            "Need a show or movie link: "
            "https://watchenglish.tv/shows/name?season=2 "
            "or https://watchenglish.tv/movies/name"
        )
    slug = bits[1]
    if bits[0] == "movies":
        return "movie", slug, None
    season = urllib.parse.parse_qs(parts.query).get("season", ["1"])[0]
    if not season.isdigit():
        raise SystemExit("Season in the link must be a number")
    return "show", slug, int(season)


def cue_text(text):
    text = BR_RE.sub("\n", text or "")
    text = FONT_RE.sub("", text)
    text = html.unescape(text).replace("\\N", "\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def stamp(seconds):
    ms = int(round(float(seconds) * 1000))
    if ms < 0:
        ms = 0
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def to_srt(items):
    blocks = []
    number = 1
    for item in items:
        text = cue_text(item.get("text") or "")
        if not text:
            continue
        start = float(item.get("start") or 0)
        end = float(item.get("end") or 0)
        if end <= start:
            end = start + 0.001
        blocks.append(f"{number}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
        number += 1
    if not blocks:
        return ""
    return "\n".join(blocks) + "\n"


def clean_title(title):
    title = BAD_NAME.sub(" ", title or "")
    title = title.replace(":", " -")
    title = re.sub(r"\s+", " ", title).strip(" .")
    return title


def file_name(season, episode, title):
    base = f"S{season:02d}E{episode:02d}"
    title = clean_title(title)
    if title:
        base = f"{base} · {title}"
    return base + ".srt"


def ready_episodes(show, season, wanted):
    seasons = show.get("seasons") or []
    block = next((item for item in seasons if item.get("season_number") == season), None)
    if block is None:
        raise SystemExit(f"No season {season}")
    picked = []
    for episode in block.get("episodes") or []:
        if not episode.get("video_ready") or not episode.get("video_asset_id"):
            continue
        number = episode.get("episode_number")
        if wanted and number not in wanted:
            continue
        picked.append(episode)
    if wanted:
        found = {episode.get("episode_number") for episode in picked}
        missing = [str(number) for number in wanted if number not in found]
        if missing:
            raise SystemExit("Not ready or missing: " + " ".join(missing))
    if not picked:
        raise SystemExit("No ready episodes")
    picked.sort(key=lambda episode: episode.get("episode_number") or 0)
    return picked


def write_srt(media_id, name):
    data = get_json(ORIGIN + "/api/core/media/" + media_id + "/subtitles?lang=en")
    with open(name, "w", encoding="utf-8") as handle:
        handle.write(to_srt(data.get("items") or []))
    print(name)


def save_movie(media):
    details = media.get("details") or {}
    if media.get("type") not in (None, "movie"):
        raise SystemExit("This link is not a movie")
    if not details.get("video_ready") or not details.get("video_asset_id"):
        raise SystemExit("Movie is not ready")
    title = clean_title(media.get("title") or "") or media.get("slug") or "movie"
    write_srt(media["id"], title + ".srt")


def save_show(show, season, wanted):
    if show.get("type") not in (None, "show"):
        raise SystemExit("This link is not a show")
    episodes = ready_episodes(show, season, wanted)
    for index, episode in enumerate(episodes):
        if index:
            time.sleep(0.2)
        number = episode.get("episode_number") or 0
        name = file_name(season, number, episode.get("title") or "")
        write_srt(episode["id"], name)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(
            'Usage: get-subs "https://watchenglish.tv/shows/name?season=2" [1 3]\n'
            '       get-subs "https://watchenglish.tv/movies/name"'
        )
    kind, slug, season = parse_page(sys.argv[1])
    wanted = []
    for arg in sys.argv[2:]:
        if not arg.isdigit() or int(arg) < 1:
            raise SystemExit(f"Bad episode number: {arg}")
        wanted.append(int(arg))
    if kind == "movie" and wanted:
        raise SystemExit("Episode numbers are only for a show link")
    media = get_json(
        ORIGIN + "/api/core/catalog/media/slug/" + urllib.parse.quote(slug)
    )
    if kind == "movie":
        save_movie(media)
    else:
        save_show(media, season, wanted)
    print("Done")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Site error {exc.code}") from exc
    except urllib.error.URLError:
        raise SystemExit("No network")
    except (TimeoutError, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"Could not save subtitles: {exc}") from exc
