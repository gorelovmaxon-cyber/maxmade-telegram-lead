"""Bridge: changedetection.io watches pages -> the same monitor pipeline.

changedetection.io monitors arbitrary pages that have no RSS or API and keeps
every snapshot. This module reads its REST API, extracts only the text that
appeared since the previous snapshot and passes each block through
`monitor.ingest` as source `feed:watch`, so the usual filter, deduplication,
cards and reply drafts apply. It never clicks, logs in or writes to the site.

First run only stores the snapshot and emits nothing: enabling a watch must
not flood the owner with a page that already exists.

Env:
  MONITOR_WATCH_API        base URL, e.g. http://127.0.0.1:5000
  MONITOR_WATCH_KEY        API key from changedetection.io (Settings -> API)
  MONITOR_WATCH_TAG        optional tag filter; empty means every watch
  MONITOR_WATCH_MIN_CHARS  shortest added block to pass on (default 40)

Usage: python watch_feed.py list | python watch_feed.py dry | python watch_feed.py run
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import urllib.request

import monitor

BLOCK_LIMIT = 4000
BLANK = re.compile(r"[ \t\r\f\v]+")


def ensure_schema(db) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS watch_state(
      uuid TEXT PRIMARY KEY, last_ts INTEGER NOT NULL,
      snapshot TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL);
    """)


def normalize(lines: list[str]) -> list[str]:
    return [BLANK.sub(" ", line).strip() for line in lines if line.strip()]


def added(previous: str, current: str) -> list[str]:
    """Text blocks that appeared since the previous snapshot.

    Comparison is by line sets, not by position: listing pages put a new entry
    on top and shift the rest down, and a positional diff would re-report the
    shifted lines as new. An empty previous snapshot yields nothing — the first
    read is a baseline, not a flood.
    """
    old = set(normalize(previous.splitlines()))
    new = normalize(current.splitlines())
    if not old or not new:
        return []
    blocks: list[str] = []
    run: list[str] = []
    for line in new:
        if line in old:
            if run:
                blocks.append("\n".join(run)[:BLOCK_LIMIT])
                run = []
        else:
            run.append(line)
    if run:
        blocks.append("\n".join(run)[:BLOCK_LIMIT])
    return blocks


def stamps(value) -> list[int]:
    """History keys as sorted unix timestamps; the API returns a dict or a list."""
    keys = list(value.keys()) if isinstance(value, dict) else value if isinstance(value, list) else []
    found = set()
    for key in keys:
        try:
            found.add(int(str(key).split(".")[0]))
        except ValueError:
            continue
    return sorted(found)


def snapshot_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("snapshot", "text", "content", "source"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


def client(url: str | None = None, key: str | None = None, timeout: int = 20):
    base = (url if url is not None else os.getenv("MONITOR_WATCH_API", "")).strip().rstrip("/")
    token = (key if key is not None else os.getenv("MONITOR_WATCH_KEY", "")).strip()
    if not base or not token:
        raise ValueError("Нужны MONITOR_WATCH_API и MONITOR_WATCH_KEY")

    def call(path: str):
        request = urllib.request.Request(base + path, headers={
            "x-api-key": token, "Accept": "application/json, text/plain"})
        # The instance is local; a system proxy must not intercept it.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            kind = response.headers.get("Content-Type", "")
        text = raw.decode("utf-8", "replace")
        if "json" in kind:
            try:
                return json.loads(text)
            except ValueError:
                return text
        return text

    return call


def watches(call) -> list[dict]:
    value = call("/api/v1/watch") or {}
    rows = list(value.values()) if isinstance(value, dict) else value
    tag = os.getenv("MONITOR_WATCH_TAG", "").strip().lower()
    result = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("uuid"):
            continue
        if row.get("paused"):
            continue
        tags = [str(x).strip().lower() for x in (row.get("tags") or [])]
        if tag and tag not in tags:
            continue
        result.append(row)
    return result


def collect(db, at: dt.datetime, call=None, minimum: int | None = None) -> list[dict]:
    """New text blocks across all active watches, newest state persisted per watch."""
    if db is None:
        raise ValueError("Сбор требует базу монитора")
    call = call or client()
    minimum = int(os.getenv("MONITOR_WATCH_MIN_CHARS", "40")) if minimum is None else minimum
    ensure_schema(db)
    found: list[dict] = []
    for watch in watches(call):
        uuid = str(watch["uuid"])
        title = str(watch.get("title") or "").strip() or uuid[:8]
        link = str(watch.get("url") or "").strip()
        state = db.execute("SELECT last_ts,snapshot FROM watch_state WHERE uuid=?", (uuid,)).fetchone()
        last = int(state["last_ts"]) if state else 0
        previous = state["snapshot"] if state else ""
        for ts in stamps(call(f"/api/v1/watch/{uuid}/history")):
            if ts <= last:
                continue
            text = snapshot_text(call(f"/api/v1/watch/{uuid}/history/{ts}"))
            for block in added(previous, text):
                if len(block) < minimum:
                    continue
                digest = hashlib.sha256(block.encode("utf-8")).hexdigest()[:12]
                found.append({"feed": "watch", "id": f"{uuid[:8]}:{ts}:{digest}",
                              "text": title + "\n" + block,
                              "published": dt.datetime.fromtimestamp(ts, monitor.UTC),
                              "link": link, "author": None})
            previous = text
            with db:
                db.execute("""INSERT INTO watch_state(uuid,last_ts,snapshot,checked_at) VALUES(?,?,?,?)
                  ON CONFLICT(uuid) DO UPDATE SET last_ts=excluded.last_ts,
                  snapshot=excluded.snapshot,checked_at=excluded.checked_at""",
                  (uuid, ts, text, monitor.iso(at)))
    return found


def preview(call=None) -> str:
    """Read-only listing for `dry`: nothing is compared or stored."""
    rows = watches(call or client())
    if not rows:
        return "Активных наблюдений нет"
    return "\n".join(f"{str(w['uuid'])[:8]}  {w.get('title') or ''}  {w.get('url') or ''}" for w in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Мост changedetection.io -> конвейер монитора")
    parser.add_argument("command", choices=("list", "dry", "run"))
    args = parser.parse_args()
    monitor.load_env()
    try:
        call = client()
        if args.command in ("list", "dry"):
            print(preview(call))
            return 0
        cfg = monitor.Config.from_env()
        db = monitor.connect(cfg.db)
        try:
            at = monitor.now()
            import feeds
            rows = collect(db, at, call)
            print(f"watch: новых блоков {len(rows)}, принято {feeds.ingest(db, cfg, rows, at)}")
        finally:
            db.close()
    except (ValueError, OSError) as exc:
        print(f"Ошибка: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
