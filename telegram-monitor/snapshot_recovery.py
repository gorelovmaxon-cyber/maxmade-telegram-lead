"""Consistent, ACL-protected recovery snapshot; no credential output."""
import contextlib
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import automation
import local_chrome
import monitor


def snapshot():
    if local_chrome.host_running() or automation.process_running():
        raise RuntimeError('Stop dedicated runtime before cold profile backup')
    cfg=automation.config()
    destination=automation.ROOT/'state'/('recovery-'+monitor.now().strftime('%Y%m%dT%H%M%SZ'))
    local_chrome.private_directory(destination)
    with contextlib.closing(sqlite3.connect(cfg.db.as_uri()+'?mode=ro',uri=True)) as db:
        with contextlib.closing(sqlite3.connect(destination/'monitor.sqlite3')) as copy:
            db.backup(copy)
            if copy.execute('pragma integrity_check').fetchone()[0]!='ok':raise RuntimeError('Integrity failure')
    shutil.copy2(automation.ROOT/'.env',destination/'settings.private')
    shutil.copytree(local_chrome.PROFILE,destination/'profile')
    if automation.JOURNAL.exists():
        with contextlib.closing(sqlite3.connect(automation.JOURNAL)) as db:
            with contextlib.closing(sqlite3.connect(destination/'operations.sqlite3')) as copy:db.backup(copy)
    files=list((destination/'profile').rglob('*'))
    receipt={'at':monitor.iso(monitor.now()),'database_integrity':'ok','cold_profile':True,
             'profile_files':sum(p.is_file() for p in files),'profile_bytes':sum(p.stat().st_size for p in files if p.is_file()),
             'env_sha256':hashlib.sha256((destination/'settings.private').read_bytes()).hexdigest()}
    automation.write_json(destination/'receipt.json',receipt)
    print(json.dumps({'snapshot':str(destination),'integrity':'ok','profile_files':receipt['profile_files']}))
    return destination


if __name__=='__main__':snapshot()
