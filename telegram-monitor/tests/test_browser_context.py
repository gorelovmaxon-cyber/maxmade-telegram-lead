import asyncio,datetime as dt,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
import browser_context as ctx,browser_ingest as ingest,filter_policy as policy,monitor
import test_browser_capture as fixture
from test_filter_policy import Sink

@unittest.skipUnless(fixture.PLAYWRIGHT,'Playwright required')
class MessageContextBrowserTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixture.BrowserCaptureTests.asyncSetUp
 asyncTearDown=fixture.BrowserCaptureTests.asyncTearDown
 add=fixture.BrowserCaptureTests.add
 settle=fixture.BrowserCaptureTests.settle
 async def reply(self,mid,text,target='4294967397',peer='-123'):
  await self.page.evaluate('''v=>{
   const b=document.createElement('div');b.className='bubble is-reply';b.dataset.mid=String(4294967296+v.mid);b.dataset.peerId='-123';b.dataset.timestamp=String(Math.ceil(Date.now()/1000));
   if(v.target!==null)b.dataset.replyToMid=v.target;if(v.peer!==null)b.dataset.replyToPeerId=v.peer;
   const message=document.createElement('div');message.className='message';
   const quote=document.createElement('div');quote.className='reply';quote.textContent='Нужен сайт, бюджет 500000, пишите выдуманному автору';
   message.append(quote,document.createTextNode(v.text));b.append(message);document.querySelector('#messages').append(b);
  }''',dict(mid=mid,text=text,target=target,peer=peer))
 def latest_context(self,cid):return json.loads(self.db.execute('SELECT evidence FROM browser_message_context WHERE candidate_id=? ORDER BY id DESC',(cid,)).fetchone()[0])
 async def test_reply_links_saved_same_source_parent_without_closing_or_inheriting(self):
  policy.activate(self.db,policy.V3,monitor.iso(monitor.now()));await self.capture.arm();await self.add();await self.settle()
  parent=(await self.capture.drain())[0];sink=Sink();self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),1)
  with self.db:self.db.execute("UPDATE candidates SET owner_status='taken' WHERE id=?",(parent,))
  saved=tuple(self.db.execute('SELECT * FROM candidates WHERE id=?',(parent,)).fetchone());sent=tuple(self.db.execute('SELECT * FROM deliveries').fetchone())
  await self.reply(102,'Уже нашли');await self.settle();cid=(await self.capture.drain())[0]
  c=self.latest_context(cid);self.assertEqual(c['reply_candidate_id'],parent);self.assertEqual(c['reply_resolution'],'same_source_saved_parent')
  self.assertEqual(policy.latest(self.db,cid)['route'],'review');self.assertEqual(self.db.execute('SELECT body FROM events WHERE message_id=?',('102',)).fetchone()[0],'Уже нашли')
  self.assertEqual(tuple(self.db.execute('SELECT * FROM candidates WHERE id=?',(parent,)).fetchone()),saved)
  self.assertEqual(tuple(self.db.execute('SELECT * FROM deliveries').fetchone()),sent)
  self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),0);self.assertEqual(self.db.execute('SELECT count(*) FROM filter_updates').fetchone()[0],0)
  await self.reply(103,'Нужен лендинг на Tilda');await self.settle();independent=(await self.capture.drain())[0]
  self.assertEqual(policy.latest(self.db,independent)['route'],'main')
  self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),1)
  self.assertNotIn('500000',sink.calls[-1][1]['text'])
 async def test_missing_foreign_invalid_and_unsaved_reply_context_stays_unknown(self):
  policy.activate(self.db,policy.V3,monitor.iso(monitor.now()));await self.capture.arm()
  cases=[(101,None,None,'reference_unavailable'),(102,'4294967301','-999','foreign_source_not_linked'),
   (103,'4294967297.5','-123','reference_unavailable'),(104,'4294967297','-123','parent_not_saved')]
  for mid,target,peer,expected in cases:
   await self.reply(mid,'Мне тоже',target,peer);await self.settle();cid=(await self.capture.drain())[0]
   self.assertEqual(self.latest_context(cid)['reply_resolution'],expected);self.assertEqual(policy.latest(self.db,cid)['route'],'review')
  sink=Sink();self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),0)
 async def test_forwarded_request_uses_group_envelope_and_unknown_original(self):
  policy.activate(self.db,policy.V3,monitor.iso(monitor.now()));await self.capture.arm()
  await self.page.evaluate('''()=>{
   const b=document.createElement('div');b.className='bubble forwarded';b.dataset.mid='4294967397';b.dataset.peerId='-123';b.dataset.timestamp=String(Math.ceil(Date.now()/1000));
   const header=document.createElement('span');header.className='bubble-name-forwarded';header.textContent='Forwarded from Unknown Original';
   const m=document.createElement('div');m.className='message';m.textContent='Нужен лендинг';b.append(header,m);document.querySelector('#messages').append(b);
  }''');await self.settle();cid=(await self.capture.drain())[0]
  c=self.latest_context(cid);self.assertTrue(c['forwarded']);self.assertEqual(c['forward_origin_status'],'original_unknown')
  e=self.db.execute('SELECT * FROM events WHERE message_id=?',('101',)).fetchone();self.assertEqual(e['body'],'Нужен лендинг');self.assertIsNone(e['author'])
  self.assertEqual(e['link'],'https://t.me/approved_group/101')
  sink=Sink();self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),1)
  self.assertIn('Пересылка',sink.calls[0][1]['text']);self.assertIn('Оригинальные автор, дата и ссылка не установлены',sink.calls[0][1]['text'])
  self.assertNotIn('Unknown Original',sink.calls[0][1]['text']);self.assertLessEqual(len(sink.calls[0][1]['text'].encode('utf-16-le'))//2,4000)
  await self.capture.stop();await self.capture.arm();self.assertEqual(await self.capture.drain(),[])
  self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),0);self.assertEqual(len(sink.calls),1)
 async def test_quote_and_forward_name_cannot_supply_a_missing_order(self):
  policy.activate(self.db,policy.V3,monitor.iso(monitor.now()));await self.capture.arm()
  await self.reply(101,'Мне тоже');await self.settle();cid=(await self.capture.drain())[0]
  self.assertEqual(policy.latest(self.db,cid)['route'],'review')
  await self.page.locator('.reply').evaluate("e=>e.textContent='Нужен новый сайт, новый бюджет'")
  await self.page.wait_for_timeout(70);self.assertEqual(await self.capture.drain(),[])
  self.assertEqual(self.db.execute('SELECT count(*) FROM filter_decisions').fetchone()[0],1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
  await self.add(mid=102,text='Всем привет')
  await self.page.locator('[data-mid="4294967398"]').evaluate('''b=>{
   b.classList.add('forwarded');const header=document.createElement('span');header.className='bubble-name-forwarded';header.textContent='Нужен сайт, срочно';b.prepend(header);
  }''')
  await self.settle();other=(await self.capture.drain())[0]
  self.assertEqual(policy.latest(self.db,other)['route'],'none');self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
  await self.add(mid=103,text='')
  await self.page.locator('[data-mid="4294967399"]').evaluate("b=>b.classList.add('forwarded')")
  await self.page.wait_for_timeout(70);self.assertEqual(await self.capture.drain(),[])
  self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],2)
 async def test_current_dom_mismatch_does_not_invent_context_or_original_author(self):
  policy.activate(self.db,policy.V3,monitor.iso(monitor.now()));await self.capture.arm();await self.reply(101,'Мне тоже');await self.settle()
  await self.page.locator('.bubble.is-reply').evaluate('e=>e.remove()')
  cid=(await self.capture.drain())[0];c=self.latest_context(cid)
  self.assertEqual(c['status'],'unavailable');self.assertIsNone(c['reply_candidate_id']);self.assertIsNone(c['forwarded'])
  self.assertEqual(policy.latest(self.db,cid)['route'],'review')
 async def test_live_method_upgrade_preserves_observer_baseline_and_identity_types(self):
  await self.capture.arm();before_identity=ingest.BrowserIdentity;before_error=ingest.BrowserAccessError
  initial=self.intake.started_at;reader=SimpleNamespace(db=self.db,sessions=[SimpleNamespace(capture=self.capture)])
  result=ctx.install_live_capture(reader,Path(ctx.__file__).parent)
  self.assertEqual(result['observers_restarted'],0);self.assertIs(ingest.BrowserIdentity,before_identity);self.assertIs(ingest.BrowserAccessError,before_error)
  self.assertTrue(self.capture.active);self.assertEqual(self.intake.started_at,initial)
  import multi_source,browser_capture
  self.assertIs(multi_source.BrowserIntake,ingest.BrowserIntake)
  self.assertIs(multi_source.BrowserCapture,browser_capture.BrowserCapture)
  await self.add();await self.settle();self.assertEqual(len(await self.capture.drain()),1)
  await self.page.locator('#account').evaluate("e=>e.textContent='foreign_account'")
  with self.assertRaises(before_error):await self.capture.drain()

class ContextEvidenceTests(unittest.TestCase):
 def test_malformed_context_never_claims_an_author_or_reply_link(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=monitor.connect(Path(tmp)/'test.sqlite3');ctx.ensure_schema(db)
   try:
    for payload in (None,{},[],{'status':'observed_dom','context_version':ctx.VERSION,'reply_present':1,'forwarded':False}):
     c=ctx.normalized(payload,'client:-1000000000123','101',db)
     self.assertEqual(c['status'],'unavailable');self.assertIsNone(c['reply_candidate_id']);self.assertIsNone(c['forwarded'])
   finally:db.close()
 def test_no_forwards_css_is_not_forwarded_and_context_repeats_are_not_deliveries(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=monitor.connect(Path(tmp)/'test.sqlite3');ctx.ensure_schema(db);cfg=monitor.Config(Path(tmp)/'test.sqlite3',owner_id=42,source_ids=(-1,),quiet_start=0,quiet_end=0)
   try:
    at=monitor.now();policy.activate(db,policy.V3,monitor.iso(at));cid=monitor.ingest(db,cfg,'-1','101','Нужен сайт',at,at)
    payload={'status':'observed_dom','context_version':ctx.VERSION,'reply_present':False,'forwarded':False}
    for _ in range(3):ctx.record(db,cid,'1','client:-1000000000123','101',payload,monitor.iso(at))
    self.assertEqual(db.execute('SELECT count(*) FROM browser_message_context').fetchone()[0],1)
    sink=Sink();self.assertEqual(monitor.deliver(db,cfg,sink,at),1);self.assertEqual(monitor.deliver(db,cfg,sink,at),0)
   finally:db.close()
