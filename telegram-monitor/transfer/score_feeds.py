"""Score external order feeds with the active MAXMADE filter (read-only analysis).

python score_feeds.py <dir>   (dir contains ct.json from parse_connect.py, *.xml feeds, kwork.html)
Prints, per source: items, period, filter routes, main/partnership per day, sample hits.
"""
import collections
import datetime as dt
import email.utils
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import order_profile  # noqa: E402

TAG = re.compile(r'<[^>]+>')


def plain(text: str) -> str:
    return re.sub(r'\s+', ' ', TAG.sub(' ', text or '')).strip()


def rss(path: str) -> tuple[str, list[dict]]:
    ch = ET.parse(path).getroot().find('channel')
    items = []
    for i in ch.findall('item'):
        when = email.utils.parsedate_to_datetime(i.findtext('pubDate'))
        items.append({'title': i.findtext('title') or '', 'body': plain(i.findtext('description')),
                      'cat': i.findtext('category') or '', 'at': when, 'link': i.findtext('link')})
    return ch.findtext('title'), items


def kwork(path: str) -> list[dict]:
    t = open(path, encoding='utf-8').read()
    i = t.find('"wants":[')
    arr, _ = json.JSONDecoder().raw_decode(t[i + len('"wants":'):])
    return [{'title': w.get('name', ''), 'body': plain(w.get('description')), 'cat': w.get('category_id'),
             'at': None, 'link': f"https://kwork.ru/projects/{w['id']}",
             'price': w.get('priceLimit')} for w in arr]


def connect(path: str) -> dict[str, list[dict]]:
    out = collections.defaultdict(list)
    for o in json.load(open(path, encoding='utf-8')):
        at = dt.datetime.fromisoformat(o['postedAt'].replace('Z', '+00:00'))
        out['connect.tg/' + o['source']].append({'title': o['title'], 'body': plain(o.get('description')),
                                                 'cat': o.get('niche'), 'at': at,
                                                 'link': 'https://connect.tg/orders/' + o['slug']})
    return out


def score(name: str, items: list[dict]) -> None:
    routes = collections.Counter()
    hits = []
    for it in items:
        a = order_profile.assess((it['title'] + '. ' + it['body'])[:2000])
        routes[a['notification_route']] += 1
        if a['notification_route'] in ('main', 'partnership'):
            hits.append(it)
    dates = [it['at'] for it in items if it['at']]
    span_days = max((max(dates) - min(dates)).total_seconds() / 86400, 0.04) if len(dates) > 1 else None
    per_day = round(len(hits) / span_days, 1) if span_days else None
    print(f'\n=== {name}: {len(items)} шт, период {round(span_days, 2) if span_days else "?"} дн, '
          f'маршруты {dict(routes)}, в бот {len(hits)} (~{per_day}/день)')
    for it in hits[:8]:
        print('  +', it['title'][:90], '|', str(it['cat'])[:30], '|', it['link'])


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    d = sys.argv[1]
    for f in glob.glob(d + '/*.xml'):
        try:
            name, items = rss(f)
        except Exception as exc:  # noqa: BLE001
            print(f, 'skip', type(exc).__name__)
            continue
        score(name, items)
    if os.path.exists(d + '/kwork.html'):
        score('Kwork projects (страница 1)', kwork(d + '/kwork.html'))
    if os.path.exists(d + '/ct.json'):
        for name, items in connect(d + '/ct.json').items():
            score(name, items)


if __name__ == '__main__':
    main()
