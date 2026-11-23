/* DOM-only candidate sensor for Telegram Web K. No requests, storage, clicks or sender.
   The controller must independently verify account/source/member before installation.
   Initial rows are IDs only; only subsequent visible mutations can enter the queue.
   Candidate selectors/version MUST be checked against the live site before enabling. */
(options) => {
  'use strict';
  const key = '__maxmadeReadOnlyObserver';
  if (window[key]) throw new Error('observer_already_installed');
  const offset = 4294967296;
  const root = document.querySelector(options.rootSelector);
  if (!root || document.querySelectorAll(options.rootSelector).length !== 1)
    throw new Error('unique_message_root_required');
  if (!/^-[1-9][0-9]*$/.test(options.domPeerId)) throw new Error('invalid_peer');
  const maxQueue = Math.min(256, Math.max(1, options.maxQueue || 128));
  const visible = e => {
    if (!e || !e.isConnected || e.hidden) return false;
    const box=e.getBoundingClientRect(), style=getComputedStyle(e);
    return box.width>0 && box.height>0 && style.display!=='none' && style.visibility!=='hidden';
  };
  const originalId = value => {
    if (!/^[1-9][0-9]*$/.test(value || '')) return null;
    const n=Number(value);
    // Temp fractional/outgoing and ephemeral IDs have no safe original permalink.
    if (!Number.isSafeInteger(n) || n>=offset*2) return null;
    const server=n>=offset ? n-offset : n;
    return server>0 && server<offset ? String(server) : null;
  };
  const metadata = bubble => {
    if (!bubble.matches('.bubble[data-mid][data-peer-id][data-timestamp]') ||
        bubble.classList.contains('is-date') || bubble.dataset.peerId!==options.domPeerId ||
        !visible(bubble)) return null;
    const id=originalId(bubble.dataset.mid), timestamp=Number(bubble.dataset.timestamp);
    if (!id || !Number.isSafeInteger(timestamp) || timestamp<=0) return null;
    return {message_id:id, published_epoch:timestamp};
  };
  const baseline=[];
  for (const bubble of root.querySelectorAll('.bubble[data-mid]')) {
    const meta=metadata(bubble);
    if (meta) baseline.push(meta.message_id);
  }
  if (!baseline.length) throw new Error('visible_baseline_required');
  const baselineMax=Math.max(...baseline.map(Number));
  const baselineSet=new Set(baseline), accepted=new Set(), pending=new Set(), lastContent=new Map();
  let queue=[], failure=null, timer=null, observer;
  const started=Date.now(), initialUrl=location.href;
  const stop = reason => {
    failure=failure || reason;
    observer?.disconnect();
    if(timer!==null)clearTimeout(timer);
    pending.clear();queue=[];
  };
  const rootAlive = () => root.isConnected && document.querySelector(options.rootSelector)===root;
  const flush = () => {
    timer=null;
    if(failure)return;
    if(location.href!==initialUrl || !rootAlive()) { stop('page_context_changed');return; }
    const changed=[...pending];pending.clear();
    for(const bubble of changed) {
      if(failure)break;
      const meta=metadata(bubble);
      if(!meta)continue;
      if(baselineSet.has(meta.message_id))continue;
      if(!accepted.has(meta.message_id) && (Number(meta.message_id)<=baselineMax || meta.published_epoch*1000<started))continue;
      const message=bubble.querySelector('.message');
      if(!message || !visible(message))continue;
      // Remove presentation-only context; never infer an author's private contact.
      const copy=message.cloneNode(true);
      copy.querySelectorAll('.time,.reply,.reply-markup,.webpage,.reactions,.message-views').forEach(e=>e.remove());
      for(const image of copy.querySelectorAll('img[alt]'))image.replaceWith(document.createTextNode(image.alt));
      const body=(copy.textContent || '').trim().slice(0,2001);
      if(!body)continue;
      // Parent DOM mutations revisit existing siblings. That is neither a new
      // message nor an edit; suppress identical content before queueing it.
      const content=String(meta.published_epoch)+'\u0000'+body;
      if(lastContent.get(meta.message_id)===content)continue;
      if(queue.length>=maxQueue){stop('queue_overflow');break;}
      queue.push({...meta,body,observed_epoch_ms:Date.now(),kind:accepted.has(meta.message_id)?'edited':'added'});
      accepted.add(meta.message_id);
      lastContent.set(meta.message_id,content);
    }
  };
  const mark = node => {
    if(node.nodeType===Node.TEXT_NODE)node=node.parentElement;
    if(!(node instanceof Element))return;
    const bubble=node.closest('.bubble[data-mid]');
    if(bubble && root.contains(bubble))pending.add(bubble);
    for(const child of node.querySelectorAll('.bubble[data-mid]'))if(root.contains(child))pending.add(child);
  };
  observer=new MutationObserver(records=>{
    if(failure)return;
    if(!rootAlive() || location.href!==initialUrl){stop('page_context_changed');return;}
    for(const record of records){mark(record.target);for(const node of record.addedNodes)mark(node);}
    if(pending.size>256){stop('mutation_overflow');return;}
    if(timer===null)timer=setTimeout(flush,25);
  });
  observer.observe(root,{childList:true,subtree:true,characterData:true,attributes:true,
    attributeFilter:['data-mid','data-peer-id','data-timestamp']});
  const api={
    status:()=>({queued:queue.length,failure}),
    drain:()=>{
      if(!failure && (location.href!==initialUrl || !rootAlive()))stop('page_context_changed');
      const events=queue;queue=[];
      return {failure,events,root_alive:rootAlive()};
    },
    stop:()=>stop('controller_stopped')
  };
  Object.defineProperty(window,key,{value:api,configurable:true});
  return {baseline_ids:baseline,started_epoch_ms:started};
}
