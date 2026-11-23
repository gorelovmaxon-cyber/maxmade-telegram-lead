import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import monitor
import reader


class ReaderSetupTests(unittest.TestCase):
    def test_system_proxy_uses_existing_settings_and_decodes_credentials(self):
        with patch.dict(os.environ, {"MONITOR_CLIENT_NETWORK": "system_proxy"}), \
                patch("reader.urllib.request.getproxies", return_value={"https": "http://user:p%40ss@127.0.0.1:10809"}):
            proxy = reader.connection_proxy()
        self.assertEqual(proxy, {"proxy_type": "http", "addr": "127.0.0.1", "port": 10809,
                                 "rdns": True, "username": "user", "password": "p@ss"})

    def test_missing_system_proxy_does_not_fall_back_to_direct(self):
        with patch.dict(os.environ, {"MONITOR_CLIENT_NETWORK": "system_proxy"}), \
                patch("reader.urllib.request.getproxies", return_value={}):
            with self.assertRaises(ValueError):
                reader.connection_proxy()

    def test_account_identity_accepts_selected_user_and_rejects_other_or_bot(self):
        reader.verify_account(SimpleNamespace(username="LIL_LUCHIK_GOD", bot=False), "lil_luchik_god")
        for user in (SimpleNamespace(username="other_account", bot=False),
                     SimpleNamespace(username=None, bot=False),
                     SimpleNamespace(username="lil_luchik_god", bot=True)):
            with self.assertRaises(ValueError):
                reader.verify_account(user, "lil_luchik_god")

    def test_wrong_account_is_stopped_before_source_resolution(self):
        fake = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
                               is_user_authorized=AsyncMock(return_value=True),
                               get_me=AsyncMock(return_value=SimpleNamespace(username="main_account", bot=False)))
        module = SimpleNamespace(TelegramClient=lambda *a, **k: fake, events=SimpleNamespace())
        env = {"MONITOR_API_ID": "12345", "MONITOR_API_HASH": "a" * 32,
               "MONITOR_CLIENT_ACCOUNT": "lil_luchik_god"}
        with tempfile.TemporaryDirectory() as directory:
            cfg = monitor.Config(Path(directory) / "monitor.sqlite3", client_sources=("top_ads_freelance",))
            with patch.dict(os.environ, env), patch.dict("sys.modules", {"telethon": module}), \
                    patch.object(reader, "SESSION", Path(directory) / "client"), \
                    patch.object(reader, "resolve_joined", new_callable=AsyncMock) as resolve:
                with self.assertRaisesRegex(ValueError, "lil_luchik_god"):
                    asyncio.run(reader.execute("check", cfg))
                resolve.assert_not_awaited()
                fake.disconnect.assert_awaited_once()

    def test_local_configuration_preserves_bot_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("MONITOR_BOT_TOKEN=local-placeholder\nMONITOR_OWNER_ID=42\n"
                            "MONITOR_API_ID=\nMONITOR_API_HASH=\n", encoding="utf-8")
            with patch.object(reader, "SESSION", Path(directory) / "state" / "client"):
                reader.save_settings(path, {"MONITOR_API_ID": "12345", "MONITOR_API_HASH": "a" * 32})
            values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())
            self.assertEqual(values["MONITOR_BOT_TOKEN"], "local-placeholder")
            self.assertEqual(values["MONITOR_OWNER_ID"], "42")
            self.assertEqual(values["MONITOR_API_ID"], "12345")
            self.assertEqual(values["MONITOR_API_HASH"], "a" * 32)
            self.assertFalse((Path(directory) / "state" / "client-config.tmp").exists())

    def test_channel_post_contact_comes_from_text_not_channel(self):
        import datetime as dt
        def event(sender, text):
            return SimpleNamespace(message=SimpleNamespace(edit_date=None), id=7, raw_text=text,
                                   date=dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc),
                                   sender=SimpleNamespace(username=sender))
        post = "Нужен сайт салона. Контакт: @wthxz больше заказов @Zakaz_Design_Bot t.me/zakazi_designers"
        self.assertEqual(reader.extract(event("zakazi_designers", post), "zakazi_designers", -100)["author"], "@wthxz")
        self.assertIsNone(reader.extract(event("fa_sites", "Лендинг на Tilda, отклик на бирже"), "fa_sites", -100)["author"])
        self.assertEqual(reader.extract(event("client_one", post), "tgfreela", -100)["author"], "@client_one")

    def _fake_client(self, entities):
        async def get_entity(name):
            if name not in entities:
                raise ValueError("No user has that username")
            return entities[name]
        return SimpleNamespace(get_entity=get_entity, get_permissions=AsyncMock())

    def test_channel_needs_explicit_optin_and_one_bad_source_is_skipped(self):
        group = SimpleNamespace(id=1, megagroup=True, broadcast=False)
        channel = SimpleNamespace(id=2, megagroup=False, broadcast=True)
        client = self._fake_client({"group_one": group, "feed_one": channel})
        ids = {id(group): -1001, id(channel): -1002}
        with patch("telethon.utils.get_peer_id", side_effect=lambda e: ids[id(e)]):
            with patch.dict(os.environ, {"MONITOR_CLIENT_CHANNELS": ""}):
                with self.assertRaises(ValueError):
                    asyncio.run(reader.resolve_joined(client, ("group_one", "feed_one")))
                skipped = {}
                resolved = asyncio.run(reader.resolve_joined(client, ("group_one", "feed_one", "gone_one"), skipped))
                self.assertEqual(set(resolved), {-1001})
                self.assertEqual(set(skipped), {"feed_one", "gone_one"})
            with patch.dict(os.environ, {"MONITOR_CLIENT_CHANNELS": "@Feed_One"}):
                skipped = {}
                resolved = asyncio.run(reader.resolve_joined(client, ("group_one", "feed_one"), skipped))
                self.assertEqual(set(resolved), {-1001, -1002})
                self.assertEqual(skipped, {})


if __name__ == "__main__":
    unittest.main()
