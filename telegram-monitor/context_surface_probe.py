"""Read-only private DOM audit for the three already-verified reader pages."""
import asyncio,json,sys
from pathlib import Path
CONTEXT_SCRIPT=Path(__file__).with_name('browser_message_context.js').read_text(encoding='utf-8')

INPUTS=r'''peer=>[...document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover) .bubble[data-mid][data-peer-id][data-timestamp]')]
.filter(b=>b.dataset.peerId===peer&&b.getClientRects().length&&!b.classList.contains('is-date')).map(b=>{
 const n=Number(b.dataset.mid),id=n>=4294967296?n-4294967296:n;const m=b.querySelector('.message');if(!m||!Number.isSafeInteger(id)||id<=0||id>=4294967296)return null;
 const c=m.cloneNode(true);c.querySelectorAll('.time,.reply,.reply-markup,.webpage,.reactions,.message-views').forEach(e=>e.remove());for(const i of c.querySelectorAll('img[alt]'))i.replaceWith(document.createTextNode(i.alt));
 return {message_id:String(id),published_epoch:Number(b.dataset.timestamp),body:(c.textContent||'').trim().slice(0,2001)};
}).filter(Boolean)'''

JS=r'''peer=>{
 const roots=[...document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)')];
 if(roots.length!==1)return {valid:false,reason:'root_unavailable'};
 const bubbles=[...roots[0].querySelectorAll('.bubble[data-mid][data-peer-id][data-timestamp]')].filter(e=>e.dataset.peerId===peer&&e.getClientRects().length);
 return {valid:true,visible:bubbles.length,rows:bubbles.filter(e=>e.querySelector('.reply,.bubble-name-forwarded')||e.classList.contains('forwarded')).map(e=>({
  mid:e.dataset.mid,timestamp:e.dataset.timestamp,attributes:[...e.attributes].map(a=>[a.name,a.value]),
  contexts:[...e.querySelectorAll('.reply,.forward,.forwarded,.forward-header,.forwarded-name,[class*="forward"]')].slice(0,6).map(x=>x.outerHTML.slice(0,6000)),
  body:e.querySelector('.message')?.textContent?.slice(0,2000)||''})).slice(-30)};
}'''

async def run(reader):
 import automation,local_chrome,monitor
 folder=automation.STATE/'context-acceptance-20261109';local_chrome.private_directory(folder)
 result=[]
 for s in reader.sessions:
  try:
   before=await asyncio.wait_for(s.verify(),timeout=10)
   value=await asyncio.wait_for(s.page.evaluate(JS,s.dom),timeout=10)
   events=await asyncio.wait_for(s.page.evaluate(INPUTS,s.dom),timeout=10)
   contexts=await asyncio.wait_for(s.page.evaluate(CONTEXT_SCRIPT,{'rootSelector':'.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)','domPeerId':s.dom,'events':events}),timeout=10)
   value['context_witnesses']=[dict(event,context=c) for event,c in zip(events,contexts) if c.get('reply_present') or c.get('forwarded') or c.get('status')=='unavailable']
   after=await asyncio.wait_for(s.verify(),timeout=10)
   if before!=after:raise ValueError('Identity changed')
   result.append({'source':s.name,'at':monitor.iso(monitor.now()),'identity_verified':True,**value})
  except Exception as e:result.append({'source':s.name,'identity_verified':False,'error_type':type(e).__name__})
 automation.write_json(folder/'context-witness-probe.json',result)
 return {'context_surface_probe':[{'source':r['source'],'verified':r['identity_verified'],'visible':r.get('visible',0),'context_rows':len(r.get('rows',[]))} for r in result]}
