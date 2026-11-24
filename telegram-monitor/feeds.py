"""Read-only exchange/web feeds for the MAXMADE monitor.

Polls public order listings (FL.ru RSS, Workspace tenders RSS, Kwork projects
page, connect.tg public order page) and passes every item through the same
filter and delivery queue as Telegram sources (`monitor.ingest`). It never
logs in, never responds to orders and never republishes them; a card only
links to the original. The bot service delivers cards for feeds listed in
MONITOR_FEEDS.

Usage: python feeds.py run [--only fl,kwork] | python feeds.py dry [--only ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

import monitor
import runtime_status as health

UA = "Mozilla/5.0 (X11; Linux x86_64) MAXMADE-order-monitor/1.0 (personal, read-only)"
MOSCOW = monitor.MOSCOW
TAG = re.compile(r"<[^>]+>")
CONTACT = re.compile(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{4,31})\b")
PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
TEXT_ROW = re.compile(r'(?:^|\n)([0-9a-f]+):T([0-9a-f]+),')


def plain(text: str | None) -> str:
    text = html.unescape(TAG.sub(" ", text or ""))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def fetch(url: str, timeout: int = 30) -> str:
    handlers = []
    if os.getenv("MONITOR_FEED_NETWORK", "direct") == "direct":
        handlers.append(urllib.request.ProxyHandler({}))  # Russian exchanges are reached directly
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with opener.open(request, timeout=timeout) as response:
        return response.read(4_000_000).decode("utf-8", "replace")


def item(feed: str, ident: str, title: str, body: str, published: dt.datetime, link: str,
         author: str | None = None, budget: str = "") -> dict:
    lines = [title.strip()]
    if budget:
        lines.append("Бюджет: " + budget)
    lines.append(body.strip())
    return {"feed": feed, "id": ident, "text": "\n".join(x for x in lines if x),
            "published": published, "link": link, "author": author}


def first_contact(text: str) -> str | None:
    for match in CONTACT.finditer(text):
        name = match.group(1)
        low = name.lower()
        if not low.endswith("bot") and low not in ("joinchat", "connectexbot"):
            return "@" + name
    return None


# --- parsers (pure: text in, items out) ---

def parse_fl(text: str) -> list[dict]:
    out = []
    for node in ET.fromstring(text).iter("item"):
        link = node.findtext("link") or ""
        found = re.search(r"/projects/(\d+)/", link)
        if not found:
            continue
        title = plain(node.findtext("title"))
        budget = ""
        price = re.search(r"\(Бюджет:\s*([^)]*)\)", title)
        if price:
            budget = price.group(1).strip()
            title = title.replace(price.group(0), "").strip()
        category = plain(node.findtext("category"))
        body = plain(node.findtext("description")) + ("\nРаздел FL.ru: " + category if category else "")
        out.append(item("fl", found.group(1), title, body,
                        email.utils.parsedate_to_datetime(node.findtext("pubDate")), link, None, budget))
    return out


def parse_workspace(text: str) -> list[dict]:
    out = []
    for node in ET.fromstring(text).iter("item"):
        link = node.findtext("link") or ""
        found = re.search(r"-(\d+)/?$", link)
        if not found:
            continue
        body = plain(node.findtext("description"))
        budget = re.search(r"Бюджет\s*:\s*([^\n]*?)(?:\s+Описание|$)", body)
        out.append(item("workspace", found.group(1), plain(node.findtext("title")), body,
                        email.utils.parsedate_to_datetime(node.findtext("pubDate")), link, None,
                        budget.group(1).strip() if budget else ""))
    return out


def parse_kwork(text: str) -> list[dict]:
    start = text.find('"wants":[')
    if start < 0:
        raise ValueError("Kwork: список заказов не найден на странице")
    wants, _ = json.JSONDecoder().raw_decode(text[start + len('"wants":'):])
    out = []
    for w in wants:
        created = dt.datetime.strptime(w["date_create"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=MOSCOW)
        price = w.get("priceLimit")
        budget = f"до {float(price):,.0f} ₽".replace(",", " ") if price else ""
        user = (w.get("user") or {}).get("username") or ""
        out.append(item("kwork", str(w["id"]), plain(w.get("name")), plain(w.get("description")), created,
                        f"https://kwork.ru/projects/{w['id']}",
                        f"kwork.ru/user/{user}" if user else None, budget))
    return out


def parse_connect(text: str) -> list[dict]:
    """connect.tg public page; only Telegram-origin orders (Kwork ones come from Kwork directly)."""
    stream = "".join(json.loads('"' + c + '"') for c in PUSH.findall(text))
    refs = {}
    for row in TEXT_ROW.finditer(stream):
        size = int(row.group(2), 16)
        refs["$" + row.group(1)] = stream[row.end():].encode("utf-8")[:size].decode("utf-8", "ignore")
    start = stream.find('"initialOrders":')
    if start < 0:
        raise ValueError("connect.tg: список заказов не найден на странице")
    orders, _ = json.JSONDecoder().raw_decode(stream[start + len('"initialOrders":'):])
    out = []
    for o in orders:
        if o.get("source") != "Telegram":
            continue
        desc = o.get("description") or ""
        desc = refs.get(desc, desc) if desc.startswith("$") else desc
        lo, hi = o.get("budgetMin"), o.get("budgetMax")
        budget = "" if o.get("budgetEstimated") or not hi else (f"{lo}–{hi} ₽" if lo and lo != hi else f"{hi} ₽")
        respond = desc[desc.rfind("Как откликнуться"):] if "Как откликнуться" in desc else desc
        out.append(item("connect", o["slug"], o.get("title") or "", desc,
                        dt.datetime.fromisoformat(o["postedAt"].replace("Z", "+00:00")),
                        "https://connect.tg/orders/" + o["slug"], first_contact(respond), budget))
    return out


FEEDS: dict[str, tuple[str, Callable[[str], list[dict]], tuple[str, ...]]] = {
    "fl": ("FL.ru", parse_fl, ("https://www.fl.ru/rss/all.xml",)),
    "workspace": ("Workspace", parse_workspace, ("https://workspace.ru/tenders/rss/",)),
    "kwork": ("Kwork", parse_kwork, ("https://kwork.ru/projects", "https://kwork.ru/projects?page=2")),
    "connect": ("connect.tg (Telegram)", parse_connect, ("https://connect.tg/",)),
    # Pages without RSS or API; snapshots come from a local changedetection.io.
    "watch": ("Веб-мониторинг", None, ()),
}

# Retired by decision, not broken: the parser and its tests stay, the default
# run skips the feed. `--only kwork` still works for a deliberate re-check.
RETIRED = {
    "kwork": "отключена 25.11.2026: заказы с Kwork не окупали отклик; возврат — явно через --only kwork",
}


def active(names: list[str], explicit: bool = False) -> tuple[list[str], list[str]]:
    """Split names into those that run and retired ones skipped by default."""
    run: list[str] = []
    skipped: list[str] = []
    for name in names:
        (skipped if name in RETIRED and not explicit else run).append(name)
    return run, skipped


def collect(name: str, getter: Callable[[str], str] = fetch, db=None,
            at: dt.datetime | None = None) -> list[dict]:
    if name == "watch":
        import watch_feed
        return watch_feed.collect(db, at or monitor.now())
    _, parser, urls = FEEDS[name]
    items: dict[str, dict] = {}
    for url in urls:
        for it in parser(getter(url)):
            items.setdefault(it["id"], it)
        time.sleep(1)
    return list(items.values())


def fresh(items: list[dict], at: dt.datetime, max_age_hours: float) -> list[dict]:
    """First start must not flood the owner with weeks-old tenders."""
    limit = at - dt.timedelta(hours=max_age_hours)
    return [x for x in items if x["published"] >= limit]


def ingest(db, cfg: monitor.Config, items: list[dict], at: dt.datetime) -> int:
    count = 0
    for it in items:
        source_id = "feed:" + it["feed"]
        exists = db.execute("SELECT 1 FROM events WHERE source_id=? AND message_id=?", (source_id, it["id"])).fetchone()
        if exists:
            continue
        monitor.ingest(db, cfg, source_id=source_id, message_id=it["id"], body=it["text"],
                       published=it["published"], received=at, edit_version=monitor.iso(it["published"]),
                       link=it["link"], author=it["author"], source_name=FEEDS[it["feed"]][0], method="feed")
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only exchange feeds for the order monitor")
    parser.add_argument("command", choices=("run", "dry"))
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    monitor.load_env()
    cfg = monitor.Config.from_env()
    names = [x for x in (args.only.split(",") if args.only else cfg.feeds) if x]
    unknown = [x for x in names if x not in FEEDS]
    if unknown or not names:
        print(f"Ошибка: неизвестные или пустые ленты {unknown or names}; есть {', '.join(FEEDS)}", file=sys.stderr)
        return 2
    names, skipped = active(names, explicit=bool(args.only))
    for name in skipped:
        print(f"{name}: пропущена, {RETIRED[name]}", file=sys.stderr)
    if not names:
        print("Ошибка: все запрошенные ленты отключены", file=sys.stderr)
        return 2
    max_age = float(os.getenv("MONITOR_FEED_MAX_AGE_HOURS", "24"))
    at = monitor.now()
    db = monitor.connect(cfg.db) if args.command == "run" else None
    failures = 0
    status: dict[str, dict] = {}
    try:
        for name in names:
            if name == "watch" and db is None:
                import watch_feed
                print(f"watch: {watch_feed.preview()}")
                continue
            try:
                items = fresh(collect(name, db=db, at=at), at, max_age)
            except Exception as exc:  # one broken site must not stop the others
                failures += 1
                status[name] = {"ok": False, "error": type(exc).__name__}
                print(f"{name}: ошибка {type(exc).__name__}", file=sys.stderr)
                continue
            if db is None:
                import order_profile
                routes: dict[str, int] = {}
                for it in items:
                    route = order_profile.assess(it["text"][:2000])["notification_route"]
                    routes[route] = routes.get(route, 0) + 1
                print(f"{name}: свежих {len(items)}, маршруты {routes}")
                continue
            added = ingest(db, cfg, items, at)
            status[name] = {"ok": True, "fresh": len(items), "new": added}
            print(f"{name}: свежих {len(items)}, новых {added}")
    finally:
        if db is not None:
            db.close()
    if args.command == "run":
        health.update("feeds", last_run=health.stamp(), feeds=status)
    return 1 if failures == len(names) else 0


if __name__ == "__main__":
    raise SystemExit(main())
