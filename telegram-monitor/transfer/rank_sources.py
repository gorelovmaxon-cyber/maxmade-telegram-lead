"""Rank discover_sources.py output into connect / reserve / reject lists.

A source is useful when the active filter finds real orders in its fresh tail,
it is alive, its rules do not forbid monitoring, and it is Russian-language.
Usage: python rank_sources.py discover.json [min_hits_per_week]
"""
from __future__ import annotations

import json
import sys

ALIVE_HOURS = 72          # last post not older than 3 days
MIN_HITS_WEEK = 1.0       # at least ~1 filter-approved order per week


def verdict(row: dict, min_hits: float) -> tuple[str, str]:
    if row.get("error"):
        return "reject", "недоступен: " + row["error"]
    if row.get("skip"):
        return "reject", row["skip"]
    if row.get("forbid_hint"):
        return "reserve", "описание ограничивает мониторинг/поиск клиентов — читать правила вручную"
    if not row.get("tail"):
        return "reject", "нет сообщений"
    if (row.get("last_age_h") or 1e9) > ALIVE_HOURS:
        return "reject", f"неактивен {row['last_age_h']} ч"
    hits = row.get("hits_per_week") or 0
    if hits >= min_hits:
        return "connect", f"{hits} заказов/нед по фильтру"
    if len(row.get("hits") or []) > 0:
        return "reserve", f"редкие заказы ({hits}/нед)"
    return "reject", "заказов по фильтру нет"


def main() -> None:
    rows = json.load(open(sys.argv[1], encoding="utf-8"))
    min_hits = float(sys.argv[2]) if len(sys.argv) > 2 else MIN_HITS_WEEK
    out = {"connect": [], "reserve": [], "reject": []}
    for row in rows:
        v, why = verdict(row, min_hits)
        out[v].append({**{k: row.get(k) for k in ("handle", "kind", "title", "peer_id", "member",
                                                     "participants", "week", "last_age_h",
                                                     "hits_per_week")}, "why": why})
    for key in out:
        out[key].sort(key=lambda r: -(r.get("hits_per_week") or 0))
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
