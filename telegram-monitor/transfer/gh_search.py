"""GitHub catalogue search for comparable order-aggregator projects (read-only).

Run on the VPS: python3 gh_search.py
Prints, per query, the top repositories by stars with a one-line description.
"""
import json
import urllib.parse
import urllib.request

QUERIES = [
    "telegram freelance orders", "telegram заказы", "фриланс парсер", "freelance parser",
    "telegram keyword monitor", "telegram channel aggregator", "tenders telegram bot",
    "kwork parser", "fl.ru parser", "upwork telegram feed", "telegram vacancy parser",
    "freelance exchanges api",
]


def search(query: str) -> list[dict]:
    url = ("https://api.github.com/search/repositories?q=" + urllib.parse.quote(query)
           + "&sort=stars&order=desc&per_page=6")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "maxmade-research"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response).get("items", [])


for q in QUERIES:
    print("===", q)
    try:
        for r in search(q):
            print(f"  {r['stargazers_count']:>5} {r['full_name']} | {(r['description'] or '')[:110]}")
    except Exception as exc:  # noqa: BLE001
        print("  error", type(exc).__name__)
