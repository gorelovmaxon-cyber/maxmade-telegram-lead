"""Parse connect.tg public order pages (Next.js flight data) into JSON.

Read-only analysis helper: python parse_connect.py <dir_with_html> -> <dir>/ct.json
"""
import collections
import glob
import json
import re
import sys

PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
TEXT = re.compile(r'(?:^|\n)([0-9a-f]+):T([0-9a-f]+),')


def orders(html: str) -> list[dict]:
    s = ''.join(json.loads('"' + c + '"') for c in PUSH.findall(html))
    refs = {}
    for m in TEXT.finditer(s):
        size = int(m.group(2), 16)
        refs['$' + m.group(1)] = s[m.end():].encode('utf-8')[:size].decode('utf-8', 'ignore')
    i = s.find('"initialOrders":')
    if i < 0:
        return []
    arr, _ = json.JSONDecoder().raw_decode(s[i + len('"initialOrders":'):])
    for o in arr:
        d = o.get('description')
        if isinstance(d, str) and d in refs:
            o['description'] = refs[d]
    return arr


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    found = {}
    for f in sorted(glob.glob(sys.argv[1] + '/*.html')):
        if 'kwork' in f:
            continue
        arr = orders(open(f, encoding='utf-8').read())
        print(f[-40:], len(arr))
        for o in arr:
            found[o['slug']] = o
    print('unique', len(found))
    print(collections.Counter(o.get('source') for o in found.values()))
    print(collections.Counter(o.get('niche') for o in found.values()).most_common(12))
    json.dump(list(found.values()), open(sys.argv[1] + '/ct.json', 'w', encoding='utf-8'), ensure_ascii=False)
    for o in list(found.values())[:5]:
        print('---', o['source'], o['postedAt'], o['title'])
        print((o.get('description') or '')[-250:].replace('\n', ' '))


if __name__ == '__main__':
    main()
