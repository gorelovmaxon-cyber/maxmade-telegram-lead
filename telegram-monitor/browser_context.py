"""Context evidence only. No classification inheritance or parent-status changes."""
import hashlib,importlib,importlib.util,json,sqlite3,sys
from pathlib import Path

VERSION='browser-context-20261109-v1'
SCRIPT=Path(__file__).with_name('browser_message_context.js').read_text(encoding='utf-8')

def ensure_schema(db):
 db.execute('''CREATE TABLE IF NOT EXISTS browser_message_context(
 id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL REFERENCES candidates(id),event_version TEXT NOT NULL,
 observed_at TEXT NOT NULL,status TEXT NOT NULL,reply_present INTEGER,reply_message_id TEXT,
 reply_candidate_id INTEGER REFERENCES candidates(id),reply_resolution TEXT NOT NULL,forwarded INTEGER,
 forward_origin_status TEXT NOT NULL,evidence TEXT NOT NULL,evidence_hash TEXT NOT NULL,
 UNIQUE(candidate_id,event_version,evidence_hash))''')
 db.commit()

def normalized(payload,source_id,message_id,db):
 out={'context_version':VERSION,'status':'unavailable','reply_present':None,'reply_message_id':None,'reply_candidate_id':None,
      'reply_resolution':'context_unavailable','forwarded':None,'forward_origin_status':'unknown'}
 if not isinstance(payload,dict) or payload.get('status')!='observed_dom' or payload.get('context_version')!=VERSION:return out
 if type(payload.get('reply_present')) is not bool or type(payload.get('forwarded')) is not bool:return out
 out.update(status='observed_dom',reply_present=payload['reply_present'],forwarded=payload['forwarded'],
            forward_origin_status='original_unknown' if payload['forwarded'] else 'not_forwarded')
 if not payload['reply_present']:out['reply_resolution']='not_reply';return out
 peer=payload.get('reply_peer_id');mid=payload.get('reply_message_id')
 if peer is None or mid is None:out['reply_resolution']='reference_unavailable';return out
 try:
  expected=str(int(source_id.removeprefix('client:'))+10**12)
  if peer!=expected:out['reply_resolution']='foreign_source_not_linked';return out
  if not isinstance(mid,str) or not mid.isascii() or not mid.isdigit() or mid.startswith('0') or not 0<int(mid)<int(message_id):
   out['reply_resolution']='invalid_reference';return out
 except (ValueError,TypeError):out['reply_resolution']='invalid_reference';return out
 out['reply_message_id']=mid
 parent=db.execute('SELECT c.id FROM candidates c JOIN events e ON e.id=c.event_id WHERE e.source_id=? AND e.message_id=?',(source_id,mid)).fetchone()
 if parent:out.update(reply_candidate_id=parent[0],reply_resolution='same_source_saved_parent')
 else:out['reply_resolution']='parent_not_saved'
 return out

def record(db,cid,event_version,source_id,message_id,payload,observed_at):
 value=normalized(payload,source_id,message_id,db)
 evidence=json.dumps(value,sort_keys=True,ensure_ascii=False)
 digest=hashlib.sha256(evidence.encode()).hexdigest()
 with db:db.execute('''INSERT OR IGNORE INTO browser_message_context(candidate_id,event_version,observed_at,status,
 reply_present,reply_message_id,reply_candidate_id,reply_resolution,forwarded,forward_origin_status,evidence,evidence_hash)
 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(cid,event_version,observed_at,value['status'],value['reply_present'],value['reply_message_id'],
 value['reply_candidate_id'],value['reply_resolution'],value['forwarded'],value['forward_origin_status'],evidence,digest))
 return value

def card_note(db,cid,event_version):
 if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_message_context'").fetchone():return ''
 row=db.execute('SELECT evidence FROM browser_message_context WHERE candidate_id=? AND event_version=? ORDER BY id DESC LIMIT 1',(cid,event_version)).fetchone()
 if not row:return ''
 c=json.loads(row[0]);notes=[]
 if c['status']=='unavailable':return 'Контекст reply/пересылки не установлен; проверить оригинал.'
 if c['forwarded']:
  notes.append('Пересылка. Оригинальные автор, дата и ссылка не установлены. Ссылка ведёт к публикации в группе.')
 if c['reply_present']:
  if c['reply_resolution']=='same_source_saved_parent':notes.append(f"Ответ на сохранённую карточку #{c['reply_candidate_id']}; её текст и статус не наследуются.")
  else:notes.append('Это reply; надёжная связь с сохранённой карточкой не установлена.')
 return '\n'.join(notes)

def install_live_capture(reader,root):
 """Swap methods without disarming observers or replacing identity/error types."""
 import browser_ingest,browser_capture
 def load(name,path):
  spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
 intake=load('_context_ingest_release',Path(root)/'browser_ingest.py')
 intake.BrowserAccessError=browser_ingest.BrowserAccessError
 intake.BrowserIdentity=browser_ingest.BrowserIdentity
 browser_ingest.BrowserMutation=intake.BrowserMutation;browser_ingest.BrowserIntake=intake.BrowserIntake
 capture=load('_context_capture_release',Path(root)/'browser_capture.py')
 browser_capture.BrowserCapture=capture.BrowserCapture
 ensure_schema(reader.db);count=0
 for session in reader.sessions:
  current=getattr(session,'capture',None)
  if current is None:continue
  current.intake.__class__=intake.BrowserIntake;current.__class__=capture.BrowserCapture;count+=1
 import chrome_pilot,multi_source
 chrome_pilot.BrowserIntake=intake.BrowserIntake;chrome_pilot.BrowserCapture=capture.BrowserCapture
 multi_source.BrowserIntake=intake.BrowserIntake;multi_source.BrowserCapture=capture.BrowserCapture
 return {'captures_updated':count,'observers_restarted':0,'baselines_changed':0,'context_version':VERSION}
