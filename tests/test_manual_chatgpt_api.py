from uuid import uuid4

from backend.app.manual_chatgpt import source

DOC=b'<h1>GPT-9 and other models in ChatGPT</h1><table><tr><td>Plus</td><td>Included</td><td>Not included</td><td>Not included</td></tr></table>'


def test_manual_handoff_is_protected_and_never_changes_paid_budget(client,monkeypatch):
    monkeypatch.setattr(source,'fetch_public',lambda url:(DOC,url))
    task=client.post('/tasks',json={'description':'Synthetic owner brief'}).json()['id']
    assert client.get('/manual-chatgpt/status',headers={'Authorization':''}).status_code==401
    assert client.post('/manual-chatgpt/check-source',json={},headers={'X-AICommander-Request':''}).status_code==403
    assert client.post('/manual-chatgpt/check-source',json={'url':'https://evil.invalid'}).status_code==422
    public=client.post('/manual-chatgpt/check-source',json={}).json()
    assert public['recommended_model']=='GPT-9' and not public['account_selection_verified_automatically']
    chosen={'selection_id':uuid4().hex,'task_id':task,'model_label':'GPT-8','effort':'High','plan':'plus',
            'model_available':True,'effort_available':True,'high_available':True,'announced_available':False,
            'latest_visible_confirmed':True,'confirmed_picker':True}
    assert client.post('/manual-chatgpt/selections',json={**chosen,'actor':'model'}).status_code==422
    assert client.post('/manual-chatgpt/selections',json={**chosen,'allow_paid':True}).status_code==422
    assert client.post('/manual-chatgpt/selections',json={**chosen,'effort':'Pro'}).status_code==409
    saved=client.post('/manual-chatgpt/selections',json=chosen)
    assert saved.status_code==200 and saved.json()['actor']=='owner'
    assert client.post('/manual-chatgpt/selections',json=chosen).json()==saved.json()
    exported={'export_id':uuid4().hex,'task_id':task,'selection_id':chosen['selection_id'],
              'reviewed_context':'Reviewed synthetic task data','confirm_sanitized':True}
    assert client.post('/manual-chatgpt/exports',json={**exported,'confirm_sanitized':False}).status_code==409
    context=client.post('/manual-chatgpt/exports',json=exported).json()
    reply={'import_id':uuid4().hex,'export_id':context['export_id'],'answer':'Manual owner-provided answer','confirm_origin':True}
    assert client.post('/manual-chatgpt/answers',json=reply).status_code==200
    status=client.get('/manual-chatgpt/status',params={'task_id':task}).json()
    assert status['answers'][0]['answer']==reply['answer']
    finance=client.get(f'/spending/tasks/{task}').json()
    assert finance['accounts'][0]['limits']=={'allow_paid':False,'max_micro_usd':0}
    assert all(a['dispatches_claimed']==0 for a in finance['accounts'])
    assert not finance['approvals']
