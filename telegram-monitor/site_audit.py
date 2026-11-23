"""Read-only public audit of a customer's existing site (no forms, no login, no writes).

The monitor reads the linked page and follows HTTP(S) redirects to public
addresses. Findings are advisory text on the card; the owner verifies them
before quoting them.

CLI:
    python site_audit.py <url> [--json]
"""
from __future__ import annotations

import argparse
import html
import http.client
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

UA = "Mozilla/5.0 (compatible; MAXMADE-site-check/1.0; +https://maxmadepro.ru/)"
MAX_BYTES = 900_000
TIMEOUT = 12
PLATFORMS = [
    ("Tilda", r"tilda\.ws|tildacdn|static\.tildacdn|tilda-build|data-tilda|tildaforms|tilda-module"),
    ("WordPress", r"wp-content|wp-includes|wp-json"),
    ("Битрикс", r"bitrix/|/bitrix/|bx-"),
    ("Wix", r"wix\.com|wixstatic|parastorage"),
    ("Webflow", r"webflow\.(?:io|com)|w-layout"),
    ("Readymag", r"readymag"),
    ("Craftum", r"craftum\.(?:ru|com)"),
    ("MODX", r"/assets/(?:template|components)/|modx"),
    ("1C-UMI", r"umi-cms|umi_cms"),
    ("React/Next", r"__NEXT_DATA__|/_next/static|data-reactroot|/assets/index-[A-Za-z0-9_-]+\.js"),
    ("Vue/Nuxt", r"__NUXT__|/_nuxt/"),
    ("Vite/SPA", r'<div id="(?:root|app)"></div>'),
]
# Links that look like an order/contact action on the first screen.
CTA = re.compile(r"(?:заявк|оставить\s+заявку|связаться|обсудить|рассчитать|позвонить|написать|обсудить\s+проект|оставить\s+контакт|получить\s+консультац)", re.I)
FORM = re.compile(r"<form\b[^>]*>", re.I)
TEL = re.compile(r'href="tel:([^"]+)"', re.I)
MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
CMS_GENERATOR = re.compile(r'<meta[^>]+name="generator"[^>]+content="([^"]+)"', re.I)
VIEWPORT = re.compile(r'<meta[^>]+name="viewport"[^>]+content="([^"]*)"', re.I)
YEAR = re.compile(r"(?:©|&copy;|\(c\))\s*(?:20\d{2})(?:\s*[-–—]\s*(20\d{2}))?", re.I)
COPYRIGHT_LINE = re.compile(r".{0,60}(?:©|&copy;|\(c\))\s*20\d{2}.{0,60}", re.I)


def _public_connection(address, timeout, source_address=None):
    """Resolve once and connect to a checked address, preserving the TLS hostname."""
    host, port = address
    last_error = OSError("Требуется общедоступный адрес")
    for family, kind, protocol, _, target in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(target[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            continue
        connection = socket.socket(family, kind, protocol)
        try:
            connection.settimeout(timeout)
            if source_address is not None:
                connection.bind(source_address)
            connection.connect(target)
            return connection
        except OSError as exc:
            connection.close()
            last_error = exc
    raise last_error


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(_PublicHTTPConnection, request)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_PublicHTTPSConnection, request, context=self._context)


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username is not None:
        raise ValueError("Требуется публичная ссылка HTTP(S)")


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, timeout: int = TIMEOUT) -> dict:
    """One public GET. Returns status, final url, body and timing; never raises."""
    started = time.monotonic()
    result = {"url": url, "status": None, "final_url": url, "body": "", "seconds": 0.0, "error": "",
              "content_type": ""}
    try:
        _validate_url(url)
        request = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "ru,en;q=0.8",
            "Accept-Encoding": "gzip, deflate"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublicHTTPHandler(),
                                             _PublicHTTPSHandler(), _PublicRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES)
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, MAX_BYTES)
            elif encoding == "deflate":
                raw = zlib.decompressobj(-zlib.MAX_WBITS).decompress(raw, MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            result.update(status=getattr(response, "status", 200),
                          final_url=response.geturl(),
                          body=raw.decode(charset, "replace"),
                          content_type=response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        result.update(status=exc.code, error=f"http_{exc.code}")
    except urllib.error.URLError as exc:
        result.update(error=type(exc.reason).__name__ if hasattr(exc, "reason") else "URLError")
    except (http.client.HTTPException, OSError, ValueError, zlib.error) as exc:
        result.update(error=type(exc).__name__)
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def copyright_years(page: str) -> list[int]:
    """All four-digit years that sit next to a copyright mark."""
    found = []
    for match in YEAR.finditer(page):
        for group in match.groups():
            if group:
                found.append(int(group))
        head = re.search(r"20\d{2}", match.group(0))
        if head:
            found.append(int(head.group(0)))
    return sorted(set(found))


def detect_platform(page: str) -> str:
    low = page.lower()
    for name, pattern in PLATFORMS:
        if re.search(pattern, low):
            return name
    generator = CMS_GENERATOR.search(page)
    return html.unescape(generator.group(1))[:40] if generator else "не определена"


def first_screen(page: str) -> str:
    """Rough approximation of what a visitor sees first: head, nav and the
    opening block of body text. Not a pixel layout - an advisory hint."""
    body = re.search(r"<body\b.*?</body>", page, re.S | re.I)
    chunk = (body.group(0) if body else page)[:14000]
    text = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", chunk, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def audit_page(page: str, final_url: str, status: int, seconds: float) -> dict:
    low = page.lower()
    viewport = VIEWPORT.search(page)
    mobile = bool(viewport and "width=device-width" in viewport.group(1).lower())
    link = re.search(r'<link[^>]+rel="(?:shortcut )?icon"', page, re.I)
    forms = FORM.findall(page)
    tel = TEL.findall(page)
    mail = MAIL.findall(page)
    cta = CTA.findall(first_screen(page))
    copyright_years_found = copyright_years(page)
    copyright_year = max(copyright_years_found) if copyright_years_found else None
    copyright_line = COPYRIGHT_LINE.search(re.sub(r"<[^>]+>", " ", page))
    https = final_url.startswith("https://")
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    description = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', page, re.I)
    h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S | re.I)
    return {
        "https": https, "status": status, "seconds": seconds,
        "platform": detect_platform(page),
        "mobile": mobile, "forms": len(forms), "phones": list(dict.fromkeys(tel))[:3],
        "emails": list(dict.fromkeys(mail))[:3], "cta_on_first_screen": len(cta),
        "copyright_year": copyright_year,
        "copyright_line": html.unescape(re.sub(r"\s+", " ", copyright_line.group(0))).strip() if copyright_line else "",
        "title": html.unescape(re.sub(r"\s+", " ", title.group(1))).strip()[:160] if title else "",
        "description": html.unescape(re.sub(r"\s+", " ", description.group(1))).strip()[:220] if description else "",
        "h1": [html.unescape(re.sub(r"<[^>]+>", " ", x)).strip()[:140] for x in h1][:2],
        "page_bytes": len(low),
    }


def findings(facts: dict, now_year: int) -> list[str]:
    """Short, checkable observations. Each one is phrased as a hint, never as a verdict."""
    out = []
    if facts.get("status") and int(facts["status"]) >= 400:
        out.append(f"страница отвечает кодом {facts['status']} — возможно, сайт недоступен")
        return out
    if not facts.get("https"):
        out.append("сайт открывается без https — браузер помечает соединение как небезопасное")
    if facts.get("seconds", 0) >= 4:
        out.append(f"страница отдаётся за {facts['seconds']} с — посетитель на мобильном успевает уйти")
    elif facts.get("seconds", 0) >= 2.5:
        out.append(f"страница отдаётся за {facts['seconds']} с — есть запас для ускорения")
    if not facts.get("mobile"):
        out.append("нет мета-тега viewport — на телефоне страница может открываться в уменьшенном виде")
    if not facts.get("forms") and not facts.get("phones"):
        out.append("на странице нет формы заявки и телефона — связаться можно только вручную")
    elif not facts.get("cta_on_first_screen"):
        out.append("на первом экране не видно кнопки заявки или «связаться»")
    year = facts.get("copyright_year")
    if year and now_year - year >= 3:
        out.append(f"в подвале {year} год — сайт давно не обновляли")
    if facts.get("platform") in ("Tilda", "WordPress", "Битрикс", "Wix", "Webflow"):
        pass  # platform alone is not a finding, it is context
    return out


def summary(facts: dict, now_year: int) -> str:
    """Two or three lines for a Telegram card."""
    if not facts or facts.get("error"):
        return ""
    parts = []
    head = facts.get("platform") or "платформа не определена"
    if facts.get("status") and int(facts["status"]) == 200:
        head += f", отвечает за {facts['seconds']} с"
    head += ", мобильная версия " + ("есть" if facts.get("mobile") else "не найдена")
    parts.append(head)
    notes = findings(facts, now_year)
    if notes:
        parts.append("Замечания: " + "; ".join(notes[:3]))
    else:
        parts.append("Явных проблем на главной не видно — проверьте вручную.")
    return "\n".join(parts)


def audit(url: str, now_year: int | None = None) -> dict:
    """Full audit of one URL. Network call; returns facts even on failure."""
    import datetime as dt
    now_year = now_year or dt.datetime.now().year
    got = fetch(url)
    if got.get("error") and not got.get("body"):
        return {"url": url, "error": got["error"], "status": got.get("status"), "seconds": got.get("seconds", 0)}
    facts = audit_page(got["body"], got.get("final_url") or url, got.get("status") or 0, got.get("seconds", 0))
    facts.update(url=url, final_url=got.get("final_url"), error="")
    return facts


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only audit of a public site")
    parser.add_argument("url")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    url = args.url if "://" in args.url else "https://" + args.url
    facts = audit(url)
    if args.json:
        print(json.dumps(facts, ensure_ascii=False, indent=1))
    else:
        print(summary(facts, 0) or f"Не удалось: {facts.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
