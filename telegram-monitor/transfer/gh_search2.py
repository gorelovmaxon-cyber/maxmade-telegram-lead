"""GitHub: universal/foreign lead-finding systems (social listening, intent leads, AI reply drafts).

Run on the VPS: python3 gh_search2.py  -> prints top repos per query, then READMEs of the strongest.
"""
import base64
import json
import time
import urllib.parse
import urllib.request

QUERIES = [
    "reddit lead generation ai", "social listening leads ai", "intent leads monitoring",
    "reddit keyword monitor leads", "twitter lead finder ai", "linkedin buying intent",
    "ai lead qualification reply", "upwork proposal generator ai", "freelance proposal ai",
    "telegram lead generation", "telegram userbot leads", "discord lead monitoring",
    "hacker news who is hiring scraper", "buying signals monitor", "f5bot", "gummysearch",
    "ai sdr open source", "lead scoring llm", "job hunting agent llm", "auto apply jobs ai agent",
]


def call(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "maxmade-research"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


seen = {}
for q in QUERIES:
    print("===", q)
    try:
        items = call("https://api.github.com/search/repositories?q=" + urllib.parse.quote(q) + "&sort=stars&order=desc&per_page=6")["items"]
    except Exception as exc:  # noqa: BLE001
        print("  error", type(exc).__name__)
        time.sleep(8)
        continue
    for r in items:
        seen[r["full_name"]] = r
        print(f"  {r['stargazers_count']:>6} {r['full_name']} | {(r['description'] or '')[:120]}")
    time.sleep(7)  # unauthenticated search limit: 10/min
