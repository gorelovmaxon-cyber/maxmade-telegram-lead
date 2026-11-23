"""Reloadable private-command dispatcher; business implementation is chrome_pilot."""
import importlib
import chrome_pilot
async def dispatch(page,context,action,state):
    if action=='context_surface_probe':
        import context_surface_probe
        reader=state.get('multi_reader')
        if not reader:raise ValueError('Existing reader required')
        return await importlib.reload(context_surface_probe).run(reader)
    if action=='trial_identity_loading':
        # Explicit acceptance probe only: hide one account metadata row locally.
        # Never changes the account, group content, login or Telegram server state.
        reader=state.get('multi_reader')
        if not reader or not reader.metadata()['all_sources_reading'] or len(reader.sessions)!=3:
            raise ValueError('Three fresh sources required before the identity-loading trial')
        target=next(s for s in reader.sessions if s.name=='top_ads_freelance')
        result=await target.page.evaluate('''expected=>{
          const panels=[...document.querySelectorAll('.settings-container.active')];
          const own=[...document.querySelectorAll('.settings-container.active .profile-content.is-me')];
          const rows=panels.flatMap(p=>[...p.querySelectorAll('.row')].filter(r=>/^(Username|Имя пользователя|Имя пользователя Telegram)$/i.test(r.querySelector('.row-subtitle')?.textContent?.trim()||'')));
          if(panels.length!==1||own.length!==1||!own[0].getClientRects().length||rows.length!==1||rows[0].querySelector('.row-title')?.textContent?.trim()!==expected)
            return {injected:false};
          const row=rows[0],marker=document.createComment('local-identity-loading-acceptance');
          row.replaceWith(marker);setTimeout(()=>{if(marker.isConnected)marker.replaceWith(row)},4000);
          return {injected:true,duration_ms:4000,account_changed:false,group_content_changed:false};
        }''',chrome_pilot.ACCOUNT)
        if not result['injected']:raise ValueError('Expected account panel was not uniquely verified')
        return {'identity_loading_trial':{**result,'source':target.name,'before_generation':target.generation},**reader.metadata()}
    if action=='inventory':
        import automation,monitor,filter_policy
        reader=state.get('multi_reader')
        inventory={'at':monitor.iso(monitor.now()),'context_pages':len(context.pages),
          'sources':[{'username':s.name,'phase':s.phase,'page_closed':not s.page or s.page.is_closed(),
            'capture_active':bool(s.capture and s.capture.active)} for s in reader.sessions] if reader else [],
          'filter_active':filter_policy.get_meta(reader.db,'filter_active_version') if reader else None,
          'loaded_profile_version':monitor.order_profile.profile()['version'],
          'classify_module':monitor.classify.__module__,'reader_started':bool(reader and reader.started)}
        automation.write_json(automation.STATE/'runtime-inventory.json',inventory)
        return {'runtime_inventory':inventory,**(reader.metadata() if reader else {})}
    if action in ('filter_v3','filter_v2'):
        import filter_control,filter_policy
        reader=state.get('multi_reader')
        if not reader:raise ValueError('No active multi-source reader')
        try:
            # Resolve the version after reload: the live process may still hold
            # the preceding release's V3 constant. Never partially apply it.
            importlib.reload(filter_policy)
            return importlib.reload(filter_control).hot_apply(reader,filter_policy.V3 if action=='filter_v3' else filter_policy.V2)
        except ValueError as error:
            # A declined policy switch must not masquerade as a failed reader.
            return {**reader.metadata(),'filter_switch':{'result':'not_applied','error_type':type(error).__name__}}
    if action=='show_membership_review':
        import show_source_membership
        return await importlib.reload(show_source_membership).run(context,state)
    if action=='trial_close_context':
        await context.close()
        return {'context_close_trial_requested':True,'runtime_alive':False,'context_closed':True}
    if action=='candidate_joined_three':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context,
            names=('onpeak_chat','businessclass_rbc','spbeventchat'),destination='candidate-joined-three.json')
    if action=='source_tail_metadata':
        import automation,monitor
        rows=[];reader=state.get('multi_reader')
        for session in reader.sessions if reader else []:
            row={'username':session.name,'baseline':session.baseline}
            if session.page and not session.page.is_closed():
                row.update(await session.page.evaluate('''peer=>{
                  const rows=[...document.querySelectorAll('.chat.active .bubble[data-mid][data-peer-id][data-timestamp]')]
                    .filter(e=>e.dataset.peerId===peer && e.getClientRects().length && !e.classList.contains('is-date'));
                  return {at_end:!!document.querySelector('.chat.active .bubbles.scrolled-down'),
                    rows:rows.map(e=>({mid:e.dataset.mid,published_epoch:e.dataset.timestamp}))};}''',session.dom))
            rows.append(row)
        automation.write_json(automation.STATE/'source-tail-metadata.json',{'at':monitor.iso(monitor.now()),'sources':rows})
        return {'source_tail_checks':len(rows)}
    if action=='resume_waiting_sources':
        import asyncio,multi_source
        importlib.reload(multi_source)
        reader=state.get('multi_reader')
        if not reader:raise ValueError('No active reader')
        resumed=[]
        for index,old in enumerate(list(reader.sessions)):
            if old.phase not in ('manual_attention','retry_limit_reached'):continue
            task=reader.tasks[index]
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
            if old.capture:await old.capture.stop()
            if old.page and not old.page.is_closed():await old.page.close()
            if not getattr(reader,'attach_gate',None):reader.attach_gate=asyncio.Lock()
            new=multi_source.SourceSession(old.definition,context,reader.db,reader.cfg,reader.journal,reader.stop_event,reader.attach_gate)
            new.generation=old.generation;reader.sessions[index]=new;reader.tasks[index]=asyncio.create_task(new.run())
            resumed.append(new.name);await asyncio.sleep(1)
        return {'resumed_waiting_sources':resumed}
    if action=='candidate_biznes':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context,names=('biznes_chat',),destination='candidate-biznes.json')
    if action=='candidate_remaining':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context,
            names=('businessclass_rbc','onpeak_chat','expopeople','marketologiyuga','imweek_ru','bit24dev','bitrixfordevelopers','spbeventchat'),destination='candidate-remaining.json')
    if action=='candidate_next_batch':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context,
            names=('biznes_chat','bitrixinside24','odp2021','chat_frilans','chat_kovalev'),destination='candidate-next-batch.json')
    if action=='candidate_stage_five':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context,
            names=('free_chat_for_freelance','tilda_official_chat'),destination='candidate-stage-five.json')
    if action=='prepare_operator_test':
        import operator_checks,resource_probe
        importlib.reload(operator_checks)
        importlib.reload(chrome_pilot);importlib.reload(resource_probe)
        reader=state.get('multi_reader')
        if not reader or not reader.metadata()['all_sources_reading']:raise ValueError('Live reader required')
        import asyncio,monitor
        card=await asyncio.to_thread(operator_checks.prepare,reader.cfg,monitor.Telegram(reader.cfg.token),'T-20261109-3S-01')
        return {'operator_test_card':card,'closed_codex_check':operator_checks.arm_closed_check()}
    if action=='add_pending_source':
        import multi_source
        importlib.reload(multi_source)
        reader=state.get('multi_reader')
        if not reader:raise ValueError('No active multi reader')
        # Bind the new method only; existing instances/tasks keep their identities.
        return await multi_source.MultiReader.add_pending(reader)
    if action=='candidate_pinned_rules':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context,
            names=('smmruschats','business_assistants','free_chat_for_freelance','Frilanser_100','tilda_official_chat'),destination='candidate-pinned-rules.json')
    if action=='refresh_reports':
        import source_registry
        importlib.reload(source_registry)
        reader=state.get('multi_reader')
        if reader:source_registry.write_report(source_registry.report(reader.journal,reader.db))
        return {'source_report_refreshed':bool(reader)}
    if action=='candidate_rules_next':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context,
            names=('chat_frilans','smmruschats','odp2021','business_assistants','chat_kovalev'),destination='candidate-rules-next.json')
    if action=='candidate_rules':
        import audit_candidate_rules
        return await importlib.reload(audit_candidate_rules).run(context)
    if action=='resume_waiting_source':
        import asyncio
        import multi_source
        importlib.reload(multi_source)
        reader=state.get('multi_reader')
        if not reader or len(reader.sessions)<2:raise ValueError('Multi-source trial required')
        old=reader.sessions[-1]
        if old.phase not in ('manual_attention','retry_limit_reached'):raise ValueError('Source is not waiting')
        index=len(reader.sessions)-1;task=reader.tasks[index]
        if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        if old.capture:await old.capture.stop()
        if old.page and not old.page.is_closed():await old.page.close()
        new=multi_source.SourceSession(old.definition,context,reader.db,reader.cfg,reader.journal,reader.stop_event,getattr(reader,'attach_gate',None))
        new.generation=old.generation
        reader.sessions[-1]=new;reader.tasks[index]=asyncio.create_task(new.run())
        return {'resumed_source':new.name}
    if action in ('trial_close_one','trial_reload_one'):
        reader=state.get('multi_reader')
        if not reader or len(reader.sessions)<2:raise ValueError('Trial requires multiple sources')
        target=reader.sessions[-1]
        if action=='trial_close_one':await target.page.close()
        else:await target.page.reload(wait_until='domcontentloaded',timeout=30000)
        return {'last_source_trial':{'action':action,'source':target.name}}
    if action=='prune_reader_pages':
        reader=state.get('multi_reader')
        if not reader:return {'pruned_reader_pages':0}
        active={s.page for s in reader.sessions if s.page and not s.page.is_closed()}
        count=0
        for candidate in list(context.pages):
            if candidate in active:continue
            # This command only removes obsolete verified-reader pages, never login forms.
            if await candidate.locator('input[type=tel],input[type=password]').count():continue
            if candidate is page and chrome_pilot.origin_ok(candidate) and reader.metadata()['all_sources_reading']:
                await candidate.close();count+=1;continue
            if await candidate.locator('.settings-container.active .profile-content.is-me').count()!=1:continue
            links=await candidate.locator('#column-right .row-title').all_text_contents()
            if any(('t.me/'+s.name) in links for s in reader.sessions):await candidate.close();count+=1
        return {'pruned_reader_pages':count,'reader_pages':len(active)}
    if action=='multi_resume':
        import multi_source
        return await importlib.reload(multi_source).activate(page,context,state)
    if action=='multi_inspect':
        reader=state.get('multi_reader');rows=[]
        for session in reader.sessions if reader else []:
            row={'username':session.name,'closed':not session.page or session.page.is_closed()}
            if not row['closed']:
                row.update(await session.page.evaluate('''()=>({
                  topbar_peers:[...new Set([...document.querySelectorAll('.chat.active .topbar [data-peer-id]')].map(e=>e.dataset.peerId))],
                  own_visible:!!document.querySelector('.settings-container.active .profile-content.is-me')?.getClientRects().length,
                  roots:document.querySelectorAll('.chat.active .bubbles-inner:not(.bubbles-remover)').length,
                  at_end:!!document.querySelector('.chat.active .bubbles.scrolled-down'),
                  down_buttons:[...document.querySelectorAll('.bubbles-go-down')].map(e=>({classes:e.className,visible:!!e.getClientRects().length})),
                  links:[...document.querySelectorAll('#column-right .row-title')].map(e=>e.textContent.trim()).filter(s=>/^t\\.me\\//.test(s)),
                  overlay:document.querySelectorAll('.btn-menu-overlay').length,
                  auth:document.querySelectorAll('input[type=tel],input[type=password]').length})'''))
            rows.append(row)
        return {'multi_inspection':rows}
    if action=='source_rules':
        import source_rules_probe
        return await importlib.reload(source_rules_probe).run(context)
    if action=='selected_layout_probe':
        return {'layout_probe':await page.evaluate('''()=>({
          width:innerWidth,height:innerHeight,ratio:devicePixelRatio,
          connection_pending:[...document.querySelectorAll('#column-left [role="status"].sr-only')].some(e=>(e.textContent||'').trim().length>0),
          connection_labels:[...document.querySelectorAll('#column-left .input-search-placeholder')].map(e=>(e.textContent||'').trim()).filter(t=>/^(Search|Поиск|Waiting|Connecting|Updating|Reconnecting|Ожидание|Соединение|Подключение|Обновление|Переподключение)/i.test(t)).slice(0,3),
          topbar_peers:[...document.querySelectorAll('.chat.active .topbar [data-peer-id]')].map(e=>e.dataset.peerId),
          original_rows:[...document.querySelectorAll('.chatlist-chat')].filter(e=>e.dataset.peerId==='-1513982592').map(e=>({tag:e.tagName,classes:e.className,href:e.getAttribute('href'),visible:!!e.getClientRects().length})),
          left_buttons:[...document.querySelectorAll('#column-left .sidebar-header button,#column-left .sidebar-header .btn-icon')].map(e=>({tag:e.tagName,classes:e.className,label:e.getAttribute('aria-label'),visible:!!e.getClientRects().length})).slice(0,12),
          chats:[...document.querySelectorAll('.chat')].map(e=>({classes:e.className,roots:e.querySelectorAll('.bubbles-inner').length,bubbles:e.querySelectorAll('.bubble[data-mid]').length})),
          roots:[...document.querySelectorAll('.bubbles-inner')].map(e=>({classes:e.className,visible:!!e.getClientRects().length})),
          source_links:[...document.querySelectorAll('#column-right .row-title')].map(e=>e.textContent.trim()).filter(t=>/^t\\.me\\//i.test(t)),
          own_visible:!!document.querySelector('.settings-container.active .profile-content.is-me')?.getClientRects().length,
          profile_visible:!!document.querySelector('#column-right .profile-content')?.getClientRects().length,
          online:navigator.onLine})''')}
    if action=='verify_selected':
        import verify_selected_groups
        return await importlib.reload(verify_selected_groups).run(page,context,state)
    if action in ('open_five_groups','next_five_groups'):
        import batch_group_tabs
        return await importlib.reload(batch_group_tabs).open_batch(page,context,state,next_batch=action=='next_five_groups')
    if action=='join_authorized_groups':
        import join_authorized_groups
        return await importlib.reload(join_authorized_groups).run(page,context,state)
    if action=='retry_unjoined_tabs':
        import manual_membership
        return await importlib.reload(manual_membership).retry_failed_tabs(page,context,state)
    if action=='show_unjoined':
        import manual_membership
        return await importlib.reload(manual_membership).show_confirmed_unjoined(page,context,state)
    if action=='diagnose_join_tab':
        tabs=[p for p in context.pages if p is not page]
        target=tabs[0] if tabs else page
        await target.bring_to_front()
        await target.wait_for_timeout(1500)
        return {'join_tab_diagnostic':await target.evaluate('''()=>({
          ready:document.readyState,own:document.querySelectorAll('.profile-content.is-me').length,
          topbars:document.querySelectorAll('.chat.active .topbar').length,
          chatinfo:document.querySelectorAll('.chat.active .topbar .chat-info-container').length,
          profiles:document.querySelectorAll('#column-right .profile-content').length,
          actions:document.querySelectorAll('#column-right .profile-action').length,
          auth:document.querySelectorAll('input[type=tel],input[type=password]').length,
          otherWindow:/Telegram is open in another|Telegram открыт в другом|открыт в другой вкладке/i.test(document.body.innerText),
          buttons:[...document.querySelectorAll('button,.btn-primary')].filter(e=>e.getClientRects().length).length})''')}
    if action=='recheck_remaining':
        import manual_membership
        return await importlib.reload(manual_membership).recheck_existing(page,context,state)
    if action=='reopen_remaining':
        import manual_membership
        return await manual_membership.reopen_remaining(page,context,state)
    if action=='open_groups':
        import manual_group_tabs
        return await manual_group_tabs.open_tabs(page,context,state)
    if action=='join_window_status':
        main=await chrome_pilot.dispatch(page,context,'diagnose',state)
        return {'join_window_status':{'tabs':len(context.pages),'main':main}}
    if action=='resume_reader_keep_window':
        state['manual_join_mode']=True
        import chrome_startup
        return await importlib.reload(chrome_startup).activate(page,context,state)
    return await importlib.reload(chrome_pilot).dispatch(page,context,action,state)
