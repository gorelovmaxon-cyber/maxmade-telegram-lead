"""Fail-closed bridge for a future verified browser capture, with no network or sender.

This is the intake boundary, not a Telegram DOM reader. The capture controller must
verify visible account/source identity, establish a baseline, then supply mutations.
It must never feed initial history or use an agent/LLM to extract group messages.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import re
import sqlite3
from urllib.parse import urlsplit

import monitor
import browser_context


class BrowserAccessError(ValueError):
    """Verified capture context is missing, changed, or no longer authorized."""


@dataclass(frozen=True)
class BrowserIdentity:
    account_username: str
    source_username: str
    peer_id: int
    member: bool
    page_url: str


@dataclass(frozen=True)
class BrowserMutation:
    message_id: str
    body: str
    published_at: dt.datetime
    observed_at: dt.datetime
    kind: str = 'added'
    context: dict | None = None


def utc(value: dt.datetime) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise BrowserAccessError('Explicit timezone required')
    return value.astimezone(monitor.UTC)


def username(value: str) -> str:
    value = value.lower().lstrip('@')
    if not re.fullmatch(r'[a-z0-9_]{5,32}', value):
        raise BrowserAccessError('Invalid expected identity')
    return value


def message_number(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,15}', value):
        raise BrowserAccessError('Original positive numeric message ID required')
    return int(value)


class BrowserIntake:
    def __init__(self, db: sqlite3.Connection, cfg: monitor.Config, expected_account: str,
                 source_username: str, peer_id: int):
        self.db = db
        self.cfg = cfg
        self.account = username(expected_account)
        self.source = username(source_username)
        if not isinstance(peer_id, int) or peer_id >= 0:
            raise BrowserAccessError('Pinned peer required')
        allowed = dict(zip(cfg.client_sources, cfg.client_peer_ids))
        if len(cfg.client_sources) != len(cfg.client_peer_ids) or allowed.get(self.source) != peer_id:
            raise BrowserAccessError('Source is outside the existing allowlist')
        self.peer_id = peer_id
        # Reuse the old adapter's source key: changing capture must not fork dedup.
        self.source_id = 'client:' + str(peer_id)
        self.started_at: dt.datetime | None = None
        self.baseline = 0
        browser_context.ensure_schema(self.db)

    def pause(self) -> None:
        self.started_at = None

    def verify(self, proof: BrowserIdentity) -> None:
        try:
            url = urlsplit(proof.page_url)
            valid = (url.scheme == 'https' and url.hostname == 'web.telegram.org'
                     and not url.username and not url.password and url.port in (None, 443)
                     and url.path.startswith('/k/')
                     and username(proof.account_username) == self.account
                     and username(proof.source_username) == self.source
                     and proof.peer_id == self.peer_id and proof.member is True)
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            self.pause()
            raise BrowserAccessError('Account/source/access mismatch; capture disarmed')

    def arm(self, proof: BrowserIdentity, visible_message_ids: list[str], at: dt.datetime) -> None:
        # Called for each actual page attachment/reconnect. Initial rows are IDs only,
        # not event bodies. An empty/unverified baseline must never import history.
        self.pause()
        self.verify(proof)
        if not visible_message_ids:
            raise BrowserAccessError('No visible baseline; capture not armed')
        baseline = max(message_number(value) for value in visible_message_ids)
        self.started_at = utc(at)
        self.baseline = baseline

    def accept(self, proof: BrowserIdentity, event: BrowserMutation) -> int | None:
        self.verify(proof)
        if self.started_at is None:
            raise BrowserAccessError('Capture must be armed before ingest')
        number = message_number(event.message_id)
        published, observed = utc(event.published_at), utc(event.observed_at)
        if observed < self.started_at or published > observed + dt.timedelta(seconds=30):
            raise BrowserAccessError('Unreliable event time')
        if event.kind not in ('added', 'edited'):
            raise BrowserAccessError('Only observed add/edit mutations accepted')
        if not isinstance(event.body, str) or not event.body.strip():
            return None
        existing = self.db.execute(
            'SELECT published_at FROM events WHERE source_id=? AND message_id=?',
            (self.source_id, event.message_id),
        ).fetchone()
        if event.kind == 'added':
            if number <= self.baseline or published < self.started_at:
                return None
        else:
            # Edits may update only an already ingested original, never import an
            # arbitrary older row. Original publication time is immutable.
            if existing is None:
                return None
            original = dt.datetime.fromisoformat(existing['published_at'])
            if abs((published - original).total_seconds()) >= 1:
                raise BrowserAccessError('Edit changed original publication time')
            published = original
        version = 'browser:' + hashlib.sha256(event.body.encode('utf-8')).hexdigest()
        candidate = monitor.ingest(
            self.db, self.cfg, self.source_id, event.message_id, event.body,
            published, observed, edit_version=version,
            link=f'https://t.me/{self.source}/{event.message_id}',
            author=None, source_name='@' + self.source, method='browser_cdp',
        )
        if candidate is not None:
            browser_context.record(self.db,candidate,version,self.source_id,event.message_id,event.context,monitor.iso(monitor.now()))
        return candidate
