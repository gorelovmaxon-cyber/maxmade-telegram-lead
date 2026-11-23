import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import filter_policy as f
import monitor as m

class Sink:
    def __init__(self,error=None):self.calls=[];self.error=error
    def call(self,method,payload,**kwargs):
        self.calls.append((method,payload))
        if self.error:raise self.error
        return {'message_id':100+len(self.calls)}

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite'
        self.db=m.connect(self.path);self.cfg=m.Config(self.path,owner_id=42,source_ids=(-1,-2),quiet_start=0,quiet_end=0)
        self.at=dt.datetime(2026,11,7,12,tzinfo=m.UTC)
        self.clock=patch('monitor.now',return_value=self.at);self.clock.start()
    def tearDown(self):self.clock.stop();self.db.close();self.tmp.cleanup()
    def add(self,text,mid='1',source='-1',version='1',at=None):
        t=at or self.at
        return m.ingest(self.db,self.cfg,source,mid,text,t,t,edit_version=version,link='https://t.me/example/'+mid)
    def activate(self,version=f.V3):return f.activate(self.db,version,m.iso(self.at))
    def test_default_v2_and_protected_legacy_edit(self):
        cid=self.add('У нас есть сайт')
        before=tuple(self.db.execute('SELECT * FROM candidates').fetchone());delivery=tuple(self.db.execute('SELECT * FROM deliveries').fetchone())
        self.activate();self.add('Создаю сайты',version='2')
        self.assertEqual(tuple(self.db.execute('SELECT * FROM candidates').fetchone()),before)
        self.assertEqual(tuple(self.db.execute('SELECT * FROM deliveries').fetchone()),delivery)
        self.assertEqual(self.db.execute('SELECT count(*) FROM filter_decisions').fetchone()[0],0)
        self.activate(f.V2);self.add('Нужен сайт',version='3')
        self.assertEqual(tuple(self.db.execute('SELECT * FROM candidates').fetchone()),before)
    def test_routes_no_budget_no_contact_and_manual_status(self):
        self.activate()
        cases=[('Нужен сайт','main'),('Ищем веб-дизайнера на постоянное сотрудничество','partnership'),
          ('Ищем веб-дизайнера в штат, оклад','employment'),('Кто занимается сайтами?','review'),('Создаю сайты','none')]
        for i,(text,route) in enumerate(cases):
            cid=self.add(text,str(i));self.assertEqual(f.latest(self.db,cid)['route'],route)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],2)
        self.assertEqual(len(f.queue_rows(self.db,'partnership')),1)
        with self.db:self.db.execute("UPDATE candidates SET owner_status='taken' WHERE id=1")
        self.add('Нужен сайт на Laravel','0',version='2')
        self.assertEqual(self.db.execute('SELECT owner_status FROM candidates WHERE id=1').fetchone()[0],'taken')
        self.assertEqual(m.deliver(self.db,self.cfg,Sink(),self.at),1)
    def test_same_event_replay_and_cosmetic_edit(self):
        self.activate();cid=self.add('Нужен сайт');self.add('Нужен сайт')
        self.assertEqual(self.db.execute('SELECT count(*) FROM filter_decisions').fetchone()[0],1)
        sink=Sink();m.deliver(self.db,self.cfg,sink,self.at)
        self.add('НУЖЕН сайт! ',version='2')
        self.assertEqual(f.latest(self.db,cid)['change_kind'],'cosmetic_edit')
        self.assertEqual(self.db.execute('SELECT count(*) FROM filter_updates').fetchone()[0],0)
        self.assertEqual(len(sink.calls),1)
    def test_generic_jobs_not_merged(self):
        self.activate()
        for i in range(3):self.add('Нужен сайт',str(i),source='-1' if i<2 else '-2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM filter_decisions WHERE duplicate_of IS NOT NULL').fetchone()[0],0)
    def test_confident_repost_cross_source_window_and_material_change(self):
        self.activate()
        body='Нужен сайт для проекта https://example.org/project-alpha. Бюджет 50000. Техническое задание готово. '+('Описание конкретного проекта. '*6)
        a=self.add(body);b=self.add(body,'2');c=self.add(body,'3',source='-2')
        self.assertEqual(f.latest(self.db,b)['change_kind'],'repost')
        self.assertEqual(f.latest(self.db,c)['change_kind'],'cross_source_repost')
        self.assertEqual(f.latest(self.db,c)['duplicate_of'],a)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
        self.add(body.replace('50000','70000'),'4',source='-2')
        late=self.at+dt.timedelta(hours=49)
        with patch('monitor.now',return_value=late):self.add(body,'5',at=late)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],5)
    def test_long_template_same_contact_different_jobs_not_merged(self):
        self.activate();body='Нужен сайт. '+('Стандартные требования к исполнителю. '*7)+'Пишите @ExampleContact'
        self.add(body);self.add(body,'2',source='-2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],2)
        body+=' Портфолио агентства https://example.org, бюджет 50000'
        self.add(body,'3');self.add(body,'4',source='-2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],4)
    def test_material_revision_append_only_and_uncertain(self):
        self.activate();cid=self.add('Нужен сайт');m.deliver(self.db,self.cfg,Sink(),self.at)
        sent=tuple(self.db.execute('SELECT * FROM deliveries').fetchone())
        self.add('Нужен сайт на Tilda, бюджет 70000',version='2')
        self.assertEqual(f.latest(self.db,cid)['change_kind'],'material_edit')
        self.assertEqual(m.deliver_revisions(self.db,self.cfg,Sink(m.UnknownDelivery()),self.at,('-1',)),0)
        self.assertEqual(self.db.execute('SELECT state FROM filter_updates').fetchone()[0],'uncertain')
        retry=Sink();m.deliver_revisions(self.db,self.cfg,retry,self.at,('-1',));self.assertEqual(retry.calls,[])
        self.assertEqual(tuple(self.db.execute('SELECT * FROM deliveries').fetchone()),sent)
        self.add('Задача закрыта, исполнитель найден',version='3')
        sink=Sink();self.assertEqual(m.deliver_revisions(self.db,self.cfg,sink,self.at,('-1',)),1)
        self.assertIn('Изменение запроса',sink.calls[0][1]['text'])
        self.assertEqual(tuple(self.db.execute('SELECT * FROM deliveries').fetchone()),sent)
    def test_rollback_new_events_only_persists_after_restart(self):
        self.activate();a=self.add('Кто занимается сайтами?');self.activate(f.V2)
        b=self.add('Кто занимается сайтами?','2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
        self.assertIsNotNone(f.latest(self.db,a));self.assertIsNone(f.latest(self.db,b))
        self.db.close();self.db=m.connect(self.path)
        self.assertEqual(f.get_meta(self.db,'filter_active_version'),f.V2)
    def test_no_strong_identity_no_duplicate_and_window_configurable(self):
        self.activate();text='Нужен сайт. '+('Требования конкретного проекта. '*8)
        self.add(text);self.add(text,'2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],2)
        self.assertIsNone(f.duplicate(self.db,text,None,self.at,{'minimum_duplicate_characters':1,'duplicate_window_hours':0},'main'))
    def test_interrupted_revision_not_retried(self):
        self.activate();a=self.add('Нужен сайт');m.deliver(self.db,self.cfg,Sink(),self.at)
        self.add('Нужен лендинг на Tilda',version='2')
        with self.db:self.db.execute("UPDATE filter_updates SET state='sending'")
        m.recover_interrupted(self.db)
        self.assertEqual(self.db.execute('SELECT state FROM filter_updates').fetchone()[0],'uncertain')
