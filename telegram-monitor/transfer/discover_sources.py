"""Read-only discovery of public Telegram groups/channels for the MAXMADE monitor.

Searches Telegram globally, reads only the public tail of each candidate
(no join, no messages, no participant lists) and scores it with the active
local filter. Paced to stay well below Telegram flood limits.
Usage (VPS): mm discover_sources.py <out.json> [extra_handles_file]
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())  # run from the release dir via mm
import order_profile
import reader

QUERIES = [
    "фриланс", "фриланс заказы", "заказы фриланс", "биржа фриланса", "фрилансеры чат",
    "заказы сайты", "заказ сайта", "разработка сайтов", "веб разработка", "веб дизайн",
    "веб-дизайн заказы", "дизайн заказы", "заказы для дизайнеров", "дизайнеры чат",
    "tilda", "тильда", "тильда заказы", "figma", "фигма", "ui ux", "ux ui дизайн",
    "лендинг", "верстка", "верстальщик", "презентации дизайн", "дизайн презентаций",
    "ищу исполнителя", "нужен дизайнер", "нужен сайт", "удаленная работа",
    "работа дизайнер", "it фриланс", "digital агентство", "веб студия",
    "субподряд", "предприниматели чат", "бизнес чат", "маркетологи", "smm чат",
    "таргетологи", "инфобизнес", "продюсеры", "онлайн школа", "wordpress", "webflow",
]
EXCLUDE = {"top_ads_freelance", "freelead", "biznes_chat",
           # earlier evaluation: rules forbid client search / ads, or admission by selection
           "spbeventchat", "expopeople", "bitrixfordevelopers", "bit24dev"}
FORBID = re.compile(r"парс|сбор\w* баз|поиск\w* клиент|запрещ\w*[^.]{0,40}(ботов|юзербот|мониторинг)", re.I)
CYR = re.compile(r"[а-яё]", re.I)
TAIL = 200
NOW = dt.datetime.now(dt.timezone.utc)


def handle(ch) -> str | None:
    if getattr(ch, "username", None):
        return ch.username
    for u in getattr(ch, "usernames", None) or []:
        if getattr(u, "active", False):
            return u.username
    return None


async def flood_safe(coro_factory, log):
    from telethon import errors
    for _ in range(3):
        try:
            return await coro_factory()
        except errors.FloodWaitError as exc:
            if exc.seconds > 300:
                raise
            log(f"flood wait {exc.seconds}s")
            await asyncio.sleep(exc.seconds + 2)
    return None


async def main(out: Path, extra: list[str]) -> None:
    from telethon import TelegramClient, errors, functions, types

    log = lambda m: print(dt.datetime.now().strftime("%H:%M:%S"), m, flush=True)
    api_id, api_hash = reader.credentials()
    # The live reader holds the main session file; use a private copy of the same authorization.
    session = reader.SESSION.parent / "discover"
    session.with_suffix(".session").write_bytes(reader.SESSION.with_suffix(".session").read_bytes())
    client = TelegramClient(str(session), api_id, api_hash, proxy=reader.connection_proxy(),
                            receive_updates=False)
    await client.connect()
    reader.verify_account(await client.get_me(), reader.expected_account())
    found: dict[str, dict] = {}

    def add(ch, origin):
        h = handle(ch)
        if not h or h.lower() in EXCLUDE or not isinstance(ch, types.Channel):
            return
        if getattr(ch, "scam", False) or getattr(ch, "fake", False) or getattr(ch, "restricted", False):
            return
        item = found.setdefault(h.lower(), {"handle": h, "entity": ch, "origins": []})
        item["origins"].append(origin)

    for q in QUERIES:
        res = await flood_safe(lambda: client(functions.contacts.SearchRequest(q=q, limit=50)), log)
        for ch in (res.chats if res else []):
            add(ch, "search:" + q)
        await asyncio.sleep(3)
    log(f"search candidates {len(found)}")
    for h in extra:
        if h.lower() in found or h.lower() in EXCLUDE:
            continue
        try:
            ent = await flood_safe(lambda: client.get_entity(h), log)
            add(ent, "catalog")
        except (ValueError, errors.RPCError) as exc:
            found[h.lower()] = {"handle": h, "error": type(exc).__name__, "origins": ["catalog"]}
        await asyncio.sleep(4)
    log(f"total candidates {len(found)}")

    results = []
    for key, item in sorted(found.items()):
        ch = item.get("entity")
        row = {"handle": item["handle"], "origins": item["origins"][:5]}
        if ch is None:
            row["error"] = item.get("error")
            results.append(row)
            continue
        row.update(title=ch.title, kind="group" if ch.megagroup else "channel",
                   member=not getattr(ch, "left", True), peer_id=int("-100" + str(ch.id)))
        if not CYR.search(ch.title or "") and not any(o == "catalog" for o in item["origins"]):
            row["skip"] = "not_russian_title"
            results.append(row)
            continue
        try:
            full = await flood_safe(lambda: client(functions.channels.GetFullChannelRequest(ch)), log)
            about = full.full_chat.about or ""
            row["participants"] = full.full_chat.participants_count
            row["forbid_hint"] = bool(FORBID.search(about))
            row["about"] = re.sub(r"\s+", " ", about)[:200]
            await asyncio.sleep(1.5)
            msgs = await flood_safe(lambda: client.get_messages(ch, limit=TAIL), log)
        except (errors.RPCError, ValueError) as exc:
            row["error"] = type(exc).__name__
            results.append(row)
            await asyncio.sleep(2)
            continue
        texts = [m for m in (msgs or []) if getattr(m, "message", None)]
        week = [m for m in texts if (NOW - m.date).days < 7]
        span_h = max((NOW - texts[-1].date).total_seconds() / 3600, 1) if texts else None
        routes, hits = {}, []
        for m in texts:
            a = order_profile.assess(m.message[:2000])
            r = a["notification_route"]
            routes[r] = routes.get(r, 0) + 1
            if r in ("main", "partnership"):
                hits.append({"id": m.id, "date": m.date.isoformat(), "route": r,
                             "text": re.sub(r"\s+", " ", m.message)[:160]})
        row.update(tail=len(texts), week=len(week), span_hours=round(span_h, 1) if span_h else None,
                   last_age_h=round((NOW - texts[0].date).total_seconds() / 3600, 1) if texts else None,
                   authors=len({m.sender_id for m in texts if m.sender_id}),
                   routes=routes, hits=hits,
                   hits_per_week=round(len(hits) / span_h * 168, 2) if span_h else 0)
        results.append(row)
        log(f"{item['handle']}: {row['kind']} week={row['week']} hits={len(hits)} routes={routes}")
        await asyncio.sleep(2.5)
    await client.disconnect()
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"saved {len(results)} rows -> {out}")


if __name__ == "__main__":
    extra = Path(sys.argv[2]).read_text(encoding="utf-8").split() if len(sys.argv) > 2 else []
    asyncio.run(main(Path(sys.argv[1]), extra))
