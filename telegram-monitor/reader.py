"""Read-only user-account adapter for explicitly joined Telegram groups.

`configure` saves local API settings; `login` performs interactive authorization. `check` and `run` require
an existing local session. This module never sends Telegram messages.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import getpass
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import monitor
import runtime_status as health


SESSION = Path("state/client_account")


def expected_account() -> str:
    username = os.getenv("MONITOR_CLIENT_ACCOUNT", "").strip().lower().lstrip("@")
    if not re.fullmatch(r"[a-z0-9_]{5,32}", username):
        raise ValueError("Укажите выбранный аккаунт в MONITOR_CLIENT_ACCOUNT")
    return username


def channel_optin() -> set[str]:
    """Usernames of broadcast channels explicitly approved for reading (MONITOR_CLIENT_CHANNELS)."""
    return {x.strip().lower().lstrip("@") for x in os.getenv("MONITOR_CLIENT_CHANNELS", "").split(",") if x.strip()}


def verify_account(me: object, username: str) -> None:
    if (getattr(me, "username", None) or "").lower() != username or getattr(me, "bot", False):
        raise ValueError(f"Сессия не принадлежит выбранному аккаунту @{username}; чтение остановлено")


def save_settings(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output = []
    seen = set()
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in values:
            if key not in seen:
                output.append(key + "=" + values[key])
                seen.add(key)
        else:
            output.append(line)
    output.extend(key + "=" + value for key, value in values.items() if key not in seen)
    # Stage under ignored state/, then replace atomically on the same disk.
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    staging = SESSION.parent / "client-config.tmp"
    try:
        staging.write_text("\n".join(output) + "\n", encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def configure() -> None:
    username = expected_account()
    print(f"Локальная настройка для @{username}. Значения останутся в .env на этом ПК.")
    api_id = input("API ID из my.telegram.org: ").strip()
    api_hash = getpass.getpass("API hash (ввод скрыт): ").strip()
    os.environ["MONITOR_API_ID"] = api_id
    os.environ["MONITOR_API_HASH"] = api_hash
    credentials()
    save_settings(Path(".env"), {"MONITOR_API_ID": api_id, "MONITOR_API_HASH": api_hash})
    print("API-настройки сохранены локально. Теперь выполните вход в выбранный аккаунт.")


def credentials() -> tuple[int, str]:
    try:
        api_id = int(os.getenv("MONITOR_API_ID", "0"))
    except ValueError:
        raise ValueError("MONITOR_API_ID должен быть числом") from None
    api_hash = os.getenv("MONITOR_API_HASH", "")
    if api_id <= 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise ValueError("Нужны MONITOR_API_ID и MONITOR_API_HASH из my.telegram.org")
    return api_id, api_hash


def connection_proxy() -> dict | None:
    # A setting ending in _PROXY would be mistaken for a protocol proxy by urllib.
    mode = os.getenv("MONITOR_CLIENT_NETWORK", "direct")
    if mode == "direct":
        return None
    if mode != "system_proxy":
        raise ValueError("MONITOR_CLIENT_NETWORK: допустимы direct или system_proxy")
    configured = urllib.request.getproxies()
    raw = configured.get("https") or configured.get("http")
    if not raw:
        raise ValueError("Системный прокси не настроен; подключение остановлено")
    try:
        url = urllib.parse.urlsplit(raw if "://" in raw else "http://" + raw)
        if url.scheme not in ("http", "socks4", "socks5") or not url.hostname:
            raise ValueError()
        result = {"proxy_type": url.scheme, "addr": url.hostname,
                  "port": url.port or (80 if url.scheme == "http" else 1080), "rdns": True}
        if url.username:
            result["username"] = urllib.parse.unquote(url.username)
            result["password"] = urllib.parse.unquote(url.password or "")
        return result
    except ValueError:
        raise ValueError("Не удалось прочитать системный прокси; его параметры не выводятся") from None


CONTACT = re.compile(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{4,31})\b")


def text_contact(body: str, own: str) -> str | None:
    """First @handle / t.me link in the post that is not the channel or a bot."""
    for match in CONTACT.finditer(body):
        name = match.group(1)
        if name.lower() != own.lower() and not name.lower().endswith("bot") and name.lower() != "joinchat":
            return name
    return None


def extract(event: object, username: str, peer_id: int) -> dict:
    message = event.message
    published = event.date
    if published.tzinfo is None:
        published = published.replace(tzinfo=monitor.UTC)
    edited = getattr(message, "edit_date", None)
    sender = getattr(event, "sender", None)
    author = getattr(sender, "username", None)
    if author and author.lower() == username.lower():
        # Aggregator channel posts are "sent" by the channel itself; the real
        # customer is the contact written in the post. Never show the channel as contact.
        author = text_contact(event.raw_text or "", username)
    return {
        "source_id": "client:" + str(peer_id),
        "message_id": str(event.id),
        "body": event.raw_text or "",
        "published": published,
        "received": monitor.now(),
        "edit_version": monitor.iso(edited) if edited else monitor.iso(published),
        "link": f"https://t.me/{username}/{event.id}",
        "author": "@" + author if author else None,
        "source_name": "@" + username,
        "method": "client_api",
    }


async def resolve_joined(client, usernames: tuple[str, ...], skipped: dict[str, str] | None = None):
    """Resolve joined sources. With `skipped`, one unavailable source is recorded
    there instead of stopping every other source (needed for many groups)."""
    from telethon import utils

    resolved = {}
    for username in usernames:
        try:
            try:
                entity = await client.get_entity(username)
                # Ask only about this account in this group. Do not enumerate
                # dialogs, participants, contacts, or message history.
                await client.get_permissions(entity, "me")
            except Exception as exc:
                raise ValueError(f"Доступ к @{username} не подтверждён: {type(exc).__name__}") from None
            peer_id = utils.get_peer_id(entity)
            # Broadcast channels are read-only order feeds; allowed only by explicit opt-in.
            is_channel = getattr(entity, "broadcast", False) and username in channel_optin()
            if not getattr(entity, "megagroup", False) and not is_channel:
                raise ValueError(f"@{username} не подтверждён как группа; канал требует отдельной проверки условий")
        except ValueError as exc:
            if skipped is None:
                raise
            skipped[username] = str(exc)
            continue
        resolved[peer_id] = (username, entity)
    return resolved


async def execute(command: str, cfg: monitor.Config) -> None:
    try:
        from telethon import TelegramClient, events
    except ImportError:
        raise ValueError("Telethon не установлен; используйте .venv и requirements-client.txt") from None

    api_id, api_hash = credentials()
    username = expected_account()
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION), api_id, api_hash, catch_up=False,
                            proxy=connection_proxy())
    if command == "login":
        # The user types the login code and optional 2FA password in their own console.
        try:
            await client.start(
                phone=lambda: getpass.getpass(f"Номер аккаунта @{username} (ввод скрыт): "),
                code_callback=lambda: getpass.getpass("Код входа Telegram (ввод скрыт): "),
                password=lambda: getpass.getpass("Пароль двухэтапной проверки (ввод скрыт): "),
            )
            me = await client.get_me()
            verify_account(me, username)
            print(f"Клиентская сессия @{username} создана локально; account id {me.id}")
        finally:
            await client.disconnect()
        return

    if not cfg.client_sources:
        raise ValueError("Добавьте один явный username в MONITOR_CLIENT_SOURCES")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Нет клиентской сессии; сначала выполните reader.py login в своей консоли")
        verify_account(await client.get_me(), username)
        skipped: dict[str, str] = {}
        # One unavailable source must not silence all others; it is reported and marked instead.
        resolved = await resolve_joined(client, cfg.client_sources, skipped)
        if command == "check":
            for peer_id, (username, _) in resolved.items():
                print(f"@{username}: участник, peer_id {peer_id}; доступ к новым событиям ещё не проверен")
            for name, reason in skipped.items():
                print(f"@{name}: ПРОПУЩЕН — {reason}")
            return

        if command != "run":
            raise ValueError("Неизвестная команда")
        if len(cfg.client_peer_ids) != len(cfg.client_sources):
            raise ValueError("После reader.py check закрепите MONITOR_CLIENT_PEER_IDS в .env")
        expected = dict(zip(cfg.client_sources, cfg.client_peer_ids))
        for peer_id, (name, _) in list(resolved.items()):
            if expected[name] != peer_id:
                skipped[name] = f"ID @{name} изменился; источник остановлен до ручной сверки"
                del resolved[peer_id]
        if not resolved:
            raise ValueError("Ни один источник не доступен: " + "; ".join(skipped.values()))
        db = monitor.connect(cfg.db)
        try:
            with db:
                for peer_id, (name, _) in resolved.items():
                    db.execute("INSERT INTO sources(id,name,method,access_state) VALUES(?,?,?,?) "
                               "ON CONFLICT(id) DO UPDATE SET access_state='member',method='client_api'",
                               ("client:" + str(peer_id), "@" + name, "client_api", "member"))
                for name in skipped:
                    peer = expected.get(name)
                    if peer:
                        db.execute("UPDATE sources SET access_state='unavailable' WHERE id=?", ("client:" + str(peer),))
            for name, reason in skipped.items():
                print(f"@{name}: пропущен — {reason}", file=sys.stderr)
            health.update("reader", skipped_sources=sorted(skipped))

            entities = [entity for _, entity in resolved.values()]

            async def on_event(event):
                source = resolved.get(event.chat_id)
                if not source:
                    return
                username, _ = source
                fields = extract(event, username, event.chat_id)
                monitor.ingest(db, cfg, **fields)
                health.update("reader", last_event_at=health.stamp())

            client.add_event_handler(on_event, events.NewMessage(chats=entities))
            client.add_event_handler(on_event, events.MessageEdited(chats=entities))
            print(f"Чтение новых сообщений ({len(resolved)}): {', '.join('@' + x for x, _ in resolved.values())}; исходящих сообщений нет")
            async def heartbeat():
                from telethon import functions
                while True:
                    try:
                        await asyncio.wait_for(client(functions.PingRequest(ping_id=0)), timeout=15)
                        health.update("reader", state="connected", last_network_ok=health.stamp(), error_type=None)
                    except Exception as exc:
                        health.update("reader", state="retrying", error_type=type(exc).__name__)
                    await asyncio.sleep(30)

            heartbeat_task = asyncio.create_task(heartbeat())
            try:
                await client.run_until_disconnected()
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
        finally:
            db.close()
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only client adapter for joined public groups")
    parser.add_argument("command", choices=("configure", "login", "check", "run"))
    args = parser.parse_args()
    try:
        monitor.load_env()
        if args.command == "configure":
            configure()
            return 0
        cfg = monitor.Config.from_env()
        if args.command == "run":
            with monitor.single_instance(cfg.db.with_suffix('.reader.lock')):
                with health.lifecycle("reader"):
                    asyncio.run(execute(args.command, cfg))
        else:
            asyncio.run(execute(args.command, cfg))
    except (ValueError, RuntimeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("Ввод отменён; чтение не запускалось.", file=sys.stderr)
        return 2
    except Exception as exc:
        # RPC/network exceptions may contain a phone or request details.
        print(f"Подключение не завершено: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
