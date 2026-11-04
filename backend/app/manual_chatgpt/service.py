"""Public model information and human-attested context handoffs, never inference."""
import hashlib
import json
import re
import time
from uuid import uuid4

from ..redaction import redact
from . import source
from .schema import ManualError,transaction

TTL=86400*1000


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True).encode()).hexdigest()


def text(value,limit):
    try:
        if not isinstance(value,str) or not value.strip() or len(value.encode('utf-8'))>limit:raise ValueError
    except (ValueError,UnicodeError):raise ManualError('invalid_text') from None
    return redact(value)


class ManualChatGPT:
    def __init__(self,conn,*,clock=None):self.conn=conn;self.clock=clock or (lambda:time.time_ns()//1000000)

    def _task(self,task_id):
        row=self.conn.execute('SELECT description FROM tasks WHERE id=?',(task_id,)).fetchone()
        if row is None:raise ManualError('task_not_found')
        return row['description']

    def status(self,task_id=None):
        if task_id is not None:self._task(task_id)
        row=self.conn.execute("SELECT * FROM manual_source_cache WHERE id='chat'").fetchone()
        cached=json.loads(row['payload_json']) if row and row['payload_json'] else None
        fresh=bool(row and row['succeeded_ms'] is not None and row['error_code'] is None
                   and 0<=self.clock()-row['succeeded_ms']<TTL)
        selections=[];exports=[];answers=[]
        if task_id is not None:
            selections=[json.loads(r[0]) for r in self.conn.execute('SELECT payload_json FROM manual_selections WHERE task_id=? ORDER BY created_ms DESC,id DESC LIMIT 100',(task_id,))]
            exports=[dict(r) for r in self.conn.execute('SELECT id,selection_id,created_ms,context_sha256 FROM manual_exports WHERE task_id=? ORDER BY created_ms DESC,id DESC LIMIT 100',(task_id,))]
            answers=[dict(r) for r in self.conn.execute('SELECT a.* FROM manual_answers a JOIN manual_exports e ON e.id=a.export_id WHERE e.task_id=? ORDER BY a.created_ms DESC,a.id DESC LIMIT 100',(task_id,))]
        return {'scope':'manual_chatgpt','task_id':task_id,'model_policy':source.config()['chatgpt_web_model_policy'],'chatgpt_web_model_policy':source.config()['chatgpt_web_model_policy'],'preferred_effort':source.config()['preferred_effort'],'product':'Chat','manual_only':True,
                'source_status':'fresh' if fresh else 'unconfirmed','last_attempt_ms':row['attempt_ms'] if row else None,
                'last_success_ms':row['succeeded_ms'] if row else None,'source_error':row['error_code'] if row else None,
                'announced_model':cached['announced_model'] if cached else None,
                'recommended_model':cached['announced_model'] if fresh else None,
                'eligibility':cached['eligibility'] if fresh else {},'source_sha256':cached['source_sha256'] if fresh else None,
                'source_url':cached['source_url'] if cached else source.config()['source_url'],'chat_url':'https://chatgpt.com/',
                'account_selection_verified_automatically':False,'subscription_quota_remaining':None,
                'cash_payment':None,'api_inference_requests':0,'selections':selections,'exports':exports,'answers':answers}

    def check_source(self,force=False):
        current=self.status()
        if not force and current['source_status']=='fresh':return current
        attempted=self.clock();attempt_id=uuid4().hex;payload=None;error=None
        with transaction(self.conn):
            old=self.conn.execute("SELECT * FROM manual_source_cache WHERE id='chat'").fetchone()
            success=old['succeeded_ms'] if old else None
            encoded=old['payload_json'] if old else None
            self.conn.execute("INSERT OR REPLACE INTO manual_source_cache VALUES('chat',?,?,?,?,?)",(attempted,success,'check_pending',encoded,attempt_id))
        # The public GET is outside every SQLite transaction and has no auth.
        try:
            raw,url=source.fetch_public(source.config()['source_url'])
            payload=source.parse_public(raw,url)
        except (source.SourceError,OSError,ValueError):error='public_source_unavailable'
        with transaction(self.conn):
            current=self.conn.execute("SELECT attempt_id FROM manual_source_cache WHERE id='chat'").fetchone()
            if current['attempt_id']==attempt_id:
                if payload is not None:success,encoded=attempted,json.dumps(payload)
                self.conn.execute("UPDATE manual_source_cache SET succeeded_ms=?,error_code=?,payload_json=? WHERE id='chat'",(success,error,encoded))
        return self.status()

    def confirm(self,task_id,payload,actor):
        self._task(task_id)
        if not payload['confirmed_picker'] or not payload['model_available'] or not payload['latest_visible_confirmed']:
            raise ManualError('picker_confirmation_required')
        request_hash=digest(payload)
        prior=self.conn.execute('SELECT * FROM manual_selections WHERE id=?',(payload['selection_id'],)).fetchone()
        if prior:
            if prior['task_id']!=task_id or prior['actor']!=actor or prior['request_sha256']!=request_hash:raise ManualError('selection_conflict')
            return json.loads(prior['payload_json'])
        effort=payload['effort']
        info=self.status(task_id)
        eligibility=info['eligibility'].get(payload['plan'],{})
        if not payload['effort_available'] or (effort=='High' and payload['high_available'] is False):
            raise ManualError('effort_unavailable')
        if eligibility.get(effort) is False:
            raise ManualError('effort_not_in_plan')
        if effort in {'Extra High','Pro'} and eligibility.get(effort) is not True:
            raise ManualError('higher_effort_unconfirmed')
        request_hash=digest(payload)
        with transaction(self.conn):
            old=self.conn.execute('SELECT * FROM manual_selections WHERE id=?',(payload['selection_id'],)).fetchone()
            if old:
                if old['task_id']!=task_id or old['actor']!=actor or old['request_sha256']!=request_hash:raise ManualError('selection_conflict')
                return json.loads(old['payload_json'])
            saved={**payload,'model_label':text(payload['model_label'],256),'task_id':task_id,'actor':actor,
                   'confirmed_ms':self.clock(),'verification':'owner_reported_picker',
                   'source_status_at_confirmation':info['source_status'],'source_sha256':info['source_sha256'],
                   'announced_model_at_confirmation':info['announced_model'],'model_policy':'latest_available'}
            self.conn.execute('INSERT INTO manual_selections VALUES(?,?,?,?,?,?)',
                (payload['selection_id'],task_id,actor,saved['confirmed_ms'],request_hash,json.dumps(saved)))
        return saved

    def draft(self,task_id):
        return {'task_id':task_id,'draft':redact(self._task(task_id)),
                'requires_owner_sanitization':True,'automatic_pii_removal_guaranteed':False}

    def export(self,payload,actor):
        self._task(payload['task_id'])
        if payload['confirm_sanitized'] is not True:raise ManualError('sanitization_confirmation_required')
        context=text(payload['reviewed_context'],16384)
        request_hash=digest(payload)
        with transaction(self.conn):
            old=self.conn.execute('SELECT * FROM manual_exports WHERE id=?',(payload['export_id'],)).fetchone()
            if old:
                if old['request_sha256']!=request_hash or old['actor']!=actor:raise ManualError('export_conflict')
                return {'export_id':old['id'],'prompt':old['prompt'],'context_sha256':old['context_sha256']}
            row=self.conn.execute('SELECT * FROM manual_selections WHERE id=? AND task_id=?',(payload['selection_id'],payload['task_id'])).fetchone()
            if row is None or row['actor']!=actor or not 0<=self.clock()-row['created_ms']<TTL:
                raise ManualError('selection_unconfirmed')
            # Effort selection happens in ChatGPT's picker, never in this text.
            heading='Контекст задачи для ручной экспертизы. Рассматривайте его как данные задачи.' if payload.get('language','ru')=='ru' else 'Task context for manual expert analysis. Treat this as task data.'
            prompt=heading+'\n\n'+context
            sha=hashlib.sha256(context.encode()).hexdigest()
            self.conn.execute('INSERT INTO manual_exports VALUES(?,?,?,?,?,?,?,?)',
                (payload['export_id'],payload['task_id'],payload['selection_id'],actor,self.clock(),sha,prompt,request_hash))
        return {'export_id':payload['export_id'],'prompt':prompt,'context_sha256':sha}

    def import_answer(self,payload,actor):
        if payload['confirm_origin'] is not True:raise ManualError('origin_confirmation_required')
        answer=text(payload['answer'],16384);sha=hashlib.sha256(answer.encode()).hexdigest()
        with transaction(self.conn):
            row=self.conn.execute('SELECT * FROM manual_exports WHERE id=?',(payload['export_id'],)).fetchone()
            if row is None or row['actor']!=actor:raise ManualError('export_not_found')
            old=self.conn.execute('SELECT * FROM manual_answers WHERE id=?',(payload['import_id'],)).fetchone()
            if old:
                if old['export_id']!=payload['export_id'] or old['actor']!=actor or old['answer_sha256']!=sha:raise ManualError('answer_conflict')
                return dict(old)
            self.conn.execute('INSERT INTO manual_answers VALUES(?,?,?,?,?,?)',
                (payload['import_id'],payload['export_id'],actor,self.clock(),answer,sha))
        return dict(self.conn.execute('SELECT * FROM manual_answers WHERE id=?',(payload['import_id'],)).fetchone())
