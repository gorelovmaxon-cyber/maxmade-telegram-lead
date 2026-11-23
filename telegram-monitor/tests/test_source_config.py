import contextlib
import json
import tempfile
import unittest
from pathlib import Path
import monitor
import source_registry as registry
import source_config as settings

class SourceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'sources.sqlite'
        self.db=registry.connect(self.path);registry.initialize(self.db)
        with self.db:
            self.db.execute("INSERT INTO stage_acceptance VALUES(5,'fixture','{}')")
            for index,name in enumerate(('top_ads_freelance','freelead','biznes_chat','odp2021')):
                self.db.execute("UPDATE sources SET enabled=1,access='joined',type='group',peer_id=?,rules_state='reviewed',read_permission='local_client' WHERE username=?",(-1000000000123-index,name))
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_select_three_preserves_disabled_records_and_restart(self):
        count=self.db.execute('SELECT count(*) FROM sources').fetchone()[0]
        names=('top_ads_freelance','freelead','biznes_chat')
        settings.select_only(self.db,names);settings.set_limit(self.db,3)
        self.db.close();self.db=registry.connect(self.path)
        self.assertEqual({r['username'] for r in registry.enabled(self.db)},set(names))
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0],count)
        old=self.db.execute("SELECT access,peer_id FROM sources WHERE username='odp2021'").fetchone()
        self.assertEqual(tuple(old),('joined',-1000000000126))
        self.assertEqual(self.db.execute("SELECT reason FROM gaps WHERE username='odp2021'").fetchone()[0],'operator_disabled_source')
    def test_cannot_enable_unknown_access_or_exceed_limit(self):
        settings.select_only(self.db,('top_ads_freelance','freelead','biznes_chat'));settings.set_limit(self.db,3)
        with self.assertRaises(ValueError):settings.enable(self.db,'odp2021',True)
        self.assertEqual(self.db.execute("SELECT enabled FROM sources WHERE username='odp2021'").fetchone()[0],0)
        with self.assertRaises(ValueError):settings.enable(self.db,'chat_frilans',True)
        self.assertEqual(len(registry.enabled(self.db)),3)
    def test_new_record_is_disabled_review_and_toggle_need_no_filter_change(self):
        settings.select_only(self.db,('top_ads_freelance','freelead'));settings.set_limit(self.db,3)
        settings.add(self.db,'fixture_group',-1000000000765,params={'navigation_timeout_ms':18000,'dom_peer_id':'-765'})
        self.assertEqual(self.db.execute("SELECT enabled FROM sources WHERE username='fixture_group'").fetchone()[0],0)
        proof={'at':monitor.iso(monitor.now()),'groups':[{'username':'fixture_group','peer_id':-1000000000765,'account_verified':True,
          'source_verified':True,'membership':'joined','message_metadata_accessible':True,'stage':'complete'}]}
        settings.review(self.db,'fixture_group',proof,'Fixture rules reviewed','explicit_request_only')
        settings.enable(self.db,'fixture_group',True)
        unchanged={r['username']:dict(r) for r in registry.enabled(self.db) if r['username']!='fixture_group'}
        settings.enable(self.db,'fixture_group',False);self.db.close();self.db=registry.connect(self.path)
        self.assertEqual({r['username']:dict(r) for r in registry.enabled(self.db)},unchanged)
        params=json.loads(self.db.execute("SELECT adapter_parameters FROM sources WHERE username='fixture_group'").fetchone()[0])
        self.assertEqual(params['navigation_timeout_ms'],18000)
    def test_bad_adapter_and_review_do_not_authorize_access(self):
        with self.assertRaises(ValueError):settings.add(self.db,'fixture_group',-1000000000765,params={'dom_peer_id':'-999'})
        with self.assertRaises(ValueError):settings.add(self.db,'fixture_group',-1000000000765,params={'evaluate':'unsafe JS'})
        settings.add(self.db,'fixture_group',-1000000000765)
        with self.assertRaises(ValueError):settings.review(self.db,'fixture_group',{'groups':[]},'rules','route')
        self.assertEqual(self.db.execute("SELECT access FROM sources WHERE username='fixture_group'").fetchone()[0],'unknown')
    def test_selection_failure_is_atomic(self):
        before=[tuple(r) for r in self.db.execute('SELECT username,enabled FROM sources ORDER BY username')]
        with self.assertRaises(ValueError):settings.select_only(self.db,('top_ads_freelance','unknown_group'))
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT username,enabled FROM sources ORDER BY username')],before)
