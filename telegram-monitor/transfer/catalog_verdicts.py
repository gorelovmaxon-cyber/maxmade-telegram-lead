"""Print verdicts for a scored catalogue (score_list.py output)."""
import json
import sys

rows = json.load(open(sys.argv[1], encoding="utf-8"))
good = [r for r in rows if not r.get("error") and (r.get("last_age_h") or 999) < 72 and (r.get("hits_per_week") or 0) >= 1.0]
good.sort(key=lambda r: -r["hits_per_week"])
print("пригодных:", len(good))
for r in good:
    print("%-26s %-8s %6s last=%6s h/w=%8.2f | %s" % (r["handle"], r.get("kind"), r.get("participants"),
          r.get("last_age_h"), r["hits_per_week"], (r.get("title") or "")[:45]))
print("--- мёртвые/ошибки")
for r in rows:
    if r.get("error") or (r.get("last_age_h") or 0) > 72:
        print(r["handle"], r.get("error") or "нет постов %s ч" % r["last_age_h"])
