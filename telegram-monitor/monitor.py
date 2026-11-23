"""Small, dependency-free Telegram demand monitor. Network is opt-in."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import http.client
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
import order_profile
import order_profile_v2
import filter_policy
import transport_diagnostics as transport
import ai_assist

UTC = dt.timezone.utc
MOSCOW = ZoneInfo("Europe/Moscow")
STATUSES = {"new", "taken", "replied", "dialog", "closed", "not_fit", "do_not_contact"}
# Two rows: the first is the decision, the second closes the loop and feeds reports.
BUTTONS = {"taken": "Взял", "not_fit": "Не подходит", "replied": "Ответил"}
BUTTONS_SECOND = {"dialog": "Диалог", "closed": "Договорились"}
ACTIONS = {**BUTTONS, **BUTTONS_SECOND}
DRAFT_HEADER = "\n\nНаш отклик\n"
STOP = False
if os.name == 'nt':
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding='utf-8')


def load_env(path: Path = Path('.env')) -> None:
    if not path.exists():
        return
    for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError(f'Неверная строка {line_no} в .env')
        key, value = line.split('=', 1)
        if not re.fullmatch(r'MONITOR_[A-Z_]+', key):
            raise ValueError('Неверное имя настройки в .env')
        os.environ.setdefault(key, value.strip().strip('"'))


@contextlib.contextmanager
def single_instance(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Другой экземпляр монитора уже работает') from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def clean(value: object, limit: int = 2000) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))[:limit]


@dataclass(frozen=True)
class Config:
    db: Path
    token: str = ""
    owner_id: int = 0
    source_ids: tuple[int, ...] = ()
    client_sources: tuple[str, ...] = ()
    client_peer_ids: tuple[int, ...] = ()
    feeds: tuple[str, ...] = ()
    quiet_start: int = 23
    quiet_end: int = 9
    retention_days: int = 30
    urgent_hours: int = 24

    @classmethod
    def from_env(cls) -> "Config":
        try:
            ids = tuple(int(x.strip()) for x in os.getenv("MONITOR_SOURCE_IDS", "").split(",") if x.strip())
            client_sources = tuple(x.strip().lower().lstrip('@') for x in os.getenv("MONITOR_CLIENT_SOURCES", "").split(",") if x.strip())
            client_peer_ids = tuple(int(x.strip()) for x in os.getenv("MONITOR_CLIENT_PEER_IDS", "").split(",") if x.strip())
            config = cls(
                db=Path(os.getenv("MONITOR_DB", "state/monitor.sqlite3")),
                token=os.getenv("MONITOR_BOT_TOKEN", ""),
                owner_id=int(os.getenv("MONITOR_OWNER_ID", "0")),
                source_ids=ids,
                client_sources=client_sources,
                client_peer_ids=client_peer_ids,
                # Exchange/web feeds (feeds.py); cards are delivered only for feeds listed here.
                feeds=tuple(x.strip().lower() for x in os.getenv("MONITOR_FEEDS", "").split(",") if x.strip()),
                quiet_start=int(os.getenv("MONITOR_QUIET_START", "23")),
                quiet_end=int(os.getenv("MONITOR_QUIET_END", "9")),
                retention_days=int(os.getenv("MONITOR_RETENTION_DAYS", "30")),
                urgent_hours=int(os.getenv("MONITOR_URGENT_HOURS", "24")),
            )
        except ValueError as exc:
            raise ValueError("Некорректное число в конфигурации MONITOR_*") from exc
        if not (0 <= config.quiet_start <= 23 and 0 <= config.quiet_end <= 23):
            raise ValueError("Тихие часы должны быть от 0 до 23")
        if not (1 <= config.retention_days <= 365 and 1 <= config.urgent_hours <= 168):
            raise ValueError("Проверьте MONITOR_RETENTION_DAYS и MONITOR_URGENT_HOURS")
        if config.owner_id < 0 or any(x >= 0 for x in ids):
            raise ValueError("MONITOR_OWNER_ID — положительный личный ID; источники — отрицательные chat_id")
        if any(not re.fullmatch(r"[a-zA-Z0-9_]{5,32}", x) for x in client_sources):
            raise ValueError("MONITOR_CLIENT_SOURCES: укажите публичные usernames без ссылок")
        if any(x >= 0 for x in client_peer_ids) or (client_peer_ids and len(client_peer_ids) != len(client_sources)):
            raise ValueError("MONITOR_CLIENT_PEER_IDS: отрицательные ID в порядке MONITOR_CLIENT_SOURCES")
        if any(not re.fullmatch(r"[a-z0-9_]{2,20}", x) for x in config.feeds):
            raise ValueError("MONITOR_FEEDS: короткие имена лент через запятую, например fl,workspace,kwork,connect")
        return config

    def live_ready(self) -> None:
        if not self.token or self.owner_id <= 0 or not (self.source_ids or self.client_peer_ids):
            raise ValueError("Live требует MONITOR_BOT_TOKEN, подтверждённый MONITOR_OWNER_ID и хотя бы один источник")
        if self.client_sources and len(self.client_peer_ids) != len(self.client_sources):
            raise ValueError("Для клиентского источника закрепите MONITOR_CLIENT_PEER_IDS после reader.py check")
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", self.token):
            raise ValueError("MONITOR_BOT_TOKEN имеет неверный формат")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sources(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, method TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
      rules TEXT NOT NULL DEFAULT '', last_ok TEXT, checkpoint TEXT, access_state TEXT NOT NULL DEFAULT 'unknown');
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), message_id TEXT NOT NULL,
      edit_version TEXT NOT NULL, published_at TEXT NOT NULL, received_at TEXT NOT NULL,
      link TEXT, author TEXT, body TEXT, expires_at TEXT NOT NULL,
      UNIQUE(source_id,message_id));
    CREATE TABLE IF NOT EXISTS candidates(
      id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL UNIQUE REFERENCES events(id), kind TEXT NOT NULL,
      reason TEXT NOT NULL, priority TEXT NOT NULL, task_state TEXT NOT NULL DEFAULT 'active',
      owner_status TEXT NOT NULL DEFAULT 'new', similar_to INTEGER REFERENCES candidates(id));
    CREATE TABLE IF NOT EXISTS deliveries(
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL UNIQUE REFERENCES candidates(id),
      recipient INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
      next_at TEXT NOT NULL, sent_at TEXT, message_id INTEGER, error_code TEXT);
    CREATE INDEX IF NOT EXISTS delivery_due ON deliveries(state,next_at);
    """)
    db.commit()
    filter_policy.ensure_schema(db)
    return db


def meta(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _classify_v2(body: str, published: dt.datetime, config: Config) -> tuple[str, str, str, str]:
    s = body.lower().replace("ё", "е")
    if re.search(r"исполнитель найден|уже нашли исполнителя|задача закрыта|вакансия закрыта", s):
        return "closed_signal", "Сообщение о закрытии задачи", "rejected", "possibly_closed"
    if re.search(r"дизайнер.{0,20}(интерьер|архитект)|3d.дизайнер|дизайнер.{0,20}(облож|ютуб)", s) and not re.search(r"сайт|лендинг|веб", s):
        return "other", "Дизайн вне заявленных веб-услуг", "rejected", "active"
    a = order_profile_v2.assess(body)
    kind, reason = a['kind'], a['reason']
    if kind in ('offer', 'other'):
        return kind, reason, 'rejected', 'active'
    if kind == 'demand':
        if a['fit'].startswith('частичное'):
            return kind, reason + '; технология требует проверки', 'digest', 'active'
        age = now() - published
        if age.total_seconds() < -300:
            return "ambiguous", "Дата публикации в будущем; проверить вручную", "digest", "active"
        if age > dt.timedelta(hours=config.urgent_hours):
            return "demand", "Прямой запрос, но публикация старше срочного окна", "digest", "active"
        return kind, reason, 'urgent', 'active'
    return kind, reason, 'digest', 'active'


def classify(body: str, published: dt.datetime, config: Config) -> tuple[str, str, str, str]:
    a = order_profile.assess(body)
    state = 'possibly_closed' if a['kind'] == 'closed_signal' else 'active'
    if a['notification_route'] not in ('main','partnership'):
        return a['kind'], a['reason'], 'rejected', state
    age = now() - published
    if age.total_seconds() < -300:
        return 'ambiguous', 'Дата публикации в будущем; проверить вручную', 'rejected', 'active'
    priority = 'urgent' if -300 <= age.total_seconds() <= config.urgent_hours * 3600 else 'digest'
    if a['service_fit'] == 'needs_verification': priority = 'digest'
    return a['kind'], a['reason'], priority, state


def reclassify(db: sqlite3.Connection, cfg: Config, candidate_ids: tuple[int, ...]) -> None:
    """Explicit local review; preserve event times, owner statuses and deliveries."""
    with db:
        for candidate_id in candidate_ids:
            row = db.execute('SELECT e.body,e.published_at FROM candidates c JOIN events e ON e.id=c.event_id WHERE c.id=?', (candidate_id,)).fetchone()
            if row is None or row['body'] is None:
                raise ValueError('Нет сохранённого текста для классификации')
            result = classify(row['body'], dt.datetime.fromisoformat(row['published_at']), cfg)
            db.execute('UPDATE candidates SET kind=?,reason=?,priority=?,task_state=? WHERE id=?', (*result, candidate_id))
        set_meta(db, 'order_profile_version', order_profile.profile()['version'])


def _ingest_v2(db: sqlite3.Connection, cfg: Config, source_id: str, message_id: str,
           body: str, published: dt.datetime, received: dt.datetime, edit_version: str = "",
           link: str | None = None, author: str | None = None, source_name: str | None = None,
           method: str | None = None) -> int:
    fragment = clean(body, 2001)
    body = (fragment[:2000-len(order_profile.TRUNCATED_MARKER)-1] + ' ' + order_profile.TRUNCATED_MARKER
            if len(fragment) > 2000 else fragment)
    kind, reason, priority, task_state = _classify_v2(body, published, cfg)
    expires = iso(received + dt.timedelta(days=cfg.retention_days))
    with db:
        db.execute("INSERT INTO sources(id,name,method,last_ok,access_state) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET last_ok=excluded.last_ok,access_state='ok'",
                   (source_id, clean(source_name or source_id, 100), method or ("synthetic" if source_id == "synthetic" else "bot_api"), iso(received), "ok"))
        old = db.execute("SELECT id,body,edit_version FROM events WHERE source_id=? AND message_id=?", (source_id, message_id)).fetchone()
        if old and old["body"] == body and old["edit_version"] == edit_version:
            return int(db.execute("SELECT id FROM candidates WHERE event_id=?", (old["id"],)).fetchone()[0])
        if old:
            event_id = old["id"]
            db.execute("UPDATE events SET body=?,edit_version=?,received_at=?,expires_at=?,link=?,author=? WHERE id=?",
                       (body, edit_version, iso(received), expires, link, author, event_id))
        else:
            cur = db.execute("INSERT INTO events(source_id,message_id,edit_version,published_at,received_at,link,author,body,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                             (source_id, message_id, edit_version, iso(published), iso(received), link, author, body, expires))
            event_id = cur.lastrowid
        similar = db.execute("SELECT c.id FROM candidates c JOIN events e ON e.id=c.event_id WHERE e.body=? AND e.id<>? AND e.source_id<>? LIMIT 1", (body, event_id, source_id)).fetchone()
        db.execute("INSERT INTO candidates(event_id,kind,reason,priority,task_state,similar_to) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(event_id) DO UPDATE SET kind=excluded.kind,reason=excluded.reason,priority=excluded.priority,task_state=excluded.task_state",
                   (event_id, kind, reason, priority, task_state, similar[0] if similar else None))
        candidate_id = int(db.execute("SELECT id FROM candidates WHERE event_id=?", (event_id,)).fetchone()[0])
        status = db.execute("SELECT owner_status FROM candidates WHERE id=?", (candidate_id,)).fetchone()[0]
        if priority != "rejected" and status == "new" and cfg.owner_id > 0:
            db.execute("INSERT OR IGNORE INTO deliveries(candidate_id,recipient,next_at) VALUES(?,?,?)",
                       (candidate_id, cfg.owner_id, iso(received)))
        return candidate_id


def ingest(db: sqlite3.Connection, cfg: Config, source_id: str, message_id: str,
           body: str, published: dt.datetime, received: dt.datetime, edit_version: str = '',
           link: str | None = None, author: str | None = None, source_name: str | None = None,
           method: str | None = None) -> int:
    old = db.execute('''SELECT e.*,c.id AS cid,c.owner_status FROM events e JOIN candidates c ON c.event_id=e.id
      WHERE e.source_id=? AND e.message_id=?''',(source_id,message_id)).fetchone()
    active = filter_policy.get_meta(db,'filter_active_version',filter_policy.V2)
    prior = filter_policy.latest(db,old['cid']) if old else None
    # Existing V3 events retain their policy after rollback; rollback concerns future events.
    protect_legacy = old and filter_policy.get_meta(db,'filter_initial_cutoff') and old['id'] <= int(filter_policy.get_meta(db,'filter_initial_cutoff'))
    if not filter_policy.is_v3(active) and not prior and not protect_legacy:
        return _ingest_v2(db,cfg,source_id,message_id,body,published,received,edit_version,link,author,source_name,method)
    fragment = clean(body,2001)
    body = (fragment[:2000-len(order_profile.TRUNCATED_MARKER)-1]+' '+order_profile.TRUNCATED_MARKER if len(fragment)>2000 else fragment)
    if old and old['body']==body and old['edit_version']==edit_version:return int(old['cid'])
    protected = old and not prior
    expires = iso(received+dt.timedelta(days=cfg.retention_days))
    a = order_profile.assess(body)
    kind,reason,priority,task_state = classify(body,published,cfg)
    if kind=='ambiguous' and a['notification_route']=='main':
        a=dict(a,kind=kind,reason=reason,notification_route='review')
    settings = order_profile.profile()['notification_policy']
    with db:
        db.execute('''INSERT INTO sources(id,name,method,last_ok,access_state) VALUES(?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET last_ok=excluded.last_ok,access_state='ok' ''',
          (source_id,clean(source_name or source_id,100),method or 'bot_api',iso(received),'ok'))
        if old:
            event_id=old['id'];cid=old['cid']
            # Preserve the previous original even for pre-policy edits, without reclassification.
            filter_policy.remember(db,cid,old['body'] or '',old['author'],old['link'],source_id,old['edit_version'],old['received_at'],False)
            db.execute('UPDATE events SET body=?,edit_version=?,received_at=?,expires_at=?,link=?,author=? WHERE id=?',
              (body,edit_version,iso(received),expires,link,author,event_id))
            if protected:
                filter_policy.remember(db,cid,body,author,link,source_id,edit_version,iso(received),False)
                return int(cid)
        else:
            event_id=db.execute('''INSERT INTO events(source_id,message_id,edit_version,published_at,received_at,link,author,body,expires_at)
              VALUES(?,?,?,?,?,?,?,?,?)''',(source_id,message_id,edit_version,iso(published),iso(received),link,author,body,expires)).lastrowid
            cid=db.execute('INSERT INTO candidates(event_id,kind,reason,priority,task_state) VALUES(?,?,?,?,?)',
              (event_id,kind,reason,priority,task_state)).lastrowid
        duplicate=filter_policy.duplicate(db,body,author,received,settings,a['notification_route'],cid)
        duplicate_id=duplicate['candidate_id'] if duplicate else None
        changed=bool(old and filter_policy.materially_changed(old['body'] or '',body))
        change=('material_edit' if changed else 'cosmetic_edit') if old else (
          'cross_source_repost' if duplicate and duplicate['source_id']!=source_id else 'repost' if duplicate else 'new')
        # Exact matching jobs are suppressed only when the linked original was main-eligible.
        if duplicate and duplicate['original_main'] and a['notification_route'] in ('main','partnership'):priority='rejected'
        db.execute('UPDATE candidates SET kind=?,reason=?,priority=?,task_state=?,similar_to=? WHERE id=?',
          (kind,reason,priority,task_state,duplicate_id,cid))
        decision_id=filter_policy.record(db,cid,a,iso(received),edit_version,duplicate_id,change)
        filter_policy.remember(db,cid,body,author,link,source_id,edit_version,iso(received),a['notification_route'] in ('main','partnership') and not duplicate_id)
        status=db.execute('SELECT owner_status FROM candidates WHERE id=?',(cid,)).fetchone()[0]
        delivery=db.execute('SELECT state FROM deliveries WHERE candidate_id=?',(cid,)).fetchone()
        if status=='new' and cfg.owner_id>0:
            if delivery and delivery['state']=='sent' and changed and (a['notification_route'] in ('main','partnership') or a['kind']=='closed_signal' and prior and prior['route'] in ('main','partnership')):
                db.execute('INSERT OR IGNORE INTO filter_updates(decision_id,candidate_id,recipient,next_at) VALUES(?,?,?,?)',
                  (decision_id,cid,cfg.owner_id,iso(received)))
            elif priority!='rejected':
                db.execute('INSERT OR IGNORE INTO deliveries(candidate_id,recipient,next_at) VALUES(?,?,?)',(cid,cfg.owner_id,iso(received)))
        return int(cid)


def quiet(cfg: Config, at: dt.datetime) -> bool:
    hour = at.astimezone(MOSCOW).hour
    if cfg.quiet_start == cfg.quiet_end:
        return False
    if cfg.quiet_start < cfg.quiet_end:
        return cfg.quiet_start <= hour < cfg.quiet_end
    return hour >= cfg.quiet_start or hour < cfg.quiet_end


def allowed_sources(cfg: Config) -> tuple[str, ...]:
    allowed = (tuple(str(x) for x in cfg.source_ids) + tuple("client:" + str(x) for x in cfg.client_peer_ids)
               + tuple("feed:" + x for x in cfg.feeds))
    return allowed or ("synthetic",)


def due_rows(db: sqlite3.Connection, cfg: Config, at: dt.datetime,
             candidate_ids: tuple[int, ...] | None = None) -> list[sqlite3.Row]:
    allowed = allowed_sources(cfg)
    placeholders = ",".join("?" for _ in allowed)
    scope = ""
    scope_args: tuple[int, ...] = ()
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        scope_args = tuple(int(cid) for cid in candidate_ids)
        scope = " AND c.id IN (" + ",".join("?" for _ in scope_args) + ")"
    rows = db.execute(f"""SELECT d.*, c.priority,c.reason,c.kind,c.owner_status,c.similar_to,
            e.body,e.published_at,e.received_at,e.edit_version,e.link,e.author,s.name AS source_name,
            (SELECT version FROM filter_decisions f WHERE f.candidate_id=c.id ORDER BY id DESC LIMIT 1) AS filter_version
            FROM deliveries d JOIN candidates c ON c.id=d.candidate_id
            JOIN events e ON e.id=c.event_id JOIN sources s ON s.id=e.source_id
            WHERE d.state='pending' AND d.next_at<=? AND d.recipient=? AND e.source_id IN ({placeholders})
              AND c.owner_status='new' AND c.priority<>'rejected' {scope}
            ORDER BY d.id LIMIT 20""", (iso(at), cfg.owner_id, *allowed, *scope_args)).fetchall()
    if quiet(cfg, at):
        return []
    return [row for row in rows if not waiting_for_ai(db, row, at)]


def waiting_for_ai(db: sqlite3.Connection, row: sqlite3.Row, at: dt.datetime) -> bool:
    """Hold a card while its AI block is still being written.

    A card is created due immediately, while the AI worker runs on a timer, so
    without a hold the model would always lose the race. The wait is bounded: a
    slow or failing model delays the card by at most MONITOR_AI_HOLD_MINUTES and
    then it goes out without the block, because a late order is worth less than
    one with a missing draft."""
    if not ai_assist.configured():
        return False
    hold = ai_hold_seconds()
    if hold <= 0:
        return False
    received = dt.datetime.fromisoformat(row['received_at'])
    if (at - received).total_seconds() >= hold:
        return False
    with contextlib.suppress(Exception):
        if ai_assist.cached(db, int(row['candidate_id']), str(row['edit_version']), ai_route(row)):
            return False
    return True


def fit_telegram_text(text: str, units: int) -> str:
    encoded = text.encode("utf-16-le")
    if len(encoded) <= units * 2:
        return text
    return encoded[:(units - 1) * 2].decode("utf-16-le", errors="ignore").rstrip() + "…"


def render(row: sqlite3.Row, draft: str | None = "", context_note: str = "") -> str:
    body = clean(row["body"]).strip() or "[текст заказа недоступен]"
    topic = order_profile.project_topic(body, row["kind"])
    header = f"№{row['candidate_id']}\n\nЗадание\n"
    context = f"\n\n{fit_telegram_text(context_note, 350)}" if context_note else ""
    footer = f"{context}\n\nТема\n{topic}"
    if draft is not None:
        reply = fit_telegram_text(draft.strip() or "Черновик пока недоступен.", 1500)
        footer += DRAFT_HEADER + reply
    remaining = 4000 - len((header + footer).encode("utf-16-le")) // 2
    return header + fit_telegram_text(body, remaining) + footer


class ApiError(Exception):
    def __init__(self, code: int, retry_after: int = 0):
        super().__init__(f"Telegram API: HTTP {code}")
        self.code, self.retry_after = code, retry_after


class UnknownDelivery(Exception):
    pass


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, payload: dict, timeout: int = 45) -> dict:
        # The token is necessarily in Bot API URL; never log request/exception URLs.
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        trace = transport.Trace(method, timeout, os.getenv("MONITOR_HTTP_BACKEND", "urllib"))
        if os.getenv("MONITOR_HTTP_BACKEND", "urllib") == "curl":
            # Windows curl uses Schannel. Config goes through stdin, so the token
            # and message content never appear in the child process command line.
            def quote(value: str) -> str:
                return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

            config = ("url = " + quote(url) + "\n"
                      "header = \"Content-Type: application/json\"\n"
                      "request = \"POST\"\n"
                      "data-raw = " + quote(json.dumps(payload, ensure_ascii=True)) + "\n")
            try:
                result = subprocess.run(["curl.exe", "-q", "--config", "-", "--silent", "--show-error",
                                         "--max-time", str(timeout)], input=config, capture_output=True,
                                        text=True, encoding="utf-8", timeout=timeout + 3, check=False)
                if result.returncode:
                    trace.finish("network_error", curl_exit=result.returncode)
                    raise UnknownDelivery("Сетевой результат неизвестен")
                data = json.loads(result.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                trace.finish("invalid_response" if isinstance(exc, ValueError) else "network_error", exc)
                raise UnknownDelivery("Сетевой результат неизвестен") from None
        else:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            try:
                with transport.open_request(req, timeout, trace) as response:
                    trace.record["http_status"] = response.status
                    data = json.load(response)
            except urllib.error.HTTPError as exc:
                try:
                    obj = json.load(exc)
                    retry = int(obj.get("parameters", {}).get("retry_after", 0))
                except (ValueError, KeyError):
                    retry = 0
                trace.finish("http_error", http_status=exc.code, retry_after_s=retry)
                raise ApiError(exc.code, retry) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                trace.finish("invalid_response" if isinstance(exc, ValueError) else "network_error", exc)
                raise UnknownDelivery("Сетевой результат неизвестен") from None
            except http.client.HTTPException as exc:
                trace.finish("network_error", exc)
                raise
        if not data.get("ok"):
            trace.finish("api_error", api_code=int(data.get("error_code", 500)))
            raise ApiError(int(data.get("error_code", 500)), int(data.get("parameters", {}).get("retry_after", 0)))
        trace.finish("ok")
        return data["result"]


def signature(token: str, candidate_id: int, action: str) -> str:
    data = f"{candidate_id}:{action}".encode()
    return hmac.new(token.encode(), data, hashlib.sha256).hexdigest()[:16]


def keyboard(cfg: Config, candidate_id: int, original: str = '', original_label: str = 'Оригинал',
             *, has_draft: bool = False) -> dict:
    def row(actions: dict) -> list[dict]:
        return [{"text": label, "callback_data": f"c:{candidate_id}:{action}:{signature(cfg.token,candidate_id,action)}"}
                for action, label in actions.items()]
    rows = [row(BUTTONS), row(BUTTONS_SECOND)]
    if has_draft:
        rows.append(row({"copy": "Отклик для копирования"}))
    if re.fullmatch(r'https?://[^\s]+', original):
        with contextlib.suppress(ValueError):
            if urllib.parse.urlsplit(original).hostname:
                rows.append([{"text": original_label, "url": original}])
    return {"inline_keyboard": rows}


def ai_draft(db: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    """Read the cached reply without calling the model during delivery."""
    if not ai_assist.configured():
        return None
    with contextlib.suppress(Exception):
        hit = ai_assist.cached(db, int(row["candidate_id"]), str(row["edit_version"]), ai_route(row))
        if hit is not None and hit["ok"]:
            draft, note = hit["draft"] or "", hit["draft_note"] or ""
            if note:
                return f"{draft}\n\nПеред отправкой: {note}".strip() if draft else f"Отклик на проверке. {note}"
            return draft
    return ""


def card_draft(db: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    reply = ai_draft(db, row)
    if reply == "" and ai_assist.drafts_only():
        import offline_outreach
        return offline_outreach.draft(row["body"] or "")
    return reply


def copyable_draft(reply: str | None) -> str:
    reply = (reply or "").strip()
    if reply.startswith(("Отклик на проверке.", "Отклик не подготовлен.", "Перед отправкой:",
                         "Черновик пока недоступен.")):
        return ""
    return reply.partition("\n\nПеред отправкой:")[0].rstrip()


JUDGE_KINDS = ('ambiguous', 'offer', 'other', 'closed_signal', 'partnership', 'vacancy')


def ai_route(row: sqlite3.Row) -> str:
    """Same route choice as ai_worker, so the cache key always matches."""
    if ai_assist.drafts_only():
        return 'draft'
    kind = row['kind'] if 'kind' in row.keys() else ''
    if ai_assist.full_mode():
        # Every card that survived the rules gets the whole pass: verdict,
        # site review and a draft, written by the same rules the owner set.
        return 'full'
    return 'judge' if kind in JUDGE_KINDS else 'draft'


def strict() -> bool:
    """Opt-in: let the model's verdict remove a card instead of only labelling it."""
    return not ai_assist.drafts_only() and os.getenv('MONITOR_AI_STRICT', '').strip().lower() in ('1', 'true', 'yes')


def ai_hold_seconds() -> int:
    """How long a card may wait for its AI block before going out without one."""
    try:
        return max(0, int(os.getenv('MONITOR_AI_HOLD_MINUTES', '5'))) * 60
    except ValueError:
        return 300


def ai_worker(db: sqlite3.Connection, cfg: Config, limit: int = 8) -> dict:
    """Pre-compute AI blocks for pending cards, one candidate at a time.
    Never sends anything and never changes the owner's status."""
    stats = {'judged': 0, 'drafted': 0, 'reviewed': 0, 'skipped': 0, 'errors': 0}
    if not ai_assist.configured():
        return stats
    # Cards still held for their block come first: they are the ones whose
    # absence would delay delivery. Older cards follow.
    sources = allowed_sources(cfg)
    placeholders = ",".join("?" for _ in sources)
    rows = db.execute(f"""SELECT c.id AS candidate_id, c.kind, c.priority, c.owner_status,
             e.body, e.edit_version, e.received_at, d.sent_at FROM candidates c
             JOIN events e ON e.id=c.event_id
             JOIN deliveries d ON d.candidate_id=c.id
             WHERE c.owner_status='new' AND c.priority<>'rejected'
               AND e.body IS NOT NULL AND e.body<>''
               AND d.state='pending' AND d.recipient=? AND e.source_id IN ({placeholders})
             ORDER BY d.id LIMIT ?""", (cfg.owner_id, *sources, limit)).fetchall()
    for row in rows:
        cid, version = int(row['candidate_id']), str(row['edit_version'])
        if ai_assist.budget_left(db) <= 0:
            stats['skipped'] += 1
            continue
        route = ai_route(row)
        if ai_assist.cached(db, cid, version, route) is None:
            got = ai_assist.review(db, cid, version, row['body'] or '', route)
            if not got.get('ok') and ai_assist.retryable(got.get('error', '')):
                stats['errors'] += 1
                continue
            if not got.get('ok'):
                stats['errors'] += 1
            elif route == 'full':
                stats['reviewed'] += 1
                # The verdict is a label on the card. Dropping the card is a
                # separate, explicit decision (MONITOR_AI_STRICT), because a
                # wrong rejection costs more than one extra card.
                if got.get('verdict') == 'not_fit' and strict():
                    with db:
                        db.execute("UPDATE candidates SET priority='rejected', reason=reason||'; ИИ: не подходит' WHERE id=?",
                                   (cid,))
            elif route == 'judge':
                stats['judged'] += 1
                # A grey card the model rejects is not shown as an order.
                if got.get('verdict') == 'not_fit' and row['priority'] != 'urgent':
                    with db:
                        db.execute("UPDATE candidates SET priority='rejected', reason=reason||'; ИИ: не подходит' WHERE id=?",
                                   (cid,))
            else:
                stats['drafted'] += 1
                # The judge runs once per real order too, but only decides the
                # card when the owner has enabled strict selection.
                if strict():
                    verdict = ai_assist.review(db, cid, version, row['body'] or '', 'judge')
                    if verdict.get('ok') and verdict.get('verdict') == 'not_fit':
                        with db:
                            db.execute("UPDATE candidates SET priority='rejected', reason=reason||'; ИИ: не подходит' WHERE id=?",
                                       (cid,))
        else:
            stats['skipped'] += 1
    return stats


def deliver(db: sqlite3.Connection, cfg: Config, sink: Telegram, at: dt.datetime,
            candidate_ids: tuple[int, ...] | None = None) -> int:
    count = 0
    for row in due_rows(db, cfg, at, candidate_ids):
        with db:
            db.execute("UPDATE deliveries SET state='sending',attempts=attempts+1 WHERE id=? AND state='pending'", (row["id"],))
        try:
            import browser_context
            note = browser_context.card_note(db, row['candidate_id'], row['edit_version'])
            original_label = 'Публикация в группе' if note.startswith('Пересылка.') else 'Оригинал'
            draft = card_draft(db, row)
            result = sink.call("sendMessage", {"chat_id": cfg.owner_id,
                                               "text": render(row, draft, note),
                                               "disable_web_page_preview": True,
                                               "reply_markup": keyboard(cfg, row["candidate_id"], row["link"] or '', original_label,
                                                                        has_draft=bool(copyable_draft(draft)))}, timeout=20)
        except ApiError as exc:
            state = ("uncertain" if exc.code >= 500 else
                     "failed" if exc.code in (400, 401, 403) or row["attempts"] >= 4 else "pending")
            delay = max(exc.retry_after, min(300, 2 ** min(row["attempts"] + 1, 8)))
            with db:
                db.execute("UPDATE deliveries SET state=?,next_at=?,error_code=? WHERE id=?",
                           (state, iso(at + dt.timedelta(seconds=delay)), f"api_{exc.code}", row["id"]))
        except UnknownDelivery:
            with db:
                db.execute("UPDATE deliveries SET state='uncertain',error_code='network_unknown' WHERE id=?", (row["id"],))
        else:
            # Telegram's message date measures when the server accepted the
            # notification, rather than when this loop began the request.
            stamp = result.get("date")
            sent_at = dt.datetime.fromtimestamp(stamp, UTC) if isinstance(stamp, int) else now()
            with db:
                db.execute("UPDATE deliveries SET state='sent',sent_at=?,message_id=?,error_code=NULL WHERE id=?",
                           (iso(sent_at), result.get("message_id"), row["id"]))
            count += 1
    return count


def deliver_revisions(db, cfg, sink, at, source_ids):
    """The same sole worker sends append-only revision notices, never rewrites sent cards."""
    if quiet(cfg,at) or not source_ids:return 0
    marks=','.join('?' for _ in source_ids)
    rows=db.execute('''SELECT u.*,f.kind,f.reason,f.assessment,f.edit_version,e.source_id,c.owner_status,
      s.name AS source_name FROM filter_updates u JOIN filter_decisions f ON f.id=u.decision_id
      JOIN candidates c ON c.id=u.candidate_id JOIN events e ON e.id=c.event_id JOIN sources s ON s.id=e.source_id
      WHERE u.state='pending' AND u.next_at<=? AND u.recipient=? AND c.owner_status='new'
      AND e.source_id IN ('''+marks+') ORDER BY u.id LIMIT 5',(iso(at),cfg.owner_id,*source_ids)).fetchall()
    count=0
    for row in rows:
        original=db.execute('SELECT body,link FROM publication_versions WHERE candidate_id=? AND edit_version=? ORDER BY id DESC LIMIT 1',
          (row['candidate_id'],row['edit_version'])).fetchone()
        if not original:continue
        import browser_context
        context_note=browser_context.card_note(db,row['candidate_id'],row['edit_version'])
        text=(f"Изменение запроса #{row['candidate_id']}\nИсточник: {row['source_name']}\nПричина: {row['reason']}\n"
          f"Оригинал: {original['link'] or 'не указан'}\nТекст: {clean(original['body'],500)}\n"
          f"{order_profile.card_guidance(original['body'])}\n{context_note}\nМаксим проверяет и отвечает сам.")
        if len(text.encode('utf-16-le'))//2>4000:
            text=f"Изменение запроса #{row['candidate_id']}\n{row['reason']}\nОригинал: {original['link'] or 'не указан'}\n{context_note}\nПроверьте обновлённые условия в оригинале."
        with db:
            claimed=db.execute("UPDATE filter_updates SET state='sending',attempts=attempts+1 WHERE id=? AND state='pending'",(row['id'],)).rowcount
        if not claimed:continue
        try:
            result=sink.call('sendMessage',{'chat_id':cfg.owner_id,'text':text,'disable_web_page_preview':True},timeout=20)
        except ApiError as exc:
            state='uncertain' if exc.code>=500 else 'failed' if exc.code in (400,401,403) or row['attempts']>=4 else 'pending'
            with db:db.execute('UPDATE filter_updates SET state=?,next_at=?,error_code=? WHERE id=?',
              (state,iso(at+dt.timedelta(seconds=max(exc.retry_after,30))),f'api_{exc.code}',row['id']))
        except Exception:
            # Unknown HTTP/network result is never blindly retried.
            with db:db.execute("UPDATE filter_updates SET state='uncertain',error_code='network_unknown' WHERE id=?",(row['id'],))
        else:
            stamp=result.get('date');sent=dt.datetime.fromtimestamp(stamp,UTC) if isinstance(stamp,int) else now()
            with db:db.execute("UPDATE filter_updates SET state='sent',sent_at=?,message_id=?,error_code=NULL WHERE id=?",(iso(sent),result.get('message_id'),row['id']))
            count+=1
    return count


def handle_callback(db: sqlite3.Connection, cfg: Config, update: dict, api: Telegram | None = None) -> bool:
    cb = update.get("callback_query") or {}
    user = cb.get("from") or {}
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    match = re.fullmatch(r"c:(\d+):(taken|not_fit|replied|dialog|closed|copy):([0-9a-f]{16})", cb.get("data", ""))
    if user.get("id") != cfg.owner_id or chat.get("id") != cfg.owner_id or not match:
        return False
    candidate_id, action, sig = int(match[1]), match[2], match[3]
    if not hmac.compare_digest(sig, signature(cfg.token, candidate_id, action)):
        return False
    row = db.execute("SELECT message_id FROM deliveries WHERE candidate_id=? AND state='sent' AND recipient=?", (candidate_id, cfg.owner_id)).fetchone()
    if not row or row[0] != msg.get("message_id"):
        return False
    if action == "copy":
        if not api or not cb.get("id"):
            return False
        draft = copyable_draft((msg.get("text") or "").rpartition(DRAFT_HEADER)[2])
        status = "В карточке нет готового текста"
        if draft:
            try:
                api.call("sendMessage", {"chat_id": cfg.owner_id, "text": draft, "disable_notification": True,
                                        "disable_web_page_preview": True,
                                        "reply_parameters": {"message_id": row[0], "allow_sending_without_reply": True}}, timeout=20)
            except (ApiError, UnknownDelivery):
                status = "Не удалось подтвердить доставку текста"
            else:
                status = "Текст отправлен отдельным сообщением"
        with contextlib.suppress(ApiError, UnknownDelivery):
            api.call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": status})
        return True
    with db:
        db.execute("UPDATE candidates SET owner_status=? WHERE id=?", (action, candidate_id))
    if api and cb.get("id"):
        with contextlib.suppress(ApiError, UnknownDelivery):
            api.call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Статус сохранён"})
    return True


def telegram_link(chat: dict, message_id: int) -> str | None:
    username = chat.get("username", "")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return f"https://t.me/{username}/{message_id}"
    return None


def process_update(db: sqlite3.Connection, cfg: Config, update: dict, api: Telegram | None = None) -> None:
    if "callback_query" in update:
        handle_callback(db, cfg, update, api)
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post") or update.get("edited_channel_post")
    if msg:
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id in cfg.source_ids and isinstance(msg.get("message_id"), int):
            stamp = msg.get("date")
            if isinstance(stamp, int):
                published = dt.datetime.fromtimestamp(stamp, UTC)
                edited = msg.get("edit_date", stamp)
                author = (msg.get("from") or {}).get("username")
                author = f"@{author}" if author else None
                ingest(db, cfg, str(chat_id), str(msg["message_id"]), msg.get("text") or msg.get("caption") or "",
                       published, now(), str(edited), telegram_link(chat, msg["message_id"]), author,
                       chat.get("title") or str(chat_id))
    with db:
        set_meta(db, "update_id", str(update["update_id"]))


def prune(db: sqlite3.Connection, at: dt.datetime) -> int:
    with db:
        cur = db.execute("UPDATE events SET body=NULL,author=NULL WHERE expires_at<? AND body IS NOT NULL", (iso(at),))
        db.execute('''UPDATE publication_versions SET body='',link=NULL WHERE candidate_id IN
          (SELECT c.id FROM candidates c JOIN events e ON e.id=c.event_id WHERE e.expires_at<?)''',(iso(at),))
        db.execute('''UPDATE filter_decisions SET assessment='{}' WHERE candidate_id IN
          (SELECT c.id FROM candidates c JOIN events e ON e.id=c.event_id WHERE e.expires_at<?)''',(iso(at),))
    return cur.rowcount


def probe_sources(db: sqlite3.Connection, cfg: Config, api: Telegram, bot_id: int) -> None:
    for sid in cfg.source_ids:
        try:
            result = api.call("getChatMember", {"chat_id": sid, "user_id": bot_id})
            state = "ok" if result.get("status") not in ("left", "kicked") else "denied"
        except ApiError as exc:
            state = f"api_{exc.code}"
        except UnknownDelivery:
            state = "network_unknown"
        with db:
            db.execute("UPDATE sources SET access_state=? WHERE id=?", (state, str(sid)))


def stop(*_: object) -> None:
    global STOP
    STOP = True


def recover_interrupted(db: sqlite3.Connection) -> None:
    with db:
        db.execute("UPDATE deliveries SET state='uncertain',error_code='process_interrupted' WHERE state='sending'")
        db.execute("UPDATE filter_updates SET state='uncertain',error_code='process_interrupted' WHERE state='sending'")


def send_weekly_report(db: sqlite3.Connection, cfg: Config, api: Telegram, at: dt.datetime) -> bool:
    """One card a week with the funnel. Absent, not an error: a failed send is retried next tick."""
    key = at.strftime("%G-W%V")
    if meta(db, "report_sent", "") == key:
        return False
    if at.weekday() != 0 or at.hour < 10:
        return False
    try:
        api.call("sendMessage", {"chat_id": cfg.owner_id, "text": report(db, cfg, 7),
                                 "disable_web_page_preview": True}, timeout=20)
    except (ApiError, UnknownDelivery):
        return False
    with db:
        set_meta(db, "report_sent", key)
    return True


def run_live(db: sqlite3.Connection, cfg: Config) -> None:
    import runtime_status as health
    cfg.live_ready()
    api = Telegram(cfg.token)
    # getMe validates token. It never enrolls the first person who writes to the bot.
    me = api.call("getMe", {})
    if api.call("getWebhookInfo", {}).get("url"):
        raise ValueError("У бота включён webhook; отключите его штатно перед long polling")
    print(f"Bot API ready: bot id {me['id']}; owner id {cfg.owner_id}; sources {len(cfg.source_ids)}")
    for sid in cfg.source_ids:
        with db:
            db.execute("INSERT OR IGNORE INTO sources(id,name,method,access_state) VALUES(?,?,?,?)", (str(sid), str(sid), "bot_api", "unverified"))
    probe_sources(db, cfg, api, me["id"])
    signal.signal(signal.SIGINT, stop)
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGTERM, stop)
    backoff = 2
    last_maintenance = 0.0
    while not STOP:
        try:
            updates = api.call("getUpdates", {"offset": int(meta(db, "update_id", "0")) + 1,
                                               "timeout": 25, "limit": 50,
                                               "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post", "callback_query"]}, timeout=35)
            for update in updates:
                process_update(db, cfg, update, api)
            deliver(db, cfg, api, now())
            send_weekly_report(db, cfg, api, now())
            health.update("monitor", state="connected", last_network_ok=health.stamp(), error_type=None)
            if time.monotonic() - last_maintenance > 3600:
                prune(db, now())
                probe_sources(db, cfg, api, me["id"])
                last_maintenance = time.monotonic()
            backoff = 2
        except ApiError as exc:
            health.update("monitor", state="retrying", error_type="ApiError", api_code=exc.code)
            print(f"Bot API HTTP {exc.code}; pause", file=sys.stderr)
            if exc.code in (401, 403, 409):
                raise RuntimeError(f"Bot API HTTP {exc.code}: проверьте токен, права или другого getUpdates потребителя") from None
            time.sleep(max(exc.retry_after, backoff))
            backoff = min(backoff * 2, 300)
        except UnknownDelivery:
            health.update("monitor", state="retrying", error_type="UnknownDelivery")
            print("Bot API connection unavailable; retrying", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


def demo(db: sqlite3.Connection, cfg: Config) -> None:
    cfg = Config(cfg.db, owner_id=1, urgent_hours=cfg.urgent_hours)
    t = now()
    examples = [
        ("1", "Нужен лендинг для автоимпорта", t),
        ("2", "Делаю сайты, беру заказы", t),
        ("1", "Нужен лендинг для автоимпорта", t),
        ("3", "Ищем дизайнера, пришлите портфолио", t - dt.timedelta(hours=30)),
        ("4", "Возможно, пора обновить сайт", t),
        ("5", "Ищу работу разработчиком сайта", t),
        ("6", "Исполнитель найден, задача закрыта", t),
    ]
    for mid, body, published in examples:
        cid = ingest(db, cfg, "synthetic", mid, body, published, t, "1")
        row = db.execute("SELECT kind,priority,reason FROM candidates WHERE id=?", (cid,)).fetchone()
        print(f"#{cid} {row['kind']}/{row['priority']}: {row['reason']}")
    print(f"Локальная очередь: {len(due_rows(db,cfg,t))}; сеть не использована")


def report(db: sqlite3.Connection, cfg: Config, days: int = 7) -> str:
    """Per-source funnel: cards shown, replies, dialogs, deals, and median first response."""
    since = iso(now() - dt.timedelta(days=days))
    allowed = allowed_sources(cfg)
    marks = ",".join("?" for _ in allowed)
    rows = db.execute(f"""SELECT s.name AS source,
             SUM(CASE WHEN c.priority='rejected' THEN 1 ELSE 0 END) AS screened,
             SUM(CASE WHEN c.priority<>'rejected' THEN 1 ELSE 0 END) AS shown,
             SUM(CASE WHEN c.owner_status='taken' THEN 1 ELSE 0 END) AS taken,
             SUM(CASE WHEN c.owner_status IN ('replied','dialog','closed') THEN 1 ELSE 0 END) AS replied,
             SUM(CASE WHEN c.owner_status IN ('dialog','closed') THEN 1 ELSE 0 END) AS dialog,
             SUM(CASE WHEN c.owner_status='closed' THEN 1 ELSE 0 END) AS deals
             FROM candidates c JOIN events e ON e.id=c.event_id JOIN sources s ON s.id=e.source_id
             WHERE e.received_at>=? AND e.source_id IN ({marks}) GROUP BY s.name ORDER BY shown DESC""",
                  (since, *allowed)).fetchall()
    lines = [f"Сводка за {days} дн."]
    total = {"shown": 0, "replied": 0, "deals": 0}
    for row in rows:
        total["shown"] += row["shown"] or 0
        total["replied"] += row["replied"] or 0
        total["deals"] += row["deals"] or 0
        if not row["shown"] and not row["screened"]:
            continue
        lines.append(f"{row['source']}: показано {row['shown']}, ответил {row['replied']}, "
                     f"диалог {row['dialog']}, сделка {row['deals']} (отсеяно {row['screened']})")
    lines.append(f"Итого: показано {total['shown']}, ответов {total['replied']}, сделок {total['deals']}")
    speed = db.execute(f"""SELECT (julianday(d.sent_at)-julianday(e.received_at))*24*60 AS mins
             FROM deliveries d JOIN candidates c ON c.id=d.candidate_id JOIN events e ON e.id=c.event_id
             WHERE d.sent_at IS NOT NULL AND d.sent_at>=? AND e.source_id IN ({marks})
             ORDER BY mins""",
                       (since, *allowed)).fetchall()
    samples = [row["mins"] for row in speed if row["mins"] is not None]
    if samples:
        mid = len(samples) // 2
        median = samples[mid] if len(samples) % 2 else (samples[mid - 1] + samples[mid]) / 2
        lines.append(f"Медиана от публикации до карточки: {median:.0f} мин "
                     f"(выборка {len(samples)}, максимум {max(samples):.0f})")
    if ai_assist.configured():
        used = ai_assist.calls_this_month(db)
        lines.append(f"ИИ-вызовов в этом месяце: {used} из {ai_assist.monthly_limit()}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram demand monitor; network disabled unless run-live")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "run-live", "identify-owner", "status", "summary", "prune", "backup", "ai-worker", "report"):
        sub.add_parser(name)
    setp = sub.add_parser("set-status")
    setp.add_argument("candidate_id", type=int)
    setp.add_argument("status", choices=sorted(STATUSES))
    args = parser.parse_args()
    try:
        load_env()
        cfg = Config.from_env()
        db = connect(cfg.db)
        if args.command == "demo":
            demo(db, cfg)
        elif args.command == "run-live":
            with single_instance(cfg.db.with_suffix('.lock')):
                import runtime_status as health
                with health.lifecycle("monitor"):
                    # A terminated request must be reconciled, never blindly resent.
                    recover_interrupted(db)
                    run_live(db, cfg)
        elif args.command == "identify-owner":
            if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", cfg.token):
                raise ValueError("Для identify-owner нужен MONITOR_BOT_TOKEN")
            updates = Telegram(cfg.token).call("getUpdates", {"timeout": 0, "limit": 100, "allowed_updates": ["message"]})
            for update in updates:
                msg = update.get("message") or {}
                if msg.get("text", "").split("@", 1)[0] == "/start" and (msg.get("chat") or {}).get("type") == "private":
                    print(f"/start private user id: {(msg.get('from') or {}).get('id')}")
        elif args.command == "status":
            for row in db.execute("SELECT state,count(*) n FROM deliveries GROUP BY state"):
                print(f"delivery {row['state']}: {row['n']}")
            for row in db.execute("SELECT id,access_state,last_ok FROM sources"):
                print(f"source {row['id']}: {row['access_state']}, last event {row['last_ok'] or 'none'}")
        elif args.command == "summary":
            allowed = allowed_sources(cfg)
            marks = ",".join("?" for _ in allowed)
            for row in db.execute(f"SELECT c.priority,count(*) n FROM candidates c JOIN events e ON e.id=c.event_id "
                                  f"WHERE c.owner_status='new' AND e.source_id IN ({marks}) GROUP BY c.priority", allowed):
                print(f"new {row['priority']}: {row['n']}")
        elif args.command == "prune":
            print(f"Redacted fragments: {prune(db,now())}")
        elif args.command == "backup":
            target = cfg.db.with_name(cfg.db.stem + "-backup-" + now().strftime("%Y%m%dT%H%M%SZ") + ".sqlite3")
            with sqlite3.connect(target) as backup:
                db.backup(backup)
            print(target)
        elif args.command == "set-status":
            with db:
                cur = db.execute("UPDATE candidates SET owner_status=? WHERE id=?", (args.status, args.candidate_id))
            if not cur.rowcount:
                raise ValueError("Карточка не найдена")
            print("Статус сохранён")
        elif args.command == "ai-worker":
            print(ai_worker(db, cfg))
        elif args.command == "report":
            print(report(db, cfg))
        db.close()
    except (ValueError, RuntimeError, ApiError, UnknownDelivery) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
