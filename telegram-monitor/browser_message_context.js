/* A read-only, current-DOM witness. Never reads storage, navigates or sends.
   Quoted text/forwarded names never become the message body, author or date. */
(options) => {
  const roots=[...document.querySelectorAll(options.rootSelector)];
  const offset=4294967296;
  const original=value=>{
    if(!/^[1-9][0-9]*$/.test(value||''))return null;
    let n=Number(value);if(!Number.isSafeInteger(n)||n>=offset*2)return null;
    if(n>=offset)n-=offset;
    return n>0&&n<offset?String(n):null;
  };
  const visible=e=>e&&e.isConnected&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  return options.events.map(raw=>{
    const missing=reason=>({status:'unavailable',reason,context_version:'browser-context-20261109-v1'});
    if(roots.length!==1)return missing('root_unavailable');
    const matches=[...roots[0].querySelectorAll('.bubble[data-mid][data-peer-id][data-timestamp]')]
      .filter(e=>visible(e)&&e.dataset.peerId===options.domPeerId&&original(e.dataset.mid)===raw.message_id);
    if(matches.length!==1)return missing('message_not_unique_or_visible');
    const bubble=matches[0],message=bubble.querySelector('.message');
    if(!visible(message)||Number(bubble.dataset.timestamp)!==raw.published_epoch)return missing('message_time_changed');
    const copy=message.cloneNode(true);
    copy.querySelectorAll('.time,.reply,.reply-markup,.webpage,.reactions,.message-views').forEach(e=>e.remove());
    for(const image of copy.querySelectorAll('img[alt]'))image.replaceWith(document.createTextNode(image.alt));
    if((copy.textContent||'').trim().slice(0,2001)!==raw.body)return missing('message_body_changed');
    const reply=bubble.classList.contains('is-reply')||!!bubble.querySelector('.reply');
    const forwarded=bubble.classList.contains('forwarded')||!!bubble.querySelector('.bubble-name-forwarded');
    return {status:'observed_dom',context_version:'browser-context-20261109-v1',
      observed_epoch_ms:Date.now(),reply_present:reply,
      reply_peer_id:reply?(bubble.dataset.replyToPeerId||null):null,
      reply_message_id:reply?original(bubble.dataset.replyToMid):null,
      forwarded,forward_origin_status:forwarded?'original_unknown':'not_forwarded'};
  });
}
