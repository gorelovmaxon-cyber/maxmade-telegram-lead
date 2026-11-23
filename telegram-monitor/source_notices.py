"""Technical source-state notices, flushed only by the existing delivery worker."""
import datetime as dt
import monitor


def recover_interrupted(db):
    with db:db.execute("UPDATE source_notices SET state='uncertain',error_code='process_interrupted' WHERE state='sending'")


def flush(db,cfg,sink,at):
    if cfg.owner_id<=0:return 0
    # Recovery is useful only when the owner actually received a failure notice.
    with db:
        db.execute("""UPDATE source_notices SET state='superseded' WHERE kind='failed' AND state='pending'
          AND incident_id IN (SELECT id FROM source_incidents WHERE closed_at IS NOT NULL)""")
        db.execute("""UPDATE source_notices SET state='quiet_log_only' WHERE kind='recovered' AND state='pending'
          AND NOT EXISTS (SELECT 1 FROM source_notices f WHERE f.incident_id=source_notices.incident_id AND f.kind='failed' AND f.state='sent')""")
    rows=db.execute('''SELECT n.*,i.username,i.terminal_phase,i.error_type,i.opened_at FROM source_notices n JOIN source_incidents i ON i.id=n.incident_id
      JOIN sources s ON s.username=i.username WHERE n.state='pending' AND n.next_at<=? AND s.enabled=1 ORDER BY n.id LIMIT 3''',(monitor.iso(at),)).fetchall()
    count=0
    for row in rows:
        if row['kind']=='failed' and row['terminal_phase']!='manual_attention' and (at-dt.datetime.fromisoformat(row['opened_at'])).total_seconds()<300:continue
        text=(f"Чтение @{row['username']} восстановлено. Сообщения за время сбоя могли быть пропущены."
          if row['kind']=='recovered' else f"Не читается @{row['username']}. "
          +('Нужно проверить вход и доступ к группе в отдельном Chrome монитора.' if row['terminal_phase']=='manual_attention'
            else 'Автоматическое восстановление пока не помогло. Проверьте состояние монитора.'))
        with db:
            claimed=db.execute("UPDATE source_notices SET state='sending',attempts=attempts+1 WHERE id=? AND state='pending'",(row['id'],)).rowcount
        if not claimed:continue
        try:result=sink.call('sendMessage',{'chat_id':cfg.owner_id,'text':text,'disable_web_page_preview':True},timeout=10)
        except monitor.ApiError as error:
            state='uncertain' if error.code>=500 else 'failed' if error.code in (400,401,403) or row['attempts']>=4 else 'pending'
            with db:db.execute('UPDATE source_notices SET state=?,next_at=?,error_code=? WHERE id=?',
              (state,monitor.iso(at+dt.timedelta(seconds=max(error.retry_after,30))),f'api_{error.code}',row['id']))
        except Exception:
            with db:db.execute("UPDATE source_notices SET state='uncertain',error_code='network_unknown' WHERE id=?",(row['id'],))
        else:
            with db:db.execute("UPDATE source_notices SET state='sent',sent_at=?,message_id=? WHERE id=?",(monitor.iso(monitor.now()),result.get('message_id'),row['id']))
            count+=1
    return count
