"""Opt-in isolated synthetic DOM -> existing intake/filter -> labelled owner notice.

Never writes the live business DB, never posts into a group, never uses its login.
The real sink strips fixture links/buttons; this is transport acceptance, not a lead.
"""
import asyncio
import dataclasses
import json
from pathlib import Path
import tempfile

import monitor
import filter_policy
from browser_capture import BrowserCapture,DomContract
from browser_ingest import BrowserIdentity,BrowserIntake
import automation


async def run(restoration=False,run_id=None):
    from playwright.async_api import async_playwright
    cfg=automation.config();cfg.live_ready()
    api=monitor.Telegram(cfg.token)
    if api.call('getMe',{},timeout=10).get('username')!='uffweb_bot':raise ValueError('Wrong bot')
    if run_id is not None and not __import__('re').fullmatch('[A-Za-z0-9_-]{1,32}',run_id):raise ValueError('Invalid run identifier')
    receipt_path=automation.STATE/('technical-'+run_id+'.json' if run_id else 'technical-restoration-20261105.json' if restoration else 'technical-acceptance.json')
    # Persist before sending; never silently resend an uncertain technical test.
    old=automation.read_json(receipt_path)
    if old.get('send_state') in ('sending','sent','uncertain'):return old
    receipt={'started_at':monitor.iso(monitor.now()),'synthetic':True,'live_business_db_used':False,'send_state':'not_sent'}
    with tempfile.TemporaryDirectory(prefix='monitor-fixture-') as folder:
        testcfg=dataclasses.replace(cfg,db=Path(folder)/'fixture.sqlite3',client_sources=('local_fixture',),client_peer_ids=(-1000000000123,),quiet_start=0,quiet_end=0)
        db=monitor.connect(testcfg.db)
        filter_policy.activate(db,filter_policy.V3,monitor.iso(monitor.now()))
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='chrome',headless=True)
            ctx=await browser.new_context(service_workers='block');requests=[]
            async def route(req):
                requests.append(req.request.url)
                if req.request.url=='https://web.telegram.org/k/':
                    await req.fulfill(content_type='text/html',body='''<div id="fixture"><div class="bubble" data-mid="4294967396" data-peer-id="-123" data-timestamp="1000000000"><div class="message">Historical synthetic row</div></div></div>''')
                else:await req.abort()
            await ctx.route('**/*',route);page=await ctx.new_page();await page.goto('https://web.telegram.org/k/')
            async def proof():return BrowserIdentity('fixture_account','local_fixture',-1000000000123,True,page.url)
            capture=BrowserCapture(page,BrowserIntake(db,testcfg,'fixture_account','local_fixture',-1000000000123),DomContract('#fixture','-123',True),proof)
            try:
                await capture.arm()
                await page.evaluate('''()=>{const e=document.createElement('div');e.className='bubble';
                  e.dataset.mid='4294967397';e.dataset.peerId='-123';e.dataset.timestamp=String(Math.ceil(Date.now()/1000));
                  e.innerHTML='<div class="message">Нужен лендинг для компании, ищу исполнителя. Техническая синтетическая проверка.</div>';
                  document.querySelector('#fixture').append(e)}''')
                await page.wait_for_function('()=>window.__maxmadeReadOnlyObserver.status().queued>0')
                ids=await capture.drain()
                if len(ids)!=1 or db.execute("select priority from candidates").fetchone()[0]=='rejected':raise ValueError('Fixture pipeline failed')
                class TechnicalSink:
                    def call(self,method,payload,timeout=20):
                        if method!='sendMessage' or payload['chat_id']!=cfg.owner_id:raise ValueError('Unexpected destination')
                        receipt['send_state']='sending';automation.write_json(receipt_path,receipt)
                        text='ТЕХНИЧЕСКИЙ ТЕСТ: синтетический DOM → приёмник → активный фильтр → уведомление Максиму.\nЭто не реальный заказ и не сообщение группы. Рабочая база и статусы не менялись.\n\n'
                        text+=payload['text'].replace('https://t.me/local_fixture/101','[локальный fixture, публичного оригинала нет]')
                        try:result=api.call(method,{'chat_id':cfg.owner_id,'text':text,'disable_web_page_preview':True},timeout=timeout)
                        except Exception:
                            receipt['send_state']='uncertain';automation.write_json(receipt_path,receipt);raise
                        receipt.update(send_state='sent',message_id=result.get('message_id'),sent_at=monitor.iso(monitor.now()))
                        automation.write_json(receipt_path,receipt);return result
                count=monitor.deliver(db,testcfg,TechnicalSink(),monitor.now(),tuple(ids))
                duplicate=await capture.drain()
                repeat=monitor.deliver(db,testcfg,TechnicalSink(),monitor.now(),tuple(ids))
                receipt.update(first_deliveries=count,repeat_deliveries=repeat,repeat_events=len(duplicate),network_intercepted=requests==['https://web.telegram.org/k/'])
            finally:await capture.stop();await ctx.close();await browser.close();db.close()
    automation.write_json(receipt_path,receipt);return receipt


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--restoration-test',action='store_true');parser.add_argument('--run-id');args=parser.parse_args()
    try:print(json.dumps(asyncio.run(run(args.restoration_test,args.run_id)),ensure_ascii=False))
    except Exception as error:print(json.dumps({'result':'unconfirmed','error_type':type(error).__name__}));raise SystemExit(2)
