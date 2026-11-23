import json,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
import monitor as m,order_profile as p,filter_policy as f
import test_filter_policy as policy_tests
from test_filter_policy import Sink

UNKNOWN_REQUESTS=(
 'Нужна помощь с проектом,4.000',
 'Нужна помощь с проектом',
 'Нужна помощь',
 'Нужен исполнитель, подробности обсуждаем',
 'Ищу специалиста для задачи, бюджет обсудим',
 'Помогите с проектом, пожалуйста',
 'Кто поможет с задачей?',
)

class AcceptanceMeaningTests(unittest.TestCase):
 def test_request_without_subject_is_review_never_an_offer(self):
  for body in UNKNOWN_REQUESTS:
   for text in (body,body.upper(),body.replace(' ','\n'),'👋 '+body+'!'):
    with self.subTest(text=text):
     a=p.assess(text);self.assertEqual(a['notification_route'],'review')
     self.assertIs(a['task_exists'],True);self.assertEqual(a['service_fit'],'unknown')
     self.assertEqual(a['services'],[]);self.assertIn('task.scope_unknown',a['rule_ids'])
     self.assertIn('предмет работ неизвестен',a['reason']);self.assertEqual(a['draft'],'')
     for e in a['evidence']:self.assertEqual(text[e['start']:e['end']],e['quote'])
 def test_explanations_distinguish_absent_unknown_outside_and_direction(self):
  absent=p.assess('Всем добрый вечер');unknown=p.assess(UNKNOWN_REQUESTS[0])
  outside=p.assess('Нужен бухгалтер');direction=p.assess('Давайте обмениваться клиентами')
  self.assertIn('Нет запроса',absent['reason']);self.assertIs(absent['task_exists'],False)
  self.assertIn('предмет работ неизвестен',unknown['reason']);self.assertIs(unknown['task_exists'],True)
  self.assertIn('услуга вне профиля',outside['reason']);self.assertEqual(outside['service_fit'],'outside')
  self.assertIn('Неизвестно направление',direction['reason']);self.assertEqual(direction['work_direction'],'unknown')
  self.assertEqual(len({a['reason'] for a in (absent,unknown,outside,direction)}),4)
 def test_offer_cancellation_and_known_outside_do_not_become_uncertain_requests(self):
  for body in ('Если вам нужна помощь с проектом — пишите, помогу','Я помогу с проектом','Помощь с проектом больше не нужна','Нужна помощь бухгалтера','Нужен трафик на готовый лендинг',
   'Нужны помощники. Нужно будет перенести мебель и коробки',
   'Нужны помощники. Нужно перенести мебель. Оплата на человека, подробности в личке',
   'Если вашим детям нужна помощь с учебой или экзаменами — пишите, помогу',
   'Нужен человек на удаленную подработку с телефона. Задачи простые. Нужно 2 человека, кто готов начать сегодня'):
   with self.subTest(body=body):self.assertEqual(p.assess(body)['notification_route'],'none')
 def test_known_web_subject_still_delivers_and_direction_remains_explicit(self):
  self.assertEqual(p.assess('Нужна помощь с проектом: нужно переделать сайт')['notification_route'],'main')
  a=p.assess('Нужен партнёр, который возьмёт разработку, продажи наши')
  self.assertEqual(a['notification_route'],'review');self.assertEqual(a['work_direction'],'work_to_us')
 def test_v33_baseline_subcontract_was_an_automatic_notification(self):
  import quality_evaluation
  text='Агентство ищет Tilda-разработчика на поток проектов'
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   (root/'sample.json').write_text(json.dumps([{'id':1,'source_id':'-1','split':'holdout','body':text,'old_assessment':p.assess(text)}]),encoding='utf-8')
   (root/'holdout-labels.json').write_text(json.dumps([{'id':1,'route':'partnership','reason':'Incoming pool'}]),encoding='utf-8')
   stats=quality_evaluation.evaluate(root,'holdout')['stats']['old']
   self.assertEqual(stats['correct_subcontract'],1);self.assertEqual(stats['missed_auto'],0)

class AcceptanceQueueTests(unittest.TestCase):
 setUp=policy_tests.PolicyTests.setUp
 tearDown=policy_tests.PolicyTests.tearDown
 add=policy_tests.PolicyTests.add
 activate=policy_tests.PolicyTests.activate
 def test_unknown_request_is_review_with_no_delivery_digest_or_duplicate(self):
  self.activate();cid=self.add(UNKNOWN_REQUESTS[0]);self.add(UNKNOWN_REQUESTS[0])
  self.assertEqual(f.latest(self.db,cid)['route'],'review')
  self.assertEqual(len(f.queue_rows(self.db,'review')),1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM filter_decisions').fetchone()[0],1)
  sink=Sink();self.assertEqual(m.deliver(self.db,self.cfg,sink,self.at),0);self.assertEqual(sink.calls,[])
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
 def test_existing_candidate_and_sent_history_untouched_by_switch(self):
  self.activate();cid=self.add('Нужен сайт','historical');sink=Sink();m.deliver(self.db,self.cfg,sink,self.at)
  with self.db:self.db.execute("UPDATE candidates SET owner_status='taken' WHERE id=?",(cid,))
  candidate=tuple(self.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone())
  sent=tuple(self.db.execute('SELECT * FROM deliveries').fetchone())
  self.activate();self.add(UNKNOWN_REQUESTS[0],'new')
  self.assertEqual(tuple(self.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone()),candidate)
  self.assertEqual(tuple(self.db.execute('SELECT * FROM deliveries').fetchone()),sent)
  self.assertEqual(m.deliver(self.db,self.cfg,sink,self.at),0)
 def test_historical_unknown_request_enters_review_by_append_only_audit(self):
  self.activate();body=UNKNOWN_REQUESTS[0];after=p.assess(body)
  before=dict(after,notification_route='none',kind='other',reason='Prior no-request result')
  with patch('monitor.order_profile.assess',return_value=before):cid=self.add(body,'historical-504')
  with self.db:self.db.execute("UPDATE candidates SET owner_status='ignored' WHERE id=?",(cid,))
  candidate=tuple(self.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone())
  old_decision=tuple(self.db.execute('SELECT * FROM filter_decisions').fetchone())
  with self.db:f.record(self.db,cid,after,m.iso(self.at),'1',None,'acceptance_review_correction')
  self.assertEqual(tuple(self.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone()),candidate)
  self.assertEqual(tuple(self.db.execute('SELECT * FROM filter_decisions ORDER BY id LIMIT 1').fetchone()),old_decision)
  self.assertEqual(f.latest(self.db,cid)['route'],'review')
  self.assertEqual([r['candidate_id'] for r in f.queue_rows(self.db,'review')],[cid])
  sink=Sink();self.assertEqual(m.deliver(self.db,self.cfg,sink,self.at),0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
