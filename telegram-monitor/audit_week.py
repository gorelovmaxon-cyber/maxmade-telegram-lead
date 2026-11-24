"""Read-only weekly report: what was delivered, what the filter rejected.

Runs against the live business database without writing to it. The report is
what the owner (or an assistant) reviews to correct the filter: delivered
cards with their statuses, rejection reasons, and a sample of rejected texts
that may have been real orders.

Usage:
    python audit_week.py --days 7
    python audit_week.py --days 7 --out report.txt
    python audit_week.py --days 7 --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import Counter

import monitor

SAMPLE_LIMIT = 40
SNIPPET = 220


def connect_ro(path) -> sqlite3.Connection:
    """The live service keeps the file open; never risk a write from a report."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def snippet(text: str | None) -> str:
    return " ".join((text or "").split())[:SNIPPET]


def report(db: sqlite3.Connection, since: dt.datetime) -> dict:
    mark = monitor.iso(since)
    events = db.execute("SELECT COUNT(*) FROM events WHERE received_at>=?", (mark,)).fetchone()[0]
    by_source = db.execute("""
        SELECT s.name, COUNT(*) AS events
        FROM events e JOIN sources s ON s.id=e.source_id
        WHERE e.received_at>=? GROUP BY s.name ORDER BY events DESC""", (mark,)).fetchall()
    rows = db.execute("""
        SELECT c.id, c.kind, c.priority, c.owner_status, c.reason, c.similar_to,
               e.received_at, e.body, e.link, s.name AS source,
               d.state, d.sent_at,
               (SELECT route FROM filter_decisions f WHERE f.candidate_id=c.id
                ORDER BY f.id DESC LIMIT 1) AS route,
               (SELECT reason FROM filter_decisions f WHERE f.candidate_id=c.id
                ORDER BY f.id DESC LIMIT 1) AS filter_reason
        FROM candidates c JOIN events e ON e.id=c.event_id
        JOIN sources s ON s.id=e.source_id
        LEFT JOIN deliveries d ON d.candidate_id=c.id
        WHERE e.received_at>=? ORDER BY e.received_at""", (mark,)).fetchall()
    statuses = Counter(r["owner_status"] for r in rows)
    routes = Counter(r["route"] or "нет решения" for r in rows)
    rejected = [r for r in rows if (r["route"] in ("none", "review")) or r["priority"] == "rejected"]
    delivered = [r for r in rows if r["state"] == "sent"]
    reasons = Counter(r["filter_reason"] or r["reason"] for r in rejected).most_common(15)
    ai = {}
    if has_table(db, "ai_reviews"):
        ai = dict(db.execute("""
            SELECT COUNT(*) AS calls, SUM(ok) AS ok, SUM(COALESCE(input_tokens,0)) AS tin,
                   SUM(COALESCE(output_tokens,0)) AS tout
            FROM ai_reviews WHERE created_at>=?""", (mark,)).fetchone())
    return {
        "since": mark,
        "events": events,
        "candidates": len(rows),
        "by_source": [dict(r) for r in by_source],
        "statuses": dict(statuses),
        "routes": dict(routes),
        "reject_reasons": reasons,
        "ai": ai,
        "delivered": [{
            "id": r["id"], "at": r["received_at"], "source": r["source"], "kind": r["kind"],
            "status": r["owner_status"], "text": snippet(r["body"]), "link": r["link"],
        } for r in delivered],
        "rejected_samples": [{
            "id": r["id"], "at": r["received_at"], "source": r["source"], "route": r["route"],
            "reason": r["filter_reason"] or r["reason"], "text": snippet(r["body"]), "link": r["link"],
        } for r in rejected[:SAMPLE_LIMIT]],
    }


def has_table(db: sqlite3.Connection, name: str) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _copy(target: sqlite3.Connection, table: str, cursor) -> int:
    """Copy rows by column name: old databases carry fewer columns than today."""
    names = [d[0] for d in cursor.description]
    known = {row[1] for row in target.execute(f"PRAGMA table_info({table})")}
    keep = [n for n in names if n in known]
    if not keep:
        return 0
    statement = f"INSERT OR REPLACE INTO {table}({','.join(keep)}) VALUES({','.join('?' for _ in keep)})"
    count = 0
    for row in cursor:
        target.execute(statement, tuple(row[name] for name in keep))
        count += 1
    return count


def export(db: sqlite3.Connection, since: dt.datetime, path) -> dict:
    """Copy one window into a small standalone database for offline review.

    Only rows that belong to the window are copied; secrets, sessions and the
    full history stay on the server.
    """
    from pathlib import Path
    import ai_assist
    mark = monitor.iso(since)
    window = "SELECT id FROM events WHERE received_at>=?"
    candidates = f"SELECT id FROM candidates WHERE event_id IN ({window})"
    target = monitor.connect(Path(path))
    ai_assist.ensure_schema(target)  # the AI tables are created lazily, not by connect()
    counts = {}
    try:
        with target:
            counts["sources"] = _copy(target, "sources", db.execute("SELECT * FROM sources"))
            counts["events"] = _copy(target, "events", db.execute("SELECT * FROM events WHERE received_at>=?", (mark,)))
            counts["candidates"] = _copy(target, "candidates", db.execute(
                f"SELECT * FROM candidates WHERE event_id IN ({window})", (mark,)))
            counts["deliveries"] = _copy(target, "deliveries", db.execute(
                f"SELECT * FROM deliveries WHERE candidate_id IN ({candidates})", (mark,)))
            counts["decisions"] = _copy(target, "filter_decisions", db.execute(
                f"SELECT * FROM filter_decisions WHERE candidate_id IN ({candidates})", (mark,)))
            if has_table(db, "ai_reviews"):
                counts["ai_reviews"] = _copy(target, "ai_reviews", db.execute(
                    f"SELECT * FROM ai_reviews WHERE candidate_id IN ({candidates})", (mark,)))
    finally:
        target.close()
    return counts


def render(value: dict) -> str:
    out = [f"Период: с {value['since']}",
           f"Входящих событий: {value['events']}",
           f"Обработанных карточек: {value['candidates']}", "",
           "По источникам:"]
    for row in value["by_source"]:
        out.append(f"  {row['name']}: {row['events']}")
    out += ["", "Статусы карточек:"] + [f"  {k}: {v}" for k, v in value["statuses"].items()]
    out += ["", "Маршруты фильтра:"] + [f"  {k}: {v}" for k, v in value["routes"].items()]
    out += ["", "Частые причины отсева:"] + [f"  {v} × {k}" for k, v in value["reject_reasons"]]
    ai = value["ai"]
    out += ["", f"ИИ: вызовов {ai.get('calls', 0)}, успешных {ai.get('ok') or 0}, "
                f"токенов {ai.get('tin') or 0}/{ai.get('tout') or 0}"]
    out += ["", f"Доставлено карточек: {len(value['delivered'])}"]
    for row in value["delivered"]:
        out.append(f"  #{row['id']} {row['at'][:16]} [{row['source']}] {row['status']}: {row['text'][:120]}")
    out += ["", f"Отсеяно (для проверки пропусков): {len(value['rejected_samples'])} из показанных"]
    for row in value["rejected_samples"]:
        out.append(f"  #{row['id']} {row['route']} · {row['reason'][:60]}: {row['text'][:160]}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Недельный отчёт монитора без записи в базу")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--export", default="", help="скопировать окно в отдельную базу")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    monitor.load_env()
    path = args.db or os.getenv("MONITOR_DB", "state/monitor.sqlite3")
    since = monitor.now() - dt.timedelta(days=max(1, args.days))
    db = connect_ro(path)
    try:
        if args.export:
            counts = export(db, since, args.export)
            print(f"Выгружено в {args.export}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
            return 0
        value = report(db, since)
    finally:
        db.close()
    text = json.dumps(value, ensure_ascii=False, indent=2) if args.json else render(value)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"Отчёт записан: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
