import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor
from browser_ingest import BrowserIdentity, BrowserMutation, BrowserIntake, BrowserAccessError


class BrowserIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = monitor.Config(Path(self.temp.name)/'fixture.sqlite3', owner_id=42,
                                  client_sources=('approved_group',), client_peer_ids=(-100123,))
        self.db = monitor.connect(self.cfg.db)
        self.at = dt.datetime(2026, 11, 4, 12, tzinfo=monitor.UTC)
        self.proof = BrowserIdentity('expected_user', 'approved_group', -100123, True,
                                     'https://web.telegram.org/k/')
        self.reader = BrowserIntake(self.db, self.cfg, 'expected_user', 'approved_group', -100123)
        self.clock = patch('monitor.now', return_value=self.at)
        self.clock.start()

    def tearDown(self):
        self.clock.stop(); self.db.close(); self.temp.cleanup()

    def event(self, mid='101', body='Нужен лендинг', kind='added', published=None):
        return BrowserMutation(mid, body, published or self.at + dt.timedelta(seconds=1),
                               self.at + dt.timedelta(seconds=2), kind)

    def counts(self):
        return tuple(self.db.execute('select count(*) from '+t).fetchone()[0]
                     for t in ('events','candidates','deliveries'))

    def test_baseline_and_delayed_history_are_not_imported(self):
        self.reader.arm(self.proof, ['99','100'], self.at)
        self.assertIsNone(self.reader.accept(self.proof, self.event('99')))
        self.assertIsNone(self.reader.accept(self.proof, self.event('102', published=self.at-dt.timedelta(hours=1))))
        self.assertEqual(self.counts(), (0,0,0))

    def test_identity_mismatch_disarms_without_writing(self):
        self.reader.arm(self.proof, ['100'], self.at)
        wrong = BrowserIdentity('other_user', 'approved_group', -100123, True, self.proof.page_url)
        with self.assertRaises(BrowserAccessError): self.reader.accept(wrong, self.event())
        with self.assertRaises(BrowserAccessError): self.reader.accept(self.proof, self.event())
        self.assertEqual(self.counts(), (0,0,0))

    def test_wrong_peer_membership_or_url_never_arms(self):
        for peer, member, url in [(-100124,True,self.proof.page_url),(-100123,False,self.proof.page_url),
                                   (-100123,True,'https://example.com/k/')]:
            proof = BrowserIdentity('expected_user','approved_group',peer,member,url)
            with self.assertRaises(BrowserAccessError): self.reader.arm(proof,['100'],self.at)
        with self.assertRaises(BrowserAccessError): self.reader.arm(self.proof,[],self.at)
        self.assertEqual(self.counts(), (0,0,0))

    def test_malformed_identity_also_disarms(self):
        self.reader.arm(self.proof,['100'],self.at)
        malformed = BrowserIdentity('', 'approved_group', -100123, True, self.proof.page_url)
        with self.assertRaises(BrowserAccessError):self.reader.accept(malformed,self.event())
        with self.assertRaises(BrowserAccessError):self.reader.accept(self.proof,self.event())
        self.assertEqual(self.counts(),(0,0,0))

    def test_new_original_has_canonical_link_and_dedups(self):
        self.reader.arm(self.proof, ['100'], self.at)
        first = self.reader.accept(self.proof, self.event())
        again = self.reader.accept(self.proof, self.event())
        self.assertEqual(first, again); self.assertEqual(self.counts(), (1,1,1))
        row = self.db.execute('select source_id,link,author,published_at from events').fetchone()
        self.assertEqual(row['source_id'],'client:-100123')
        self.assertEqual(row['link'],'https://t.me/approved_group/101')
        self.assertIsNone(row['author'])
        self.assertEqual(row['published_at'],monitor.iso(self.event().published_at))

    def test_edit_keeps_owner_delivery_and_does_not_import_unknown_old_row(self):
        self.reader.arm(self.proof,['100'],self.at)
        candidate = self.reader.accept(self.proof,self.event())
        with self.db:
            self.db.execute("update candidates set owner_status='taken' where id=?",(candidate,))
            self.db.execute("update deliveries set state='uncertain',attempts=1,message_id=91")
        self.assertIsNone(self.reader.accept(self.proof,self.event('98',kind='edited')))
        self.reader.accept(self.proof,self.event(body='Нужен лендинг, не в штат',kind='edited'))
        row=self.db.execute('select owner_status from candidates').fetchone()
        self.assertEqual(row['owner_status'],'taken')
        delivery=self.db.execute('select state,attempts,message_id,recipient from deliveries').fetchone()
        self.assertEqual(tuple(delivery),('uncertain',1,91,42)); self.assertEqual(self.counts(),(1,1,1))

    def test_reconnect_requires_new_baseline_and_preserves_database(self):
        self.reader.arm(self.proof,['100'],self.at);self.reader.accept(self.proof,self.event())
        other=BrowserIntake(self.db,self.cfg,'expected_user','approved_group',-100123)
        with self.assertRaises(BrowserAccessError):other.accept(self.proof,self.event())
        other.arm(self.proof,['101','105'],self.at+dt.timedelta(minutes=1))
        self.assertIsNone(other.accept(self.proof,BrowserMutation('104','Нужен лендинг',self.at+dt.timedelta(seconds=50),self.at+dt.timedelta(minutes=2))))
        self.assertEqual(self.counts(),(1,1,1))

    def test_original_id_and_timezone_are_required(self):
        self.reader.arm(self.proof,['100'],self.at)
        with self.assertRaises(BrowserAccessError):self.reader.accept(self.proof,self.event('generated-id'))
        with self.assertRaises(BrowserAccessError):self.reader.accept(self.proof,self.event(published=dt.datetime(2026,11,4)))
        self.assertEqual(self.counts(),(0,0,0))

    def test_synthetic_browser_to_filter_to_notification_and_no_replay(self):
        class FakeSink:
            def __init__(self):self.calls=[]
            def call(sink,method,payload,timeout=20):
                sink.calls.append((method,payload))
                return {'message_id':91,'date':int((self.at+dt.timedelta(seconds=3)).timestamp())}
        sink=FakeSink();self.reader.arm(self.proof,['100'],self.at)
        cid=self.reader.accept(self.proof,self.event())
        row=self.db.execute('select kind,priority from candidates where id=?',(cid,)).fetchone()
        self.assertEqual(tuple(row),('demand','urgent'))
        self.assertEqual(monitor.deliver(self.db,self.cfg,sink,self.at+dt.timedelta(seconds=2)),1)
        self.assertEqual(sink.calls[0][0],'sendMessage');self.assertEqual(sink.calls[0][1]['chat_id'],42)
        self.reader.accept(self.proof,self.event())
        self.assertEqual(monitor.deliver(self.db,self.cfg,sink,self.at+dt.timedelta(seconds=4)),0)
        self.assertEqual(len(sink.calls),1);self.assertEqual(self.counts(),(1,1,1))
        self.assertEqual(self.db.execute('select state,attempts,message_id from deliveries').fetchone()[:],('sent',1,91))


if __name__ == '__main__': unittest.main()
