"""Versioned selection journal. Append-only evidence; no network or old-card replay."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
import sqlite3
import unicodedata

V2 = 'maxmadepro-2026-10-30-v2'
PREVIOUS_V3 = 'maxmadepro-2026-11-09-v3.2'
V3 = 'maxmadepro-2026-11-09-v3.3'

def is_v3(version):return version in (PREVIOUS_V3,V3,'maxmadepro-2026-11-07-v3.1','maxmadepro-2026-11-07-v3')


def ensure_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS filter_decisions(
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES candidates(id),
      version TEXT NOT NULL, decided_at TEXT NOT NULL, edit_version TEXT NOT NULL,
      route TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL,
      assessment TEXT NOT NULL, duplicate_of INTEGER, change_kind TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS filter_candidate ON filter_decisions(candidate_id,id);
    CREATE TABLE IF NOT EXISTS publication_versions(
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES candidates(id),
      seen_at TEXT NOT NULL, edit_version TEXT NOT NULL, body TEXT NOT NULL, link TEXT,
      content_hash TEXT NOT NULL, identity_hash TEXT NOT NULL, source_id TEXT NOT NULL,
      original_main INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS publication_match ON publication_versions(content_hash,identity_hash,seen_at);
    CREATE TABLE IF NOT EXISTS filter_updates(
      id INTEGER PRIMARY KEY, decision_id INTEGER UNIQUE NOT NULL REFERENCES filter_decisions(id),
      candidate_id INTEGER NOT NULL REFERENCES candidates(id), recipient INTEGER NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
      next_at TEXT NOT NULL, sent_at TEXT, message_id INTEGER, error_code TEXT);
    ''')


def get_meta(db,key,default=''):
    row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    return row[0] if row else default


def activate(db,version,at):
    if version not in (V2,V3):raise ValueError('Unknown selection version')
    ensure_schema(db)
    cutoff=db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
    # A single immutable cutoff also protects older cards across rollback/re-enable.
    with db:
        for key,value in [('filter_active_version',version),('filter_activated_at',at)]:
            db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
        db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('filter_initial_cutoff',str(cutoff)))
        if version==V3:
            import selection_engine,order_profile
            for key,value in [('filter_active_rules_version',selection_engine.RULES_VERSION),('active_business_profile_version',order_profile.profile()['version'])]:
                db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
        else:
            db.execute("INSERT INTO meta VALUES('filter_active_rules_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",('legacy-v2',))
            db.execute("INSERT INTO meta VALUES('active_business_profile_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(V2,))
    return {'version':version,'initial_cutoff':int(get_meta(db,'filter_initial_cutoff')),'at':at}


def normalized(body):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',body).lower().replace('\u200b','')).strip()


def fingerprint(body,author=''):
    text=normalized(body)
    identities=set(re.findall(r'@[a-z0-9_]{5,32}\b|https?://[^\s<>]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>]*)?',text))
    if re.fullmatch(r'@[a-zA-Z0-9_]{5,32}|(?:user:)?\d{5,}',author or ''):identities.add(author.lower())
    digest=lambda value:hashlib.sha256(value.encode()).hexdigest()
    return digest(text),digest('\n'.join(sorted(identities))) if identities else ''


def duplicate(db,body,author,received,settings,route,exclude=None):
    content,identity=fingerprint(body,author)
    if len(normalized(body))<settings.get('minimum_duplicate_characters',160) or not identity:return None
    # Main-job templates need a concrete distinguishing specification as well.
    # Contacts alone cannot distinguish two jobs commissioned by one agency.
    if route=='main' and not (re.search(r'(?:для\s+проекта|текущий\s+сайт|сайт\s+проекта|редизайн|переделать|доработать|ТЗ)\s*[:—-]?\s*https?://',body,re.I)
                             and re.search(r'\d{2,}|ТЗ|техническ\w*\s+задани',body,re.I)):
        return None
    since=(received-dt.timedelta(hours=settings.get('duplicate_window_hours',48))).isoformat(timespec='seconds')
    return db.execute('''SELECT candidate_id,source_id,original_main FROM publication_versions
      WHERE content_hash=? AND identity_hash=? AND seen_at>=? AND seen_at<=?
      AND candidate_id<>? ORDER BY id LIMIT 1''',(content,identity,since,received.isoformat(timespec='seconds'),exclude or -1)).fetchone()


def remember(db,cid,body,author,link,source_id,edit_version,at,main):
    content,identity=fingerprint(body,author)
    db.execute('''INSERT INTO publication_versions(candidate_id,seen_at,edit_version,body,link,content_hash,identity_hash,source_id,original_main)
      VALUES(?,?,?,?,?,?,?,?,?)''',(cid,at,edit_version,body,link,content,identity,source_id,int(main)))


def materially_changed(before,after):
    # Whitespace/case/punctuation-only redraws cannot create update notifications.
    words=lambda text:re.findall(r'\w+|[@/:%+-]',normalized(text))
    return words(before)!=words(after)


def latest(db,cid):
    return db.execute('SELECT * FROM filter_decisions WHERE candidate_id=? ORDER BY id DESC LIMIT 1',(cid,)).fetchone()


def queue_rows(db,route,limit=100):
    if route not in ('partnership','employment','review'):raise ValueError('Unknown queue')
    return db.execute('''SELECT f.candidate_id,f.version,f.kind,f.reason,f.decided_at,c.owner_status,e.link,s.name AS source_name
      FROM filter_decisions f JOIN candidates c ON c.id=f.candidate_id JOIN events e ON e.id=c.event_id
      JOIN sources s ON s.id=e.source_id WHERE f.id=(SELECT MAX(x.id) FROM filter_decisions x WHERE x.candidate_id=f.candidate_id)
      AND f.route=? ORDER BY f.id DESC LIMIT ?''',(route,limit)).fetchall()


def record(db,cid,assessment,at,edit_version,duplicate_of,change_kind):
    cur=db.execute('''INSERT INTO filter_decisions(candidate_id,version,decided_at,edit_version,route,kind,reason,assessment,duplicate_of,change_kind)
      VALUES(?,?,?,?,?,?,?,?,?,?)''',(cid,assessment['version'],at,edit_version,assessment['notification_route'],assessment['kind'],
      assessment['reason'],json.dumps(assessment,ensure_ascii=False),duplicate_of,change_kind))
    return cur.lastrowid
