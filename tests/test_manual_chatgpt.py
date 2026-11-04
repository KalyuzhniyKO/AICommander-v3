"""Synthetic public documents and manual owner handoffs; no ChatGPT account access."""
from contextlib import closing
import json
from uuid import uuid4

import pytest

from backend.app.manual_chatgpt import source
from backend.app.manual_chatgpt.service import ManualChatGPT,TTL
from backend.app.manual_chatgpt.schema import ManualError
from backend.app.storage.db import connect,init_db


def document(model='GPT-9',high='Included'):
    return f'<h1>{model} and other models in ChatGPT</h1><table><tr><th>ChatGPT plan</th><th>Medium and High</th><th>Extra High</th><th>Pro</th></tr><tr><td>Plus</td><td>{high}</td><td>Not included</td><td>Not included</td></tr><tr><td>Pro</td><td>Included</td><td>Included</td><td>Included</td></tr></table>'.encode()


@pytest.fixture
def manual(tmp_path,monkeypatch):
    now=[2000000]
    with closing(connect(tmp_path/'manual.db')) as conn:
        init_db(conn)
        conn.execute("INSERT INTO tasks(title,description,project_id) VALUES('fixture','Synthetic owner context',1)")
        conn.commit()
        engine=ManualChatGPT(conn,clock=lambda:now[0])
        def fetch(url):
            assert not conn.in_transaction
            return document(),url
        monkeypatch.setattr(source,'fetch_public',fetch)
        yield engine,now


def selection(model='GPT-9',effort='High',plan='plus',**extra):
    return {'selection_id':uuid4().hex,'task_id':1,'model_label':model,'effort':effort,'plan':plan,
            'model_available':True,'effort_available':True,'high_available':True,'announced_available':True,
            'latest_visible_confirmed':True,'confirmed_picker':True,**extra}


def test_public_announcement_is_not_account_availability_and_cache_avoids_network(manual,monkeypatch):
    engine,now=manual
    assert engine.status()['recommended_model'] is None
    status=engine.check_source()
    assert status['recommended_model']=='GPT-9' and status['model_policy']=='latest_available'
    assert status['preferred_effort']=='High' and status['account_selection_verified_automatically'] is False
    assert status['api_inference_requests']==0 and status['subscription_quota_remaining'] is None
    monkeypatch.setattr(source,'fetch_public',lambda url:pytest.fail('Fresh cache should avoid GET'))
    assert engine.check_source()['recommended_model']=='GPT-9'
    assert engine.conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0]==0


def test_new_announcement_preserves_task_model_history_and_rollout_is_manual(manual,monkeypatch):
    engine,now=manual
    engine.check_source()
    chosen=engine.confirm(1,selection('GPT-8',announced_available=False),'owner')
    assert chosen['model_label']=='GPT-8' and chosen['verification']=='owner_reported_picker'
    now[0]+=1000
    monkeypatch.setattr(source,'fetch_public',lambda url:(document('GPT-10'),url))
    assert engine.check_source(force=True)['recommended_model']=='GPT-10'
    assert engine.status(1)['selections'][0]['model_label']=='GPT-8'


def test_high_unavailable_and_plus_higher_modes_are_not_invented(manual):
    engine,now=manual;engine.check_source()
    with pytest.raises(ManualError,match='effort_unavailable'):
        engine.confirm(1,selection(high_available=False),'owner')
    for effort in ('Extra High','Pro'):
        with pytest.raises(ManualError,match='effort_not_in_plan'):
            engine.confirm(1,selection(effort=effort),'owner')
    actual=engine.confirm(1,selection(effort='Medium',high_available=False),'owner')
    assert actual['effort']=='Medium' and engine.status()['preferred_effort']=='High'
    assert engine.confirm(1,selection(effort='Pro',plan='pro'),'owner')['effort']=='Pro'


def test_stale_or_failed_source_is_unconfirmed_with_last_success_preserved(manual,monkeypatch):
    engine,now=manual
    checked=engine.check_source();successful=checked['last_success_ms']
    now[0]+=TTL+1
    assert engine.status()['source_status']=='unconfirmed'
    assert engine.status()['recommended_model'] is None
    def fail(url):raise source.SourceError('network failed')
    monkeypatch.setattr(source,'fetch_public',fail)
    result=engine.check_source(force=True)
    assert result['last_success_ms']==successful and result['last_attempt_ms']==now[0]
    assert result['announced_model']=='GPT-9' and result['recommended_model'] is None
    with pytest.raises(ManualError,match='higher_effort_unconfirmed'):
        engine.confirm(1,selection(effort='Pro',plan='pro'),'owner')


def test_owner_reviewed_export_import_is_idempotent_and_does_not_approve_code(manual):
    engine,now=manual;engine.check_source()
    chosen=engine.confirm(1,selection(),'owner')
    assert engine.draft(1)['requires_owner_sanitization'] is True
    payload={'export_id':uuid4().hex,'task_id':1,'selection_id':chosen['selection_id'],
             'reviewed_context':'Synthetic non-sensitive brief','confirm_sanitized':True}
    exported=engine.export(payload,'owner')
    assert 'think harder' not in exported['prompt']
    assert engine.export(payload,'owner')==exported
    answer={'import_id':uuid4().hex,'export_id':exported['export_id'],'answer':'<script>untrusted()</script> manual note','confirm_origin':True}
    saved=engine.import_answer(answer,'owner')
    assert engine.import_answer(answer,'owner')==saved
    assert engine.status(1)['answers'][0]['answer']==answer['answer']
    assert engine.conn.execute('SELECT COUNT(*) FROM taskflow_artifacts').fetchone()[0]==0
    assert engine.conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0]==0
    assert engine.conn.execute("SELECT status FROM tasks WHERE id=1").fetchone()[0]=='active'


def test_only_official_public_document_paths_are_allowed():
    for url in ('http://help.openai.com/en/articles/1','https://chatgpt.com/api/auth/session','https://help.openai.com@evil.invalid/en/articles/1','https://127.0.0.1/docs/a','https://help.openai.com/en/articles/1?token=private','https://developers.openai.com/auth/session'):
        assert not source.allowed(url)
    assert source.allowed('https://help.openai.com/en/articles/1')
    with pytest.raises(source.SourceError):source.parse_public(b'<h1>Private account</h1>','https://help.openai.com/en/articles/1')


def test_manual_confirmation_cannot_be_inferred_from_public_announcement(manual):
    engine,now=manual;engine.check_source()
    with pytest.raises(ManualError,match='picker_confirmation_required'):
        engine.confirm(1,selection(model_available=False),'owner')
    with pytest.raises(ManualError,match='picker_confirmation_required'):
        engine.confirm(1,selection(confirmed_picker=False),'owner')
    assert engine.status(1)['selections']==[]


def test_public_fetch_does_not_forward_auth_or_follow_private_redirect(monkeypatch):
    import io
    import urllib.error
    seen=[]
    class Opener:
        def open(self,request,timeout):
            seen.append(request)
            assert request.get_header('Authorization') is None
            assert request.get_header('Cookie') is None
            assert timeout==5
            raise urllib.error.HTTPError(request.full_url,302,'redirect',{'Location':'https://chatgpt.com/api/auth/session'},io.BytesIO(b''))
    monkeypatch.setattr(source.urllib.request,'build_opener',lambda *args:Opener())
    with pytest.raises(source.SourceError,match='source_url_not_allowed'):
        source.fetch_public('https://help.openai.com/en/articles/1')
    assert len(seen)==1


def test_manual_export_is_bound_to_its_confirmed_task(manual):
    engine,now=manual;engine.check_source()
    chosen=engine.confirm(1,selection(),'owner')
    engine.conn.execute("INSERT INTO tasks(title,description,project_id) VALUES('other','Other context',1)")
    engine.conn.commit()
    with pytest.raises(ManualError,match='selection_unconfirmed'):
        engine.export({'export_id':uuid4().hex,'task_id':2,'selection_id':chosen['selection_id'],
                       'reviewed_context':'Other reviewed data','confirm_sanitized':True},'owner')
    assert engine.conn.execute('SELECT COUNT(*) FROM manual_exports').fetchone()[0]==0
