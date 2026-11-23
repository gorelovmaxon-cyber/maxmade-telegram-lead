"""Collect YouTube videos about Telegram lead/order monitoring for analysis (read-only).

Runs with the system python and the HTTP proxy from the environment.
  python3 yt_research.py search <out_dir>     -> <out_dir>/videos.json (unique videos from QUERIES)
  python3 yt_research.py details <out_dir>    -> adds description, channel, views, transcript (if any)
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
QUERIES = [
    "парсер заказов telegram", "мониторинг телеграм чатов заказы", "поиск клиентов в телеграм чатах",
    "лиды из телеграм чатов", "парсер лидов телеграм", "бот для поиска заказов фрилансеру",
    "как найти заказы в телеграм", "фриланс заказы телеграм каналы", "где искать заказы веб дизайнеру",
    "где искать клиентов на создание сайтов", "мониторинг ключевых слов телеграм", "telethon мониторинг чатов",
    "юзербот мониторинг чатов", "автоматический поиск заказов фриланс", "парсер kwork telegram бот",
    "парсер fl.ru бот", "сервис поиска лидов в telegram", "лидогенерация телеграм чаты",
    "поиск заказов tilda дизайнер", "первые заказы веб дизайнеру", "как находить клиентов дизайнеру",
    "холодный поиск клиентов в телеграм", "telegram lead monitoring", "telegram keyword monitoring bot",
    "telegram scraper leads", "freelance job alerts telegram bot", "n8n telegram мониторинг чатов",
    "make telegram мониторинг заказов", "ai поиск заказов фриланс", "агрегатор заказов фриланс",
]
DATA = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.S)
PLAYER_START = "ytInitialPlayerResponse = "


def player_json(page: str) -> dict:
    start = page.find(PLAYER_START)
    if start < 0:
        raise ValueError("no player response")
    obj, _ = json.JSONDecoder().raw_decode(page[start + len(PLAYER_START):])
    return obj


def get(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def walk(node, key):
    if isinstance(node, dict):
        if key in node:
            yield node[key]
        for value in node.values():
            yield from walk(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value, key)


def text(runs) -> str:
    if not runs:
        return ""
    if "simpleText" in runs:
        return runs["simpleText"]
    return "".join(r.get("text", "") for r in runs.get("runs", []))


def search(out: Path) -> None:
    videos: dict[str, dict] = {}
    for q in QUERIES:
        try:
            page = get("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q) + "&hl=ru")
            data = json.loads(DATA.search(page).group(1))
        except Exception as exc:  # noqa: BLE001
            print(f"{q}: {type(exc).__name__}", flush=True)
            continue
        added = 0
        for v in walk(data, "videoRenderer"):
            vid = v.get("videoId")
            if not vid or vid in videos:
                if vid:
                    videos[vid]["queries"].append(q)
                continue
            videos[vid] = {"id": vid, "title": text(v.get("title")), "channel": text(v.get("ownerText")),
                           "published": text(v.get("publishedTimeText")), "views": text(v.get("viewCountText")),
                           "length": text(v.get("lengthText")), "queries": [q]}
            added += 1
        print(f"{q}: +{added} (всего {len(videos)})", flush=True)
        time.sleep(2)
    out.mkdir(parents=True, exist_ok=True)
    (out / "videos.json").write_text(json.dumps(list(videos.values()), ensure_ascii=False, indent=1), encoding="utf-8")


def transcript(player: dict) -> str:
    tracks = list(walk(player, "captionTracks"))
    if not tracks:
        return ""
    tracks = tracks[0]
    track = next((t for t in tracks if t.get("languageCode") == "ru"), tracks[0])
    try:
        xml = get(track["baseUrl"] + "&fmt=srv3")
    except Exception:  # noqa: BLE001
        return ""
    parts = re.findall(r"<s[^>]*>([^<]*)</s>|<p[^>]*>([^<]*)</p>", xml)
    return re.sub(r"\s+", " ", html.unescape(" ".join(a or b for a, b in parts))).strip()


def details(out: Path) -> None:
    path = out / "videos.json"
    videos = json.loads(path.read_text(encoding="utf-8"))
    for i, v in enumerate(videos):
        if v.get("description"):
            continue
        try:
            page = get(f"https://www.youtube.com/watch?v={v['id']}&hl=ru")
            player = player_json(page)
            details_ = player.get("videoDetails", {})
            v["description"] = details_.get("shortDescription", "")
            v["view_count"] = int(details_.get("viewCount", "0") or 0)
            v["transcript"] = transcript(player)
            if not v["description"]:
                # Bot check hides videoDetails; the page data still carries the description.
                found = DATA.search(page)
                data = json.loads(found.group(1)) if found else {}
                described = list(walk(data, "attributedDescription"))
                v["description"] = described[0].get("content", "") if described else ""
                views = [text(x) for x in walk(data, "viewCount") if isinstance(x, dict)]
                digits = re.sub(r"\D", "", next((x for x in views if x), ""))
                v["view_count"] = v["view_count"] or (int(digits) if digits else 0)
        except Exception as exc:  # noqa: BLE001
            v["description"] = ""
            v["error"] = type(exc).__name__
        print(f"{i + 1}/{len(videos)} {v['id']} desc={len(v['description'])} tr={len(v.get('transcript', ''))}", flush=True)
        path.write_text(json.dumps(videos, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(1.5)


if __name__ == "__main__":
    {"search": search, "details": details}[sys.argv[1]](Path(sys.argv[2]))
