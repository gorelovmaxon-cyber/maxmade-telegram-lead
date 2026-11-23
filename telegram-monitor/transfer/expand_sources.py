"""Second-pass discovery: does the first pass miss sources?

Three independent ways to reach chats the global search does not return:
  1. channels.GetChannelRecommendations for known order feeds ("similar channels");
  2. forwarded-message graph: aggregators repost orders and Telegram keeps the
     original chat in fwd_from, which names the real source;
  3. extra global queries with different wording.
Every candidate is then scored on its public tail with the active filter.
Usage (VPS): mm expand_sources.py <out.json> [--seeds a,b,c]
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
import order_profile
import reader

QUERIES = [
    "заказ лендинг", "нужен сайт на тильде", "сайт под ключ заказ", "ищу веб дизайнера",
    "заказы на сайты чат", "фриланс заказы чат", "веб студия чат", "digital агентство чат",
    "субподряд веб", "заказы копирайтинг дизайн", "заказы интернет магазин",
    "битрикс заказы", "wordpress заказы", "заказы телеграм бот", "smm заказы чат",
    "маркетплейс селлеры чат", "предприниматели заказы", "инфобизнес чат заказы",
    "дизайн презентаций заказы", "моушн дизайн заказы", "заказы figma", "верстка заказы чат",
]
SEEDS = ["fa_sites", "top_ads_freelance", "freelead", "onpeak_chat", "zakazi_designers",
         "designerworkchat", "job_webdev", "freelancetaverna", "designwork_vacansii"]
EXCLUDE = {"top_ads_freelance", "freelead", "biznes_chat"}
NOW = dt.datetime.now(dt.timezone.utc)
TAIL = 200


def handle(ch) -> str | None:
    if getattr(ch, "username", None):
        return ch.username
    for u in getattr(ch, "usernames", None) or []:
        if getattr(u, "active", False):
            return u.username
    return None


async def main(out: Path, seeds: list[str]) -> None:
    from telethon import TelegramClient, errors, functions, types

    log = lambda m: print(dt.datetime.now().strftime("%H:%M:%S"), m, flush=True)
    api_id, api_hash = reader.credentials()
    session = reader.SESSION.parent / "expand"
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
        found.setdefault(h.lower(), {"handle": h, "entity": ch, "origins": []})["origins"].append(origin)

    # 1. Forward graph: order feeds repost from the chats that matter.
    for seed in seeds:
        try:
            entity = await client.get_entity(seed)
            messages = await client.get_messages(entity, limit=400)
        except (ValueError, errors.RPCError) as exc:
            log(f"seed {seed}: {type(exc).__name__}")
            continue
        seen = 0
        for m in messages or []:
            if not m.message:
                continue
            origin = getattr(m, "fwd_from", None)
            peer = getattr(origin, "from_id", None) or getattr(origin, "saved_from_peer", None)
            if peer is None:
                continue
            try:
                chat = await client.get_entity(peer)
            except (ValueError, errors.RPCError):
                continue
            if isinstance(chat, types.Channel) and handle(chat):
                add(chat, "fwd:" + seed)
                seen += 1
            await asyncio.sleep(0.4)
        log(f"seed {seed}: forwarded origins {seen}")
        await asyncio.sleep(2)

    # 2. Similar-channel recommendations for the strongest feeds.
    for seed in seeds[:6]:
        try:
            entity = await client.get_entity(seed)
            result = await client(functions.channels.GetChannelRecommendationsRequest(channel=entity))
            for ch in result.chats:
                add(ch, "rec:" + seed)
            log(f"rec {seed}: {len(result.chats)}")
        except Exception as exc:  # noqa: BLE001 - API varies by layer
            log(f"rec {seed}: {type(exc).__name__}")
        await asyncio.sleep(2)

    # 3. Extra global queries.
    for q in QUERIES:
        try:
            res = await client(functions.contacts.SearchRequest(q=q, limit=50))
            for ch in res.chats:
                add(ch, "search2:" + q)
            log(f"search2 {q}: {len(res.chats)}")
        except errors.FloodWaitError as exc:
            log(f"flood {exc.seconds}s")
            await asyncio.sleep(min(exc.seconds + 5, 300))
        except Exception as exc:  # noqa: BLE001
            log(f"search2 {q}: {type(exc).__name__}")
        await asyncio.sleep(3)

    log(f"candidates {len(found)}")
    results = []
    for key, item in sorted(found.items()):
        ch = item["entity"]
        row = {"handle": item["handle"], "origins": item["origins"][:6]}
        try:
            full = await client(functions.channels.GetFullChannelRequest(ch))
            about = full.full_chat.about or ""
            row.update(title=ch.title, kind="group" if ch.megagroup else "channel",
                       participants=full.full_chat.participants_count, about=re.sub(r"\s+", " ", about)[:160])
            await asyncio.sleep(1)
            messages = await client.get_messages(ch, limit=TAIL)
        except (errors.RPCError, ValueError) as exc:
            row["error"] = type(exc).__name__
            results.append(row)
            await asyncio.sleep(2)
            continue
        texts = [m for m in (messages or []) if getattr(m, "message", None)]
        span = max((NOW - texts[-1].date).total_seconds() / 3600, 1) if texts else None
        hits = [m for m in texts if order_profile.assess(m.message[:2000])["notification_route"] in ("main", "partnership")]
        row.update(tail=len(texts), last_age_h=round((NOW - texts[0].date).total_seconds() / 3600, 1) if texts else None,
                   hits=[{"id": m.id, "text": re.sub(r"\s+", " ", m.message)[:120]} for m in hits[:5]],
                   hits_per_week=round(len(hits) / span * 168, 2) if span else 0)
        results.append(row)
        log(f"{key}: hits {len(hits)}/week {row['hits_per_week']} origins {row['origins'][:2]}")
        await asyncio.sleep(2.5)
    await client.disconnect()
    session.with_suffix(".session").unlink(missing_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"saved {len(results)} -> {out}")


if __name__ == "__main__":
    seeds = SEEDS
    if "--seeds" in sys.argv:
        seeds = sys.argv[sys.argv.index("--seeds") + 1].split(",")
    asyncio.run(main(Path(sys.argv[1]), seeds))
