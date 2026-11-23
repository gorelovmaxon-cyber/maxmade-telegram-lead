"""Paced joining of approved sources by @maxmade_pro (no posting, no DMs).

Joins at most --limit sources per run with a pause between joins, stops on any
FloodWait and records progress, so a timer can finish the list over hours
without risking the account. Groups are skipped when their description or
pinned message forbids bots/parsing/collecting clients.
Usage (VPS): mm join_sources.py <targets.txt> [--limit 6] [--pause 120]
Prints "JOINED <n>" on the last line.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
import reader

FORBID = re.compile(r"(запрещ\w*|нельзя)[^.\n]{0,60}(бот|парс|сбор\w* (баз|контакт)|мониторинг|юзербот)", re.I)
PROGRESS = reader.SESSION.parent / "join-progress.json"


async def main(targets: list[str], limit: int, pause: int) -> int:
    from telethon import TelegramClient, errors, functions

    progress = json.loads(PROGRESS.read_text(encoding="utf-8")) if PROGRESS.exists() else {}
    session = reader.SESSION.parent / "joiner"
    session.with_suffix(".session").write_bytes(reader.SESSION.with_suffix(".session").read_bytes())
    api_id, api_hash = reader.credentials()
    client = TelegramClient(str(session), api_id, api_hash, proxy=reader.connection_proxy(),
                            receive_updates=False)
    await client.connect()
    joined = 0
    try:
        reader.verify_account(await client.get_me(), reader.expected_account())
        for handle in targets:
            if progress.get(handle, {}).get("state") in ("joined", "member", "skipped_rules"):
                continue
            if joined >= limit:
                break
            stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            try:
                entity = await client.get_entity(handle)
                if not getattr(entity, "left", True):
                    progress[handle] = {"state": "member", "at": stamp}
                    continue
                if getattr(entity, "megagroup", False):
                    full = await client(functions.channels.GetFullChannelRequest(entity))
                    text = full.full_chat.about or ""
                    if full.full_chat.pinned_msg_id:
                        pinned = await client.get_messages(entity, ids=full.full_chat.pinned_msg_id)
                        text += "\n" + (getattr(pinned, "message", "") or "")
                    hit = FORBID.search(text)
                    if hit:
                        progress[handle] = {"state": "skipped_rules", "at": stamp, "quote": hit.group(0)[:120]}
                        print(f"@{handle}: пропущен по правилам: {hit.group(0)[:80]}")
                        continue
                await client(functions.channels.JoinChannelRequest(entity))
                progress[handle] = {"state": "joined", "at": stamp}
                joined += 1
                print(f"@{handle}: вступили")
                await asyncio.sleep(pause)
            except errors.FloodWaitError as exc:
                progress[handle] = {"state": "flood_wait", "at": stamp, "seconds": exc.seconds}
                print(f"FloodWait {exc.seconds}s — продолжим в следующий запуск")
                break
            except (errors.RPCError, ValueError) as exc:
                progress[handle] = {"state": "error", "at": stamp, "error": type(exc).__name__}
                print(f"@{handle}: ошибка {type(exc).__name__}")
            finally:
                PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        await client.disconnect()
        session.with_suffix(".session").unlink(missing_ok=True)
    left = [h for h in targets if progress.get(h, {}).get("state") not in ("joined", "member", "skipped_rules")]
    print(f"осталось: {len(left)} {' '.join(left)}")
    print(f"JOINED {joined}")
    return joined


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("targets")
    p.add_argument("--limit", type=int, default=6)
    p.add_argument("--pause", type=int, default=120)
    a = p.parse_args()
    asyncio.run(main(Path(a.targets).read_text(encoding="utf-8").split(), a.limit, a.pause))
