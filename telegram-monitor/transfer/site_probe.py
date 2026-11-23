"""Fetch public landing pages of lead services and print title, prices and key claims (read-only)."""
import html
import re
import sys
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
SITES = sys.argv[1:] or [
    "https://leadscan.ru", "https://crmchat.ai", "https://leadgram.io", "https://f5bot.com", "https://syften.com",
    "https://gummysearch.com", "https://octolens.com", "https://leadverse.ai", "https://replyguy.com",
    "https://gojiberry.ai", "https://www.commonroom.io", "https://tgstat.ru", "https://leadconverter.su",
    "https://telega.in", "https://tglead.ru", "https://leadsbot.ru", "https://www.vacancy-bot.ru",
]
PRICE = re.compile(r"(\d[\d\s]{1,7}\s?(?:₽|руб|\$|USD|€)|\$\s?\d{1,4}(?:[.,]\d{2})?(?:\s?/\s?(?:mo|month|мес))?)", re.I)


def plain(page: str) -> str:
    page = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


for url in SITES:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"\n## {url}: {type(exc).__name__} {getattr(exc, 'code', '')}")
        continue
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', raw, re.I)
    text = plain(raw)
    prices = list(dict.fromkeys(p.strip() for p in PRICE.findall(text)))[:8]
    print(f"\n## {url}\n title: {html.unescape(title.group(1).strip()) if title else '-'}")
    print(f" desc: {html.unescape(desc.group(1))[:220] if desc else '-'}")
    print(f" prices: {prices}")
    print(f" text: {text[:700]}")
