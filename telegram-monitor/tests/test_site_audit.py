"""Offline tests for the read-only site audit (network mocked)."""
import datetime
import socket
import unittest
import urllib.request
from unittest import mock

import site_audit

THIS_YEAR = datetime.datetime.now().year

TILDA = """<html><head><title>Клиника Ромашка</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Стоматология в Москве">
<script src="https://static.tildacdn.com/js/tilda.js"></script></head>
<body><h1>Стоматология Ромашка</h1>
<a href="#zayavka">Оставить заявку</a>
<a href="tel:+74951234567">Позвонить</a>
<form action="/send"><input name="phone"></form>
<div>© {year} ООО Ромашка</div></body></html>""".replace("{year}", str(THIS_YEAR))

PLAIN = """<html><head><title>Старый сайт</title></head>
<body><h1>Мы работаем с 2012</h1><p>© 2015</p></body></html>"""


def page(body: str, url: str = "https://example.ru/", status: int = 200, seconds: float = 0.8):
    return {"url": url, "final_url": url, "status": status, "body": body,
            "seconds": seconds, "error": "", "content_type": "text/html"}


class PlatformTests(unittest.TestCase):
    def test_tilda(self):
        self.assertEqual(site_audit.detect_platform(TILDA), "Tilda")

    def test_wordpress(self):
        self.assertEqual(site_audit.detect_platform('<link href="/wp-content/a.css">'), "WordPress")

    def test_bitrix(self):
        self.assertEqual(site_audit.detect_platform('<script src="/bitrix/js/a.js">'), "Битрикс")

    def test_unknown(self):
        self.assertEqual(site_audit.detect_platform("<html><body>hi</body></html>"), "не определена")


class FindingTests(unittest.TestCase):
    def test_good_site_has_no_findings(self):
        facts = site_audit.audit_page(TILDA, "https://example.ru/", 200, 0.9)
        self.assertEqual(site_audit.findings(facts, THIS_YEAR), [])

    def test_no_viewport(self):
        facts = site_audit.audit_page(PLAIN, "https://example.ru/", 200, 0.9)
        notes = site_audit.findings(facts, 2026)
        self.assertTrue(any("viewport" in n for n in notes))

    def test_no_contact(self):
        facts = site_audit.audit_page(PLAIN, "https://example.ru/", 200, 0.9)
        notes = site_audit.findings(facts, 2026)
        self.assertTrue(any("формы" in n for n in notes))

    def test_old_copyright(self):
        facts = site_audit.audit_page(PLAIN, "https://example.ru/", 200, 0.9)
        notes = site_audit.findings(facts, 2026)
        self.assertTrue(any("2015" in n for n in notes))

    def test_http(self):
        facts = site_audit.audit_page(TILDA, "http://example.ru/", 200, 0.9)
        self.assertTrue(any("https" in n for n in site_audit.findings(facts, 2026)))

    def test_slow(self):
        facts = site_audit.audit_page(TILDA, "https://example.ru/", 200, 6.2)
        self.assertTrue(any("6.2" in n for n in site_audit.findings(facts, 2026)))

    def test_error_page_stops(self):
        facts = {"status": 404}
        self.assertEqual(site_audit.findings(facts, 2026), ["страница отвечает кодом 404 — возможно, сайт недоступен"])


class SummaryTests(unittest.TestCase):
    def test_summary_mentions_platform_and_speed(self):
        facts = site_audit.audit_page(TILDA, "https://example.ru/", 200, 1.2)
        text = site_audit.summary(facts, 2026)
        self.assertIn("Tilda", text)
        self.assertIn("1.2", text)

    def test_summary_empty_on_error(self):
        self.assertEqual(site_audit.summary({"error": "TimeoutError"}, 2026), "")


class AuditTests(unittest.TestCase):
    def test_network_error_reported(self):
        with mock.patch.object(site_audit, "fetch",
                               return_value={"error": "TimeoutError", "body": "", "status": None, "seconds": 12.0}):
            got = site_audit.audit("https://slow.example/")
        self.assertEqual(got["error"], "TimeoutError")

    def test_facts_returned(self):
        with mock.patch.object(site_audit, "fetch", return_value=page(TILDA)):
            got = site_audit.audit("https://example.ru/")
        self.assertEqual(got["platform"], "Tilda")
        self.assertTrue(got["mobile"])
        self.assertEqual(got["phones"], ["+74951234567"])

    def test_private_addresses_never_open_a_socket(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80)),
                     (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 80, 0, 0))]
        with mock.patch.object(site_audit.socket, "getaddrinfo", return_value=addresses), \
             mock.patch.object(site_audit.socket, "socket") as connection:
            result = site_audit.fetch("http://example.org/")
        self.assertEqual(result["error"], "OSError")
        connection.assert_not_called()

    def test_connection_uses_the_checked_address(self):
        target = ("93.184.216.34", 443)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", target)]
        with mock.patch.object(site_audit.socket, "getaddrinfo", return_value=addresses) as resolve, \
             mock.patch.object(site_audit.socket, "socket") as connection:
            site_audit._public_connection(("example.org", 443), 12)
        resolve.assert_called_once_with("example.org", 443, type=socket.SOCK_STREAM)
        connection.return_value.connect.assert_called_once_with(target)

    def test_redirect_cannot_switch_to_ftp(self):
        original = urllib.request.Request("https://example.org/")
        with self.assertRaises(ValueError):
            site_audit._PublicRedirectHandler().redirect_request(
                original, None, 302, "Found", {}, "ftp://example.org/")


if __name__ == "__main__":
    unittest.main()
