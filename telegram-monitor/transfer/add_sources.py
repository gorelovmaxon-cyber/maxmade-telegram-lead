"""Append approved public feeds to MONITOR_CLIENT_SOURCES / CHANNELS, with stable peer IDs.

Read-only towards Telegram: resolves usernames (no join, no messages) and writes
config. Every new source gets its peer ID in the same run, so `reader.py run`
never starts with a source/ID length mismatch.
Run as maxmade-admin: sudo sh /tmp/add-sources.sh
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

NEW = ["FreelancehuntProjects", "frilans", "frilanser_vacansii", "Koteyka_Freelancer", "pixeltechspec",
       "fa_chatbots", "freelance_wordpress", "Vibecodingjob", "reelsmakervacancy"]
ENV = Path("/etc/maxmade-monitor/monitor.env")
PROXY_ENV = Path("/etc/maxmade-monitor/proxy.env")


def load_proxy_env() -> None:
    """systemd reads EnvironmentFile itself; a plain shell run may not export it."""
    import urllib.request
    if urllib.request.getproxies() or not PROXY_ENV.exists():
        return
    for line in PROXY_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ").strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

sys.path.insert(0, "/opt/maxmade-monitor/current")
import monitor  # noqa: E402
import reader  # noqa: E402


def read_env() -> dict[str, str]:
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


async def peer_ids(handles: list[str]) -> dict[str, int]:
    from telethon import TelegramClient, utils

    load_proxy_env()
    api_id, api_hash = reader.credentials()
    session = reader.SESSION.parent / "peers"
    session.with_suffix(".session").write_bytes(reader.SESSION.with_suffix(".session").read_bytes())
    client = TelegramClient(str(session), api_id, api_hash, proxy=reader.connection_proxy(), receive_updates=False)
    await client.connect()
    out = {}
    try:
        reader.verify_account(await client.get_me(), reader.expected_account())
        for handle in handles:
            try:
                out[handle] = utils.get_peer_id(await client.get_entity(handle))
            except Exception as exc:  # noqa: BLE001
                print(f"@{handle}: не разрешён {type(exc).__name__}", file=sys.stderr)
            await asyncio.sleep(1.5)
    finally:
        await client.disconnect()
        session.with_suffix(".session").unlink(missing_ok=True)
    return out


def append(values: dict[str, str], key: str, additions: list[str], separator: str = ",") -> str:
    current = [x for x in values.get(key, "").split(",") if x.strip()]
    low = {x.strip().lower().lstrip("@") for x in current}
    merged = current + [a for a in additions if a.lower().lstrip("@") not in low]
    return separator.join(merged)


def main() -> None:
    values = read_env()
    have = {x.strip().lower() for x in values.get("MONITOR_CLIENT_SOURCES", "").split(",") if x.strip()}
    todo = [h for h in NEW if h.lower() not in have]
    peers = asyncio.run(peer_ids(todo))
    todo = [h for h in todo if h in peers]
    if not todo:
        print("нечего добавлять")
        return
    values["MONITOR_CLIENT_SOURCES"] = append(values, "MONITOR_CLIENT_SOURCES", todo)
    values["MONITOR_CLIENT_PEER_IDS"] = append(values, "MONITOR_CLIENT_PEER_IDS", [str(peers[h]) for h in todo])
    values["MONITOR_CLIENT_CHANNELS"] = append(values, "MONITOR_CLIENT_CHANNELS", todo)
    lines = ENV.read_text(encoding="utf-8").splitlines()
    written = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.startswith("#"):
            lines[i] = f"{key}={values[key]}"
            written.add(key)
    for key in ("MONITOR_CLIENT_SOURCES", "MONITOR_CLIENT_PEER_IDS", "MONITOR_CLIENT_CHANNELS"):
        if key not in written:
            lines.append(f"{key}={values[key]}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sources = values["MONITOR_CLIENT_SOURCES"].split(",")
    ids = values["MONITOR_CLIENT_PEER_IDS"].split(",")
    print(f"добавлено: {', '.join(todo)}")
    print(f"источников: {len(sources)}, ID: {len(ids)}")
    missing = [h for h in NEW if h.lower() not in {x.lower() for x in sources}]
    if missing:
        print("не добавлены: " + ", ".join(missing))


if __name__ == "__main__":
    main()
