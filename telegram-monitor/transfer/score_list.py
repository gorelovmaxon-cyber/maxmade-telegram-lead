"""Score an explicit list of public channels/groups with the active filter (read-only).

The list may come from any external catalogue (another project's sources.py, a
blog post, a video). It only reads the public tail of each chat; no join, no
messages to anyone.
Usage (VPS): mm score_list.py <out.json> <handles.txt>
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

NOW = dt.datetime.now(dt.timezone.utc)
TAIL = 200


async def main(out: Path, handles: list[str]) -> None:
    from telethon import TelegramClient, errors, functions

    log = lambda m: print(dt.datetime.now().strftime("%H:%M:%S"), m, flush=True)
    api_id, api_hash = reader.credentials()
    session = reader.SESSION.parent / "score"
    session.with_suffix(".session").write_bytes(reader.SESSION.with_suffix(".session").read_bytes())
    client = TelegramClient(str(session), api_id, api_hash, proxy=reader.connection_proxy(), receive_updates=False)
    await client.connect()
    reader.verify_account(await client.get_me(), reader.expected_account())
    results = []
    for h in handles:
        row = {"handle": h}
        try:
            entity = await client.get_entity(h)
            full = await client(functions.channels.GetFullChannelRequest(entity))
            row.update(title=entity.title, kind="group" if entity.megagroup else "channel",
                       participants=full.full_chat.participants_count,
                       about=re.sub(r"\s+", " ", full.full_chat.about or "")[:150])
            messages = await client.get_messages(entity, limit=TAIL)
        except (errors.RPCError, ValueError) as exc:
            row["error"] = type(exc).__name__
            results.append(row)
            log(f"@{h}: {row['error']}")
            await asyncio.sleep(2)
            continue
        texts = [m for m in (messages or []) if getattr(m, "message", None)]
        span = max((NOW - texts[-1].date).total_seconds() / 3600, 1) if texts else None
        hits = [m for m in texts if order_profile.assess(m.message[:2000])["notification_route"] in ("main", "partnership")]
        row.update(tail=len(texts), last_age_h=round((NOW - texts[0].date).total_seconds() / 3600, 1) if texts else None,
                   hits=[{"text": re.sub(r"\s+", " ", m.message)[:120]} for m in hits[:4]],
                   hits_per_week=round(len(hits) / span * 168, 2) if span else 0)
        results.append(row)
        log(f"@{h}: {row['kind']} last {row['last_age_h']}h hits {len(hits)} /week {row['hits_per_week']}")
        await asyncio.sleep(2.5)
    await client.disconnect()
    session.with_suffix(".session").unlink(missing_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"saved {len(results)} -> {out}")


if __name__ == "__main__":
    handles = Path(sys.argv[2]).read_text(encoding="utf-8").split()
    asyncio.run(main(Path(sys.argv[1]), handles))
