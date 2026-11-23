"""Analyze collected YouTube video metadata: services, Telegram handles, links, prices, themes.

python yt_analyze.py videos.json
"""
import collections
import json
import re
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
videos = json.load(open(sys.argv[1], encoding="utf-8"))

HANDLE = re.compile(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{4,31})")
URL = re.compile(r"https?://[^\s)\]]+")
PRICE = re.compile(r"\d[\d\s]{1,6}\s?(?:₽|руб)|\$\s?\d{1,4}", re.I)
THEMES = {
    "ключевые слова/мониторинг чатов": r"ключев\w* слов|мониторинг|отслежива",
    "ИИ-фильтр/классификация": r"\bии\b|нейросет|gpt|ai\b|искусственн|llm|openai|claude",
    "парсинг базы/рассылка/инвайт": r"рассылк|инвайт|спам|база\s+(для|контакт)|парсинг\s+(аудитор|участник|базы)",
    "автоотклик/генерация ответа": r"автоотклик|отклик|ответ\w* клиент|генерац\w* (текст|ответ)|продающ",
    "n8n/make/no-code": r"\bn8n\b|\bmake\b|zapier|no.?code",
    "биржи (kwork/fl/upwork)": r"kwork|кворк|fl\.ru|фл\.ру|upwork|habr|хабр|freelancehunt",
    "CRM": r"amocrm|амо|битрикс|bitrix|crm|срм",
    "Reddit/X/LinkedIn": r"reddit|linkedin|twitter|\bx\.com",
    "продажа доступа/подписка": r"подписк|тариф|₽\s*/\s*мес|в месяц|оплат",
}

handles, hosts, prices, themes = collections.Counter(), collections.Counter(), [], collections.Counter()
lang = collections.Counter()
for v in videos:
    text = (v.get("title", "") + "\n" + v.get("description", ""))
    low = text.lower()
    lang["ru" if re.search(r"[а-яё]", v.get("title", ""), re.I) else "en"] += 1
    for h in set(x.lower() for x in HANDLE.findall(text)):
        if not h.endswith(("_bot",)) or True:
            handles[h] += 1
    for u in set(URL.findall(text)):
        host = (urllib.parse.urlsplit(u).hostname or "").removeprefix("www.")
        if host and not host.endswith(("youtube.com", "youtu.be", "vk.com", "instagram.com", "tiktok.com", "dzen.ru")):
            hosts[host] += 1
    for name, pattern in THEMES.items():
        if re.search(pattern, low):
            themes[name] += 1
    for p in PRICE.findall(v.get("description", "")):
        prices.append((p.strip(), v["title"][:60]))

print("видео:", len(videos), "| язык:", dict(lang))
print("\nТЕМЫ (в скольких видео встречается):")
for k, n in themes.most_common():
    print(f"  {n:>3}  {k}")
print("\nСАЙТЫ/СЕРВИСЫ из описаний (топ 40):")
for h, n in hosts.most_common(40):
    print(f"  {n:>3}  {h}")
print("\nTELEGRAM из описаний (топ 50):")
print("  " + ", ".join(f"{h}({n})" for h, n in handles.most_common(50)))
print("\nЦЕНЫ в описаниях (до 25):")
for p, t in prices[:25]:
    print(f"  {p:>12} | {t}")
print("\nСАМЫЕ ПРОСМАТРИВАЕМЫЕ (топ 25):")
for v in sorted(videos, key=lambda x: -(x.get("view_count") or 0))[:25]:
    print(f"  {v.get('view_count', 0):>8} | {v['title'][:85]} | {v.get('channel', '')[:25]}")
