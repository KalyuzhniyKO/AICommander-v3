"""Actual protected API; owner confirmation is a mutation, not inference."""
import json
from uuid import uuid4

from backend.app import main
from backend.app.storage.db import connect
from contextlib import closing


def approval(status,scope='task',amount=1000):
    return {'approval_id':uuid4().hex,'scope':scope,'task_id':status['task_id'],
            'amount_micro_usd':amount,'allow_paid':True,'reference':'openrouter/fixture/paid',
            'expected_revision':next(a['revision'] for a in status['accounts'] if a['scope']==scope),
            'expected_policy_sha256':status['policy_sha256'],'confirm':True}


def test_default_zero_and_unknown_without_guessed_subscription_or_billing(client):
    data=client.get('/spending/status').json()
    assert data['scope']=='runtime_spending' and data['policy_available'] is False
    assert data['estimates']==[] and data['subscription_quota_remaining'] is None
    assert data['real_cash_payment_micro_usd'] is None
    assert data['accounts'][0]['limits']=={'allow_paid':False,'max_micro_usd':0}
    assert data['accounts'][0]['reported_cost_micro_usd'] is None
    assert client.get('/spending/status',headers={'Authorization':''}).status_code==401


def test_owner_approval_csrf_idempotency_revision_and_price_binding(client,app_spend_policy):
    task=client.post('/tasks',json={'description':'synthetic budget task'}).json()
    path=f"/spending/tasks/{task['id']}"
    before=client.get(path).json()
    assert len(before['accounts'])==3
    payload=approval(before)
    assert client.post('/spending/approvals',headers={'X-AICommander-Request':''},json=payload).status_code==403
    assert client.post('/spending/approvals',json={**payload,'confirm':False}).status_code==422
    assert client.post('/spending/approvals',json={**payload,'confirm':1}).status_code==422
    assert client.post('/spending/approvals',json={**payload,'actor':'model'}).status_code==422
    result=client.post('/spending/approvals',json=payload)
    assert result.status_code==200 and result.json()['revision']==1
    assert client.post('/spending/approvals',json=payload).json()==result.json()
    assert client.post('/spending/approvals',json={**payload,'amount_micro_usd':2000}).status_code==409
    assert client.post('/spending/approvals',json={**payload,'approval_id':uuid4().hex}).status_code==409
    state=client.get(path).json()
    assert len(state['approvals'])==1 and state['approvals'][0]['actor']=='owner'
    assert state['approvals'][0]['after']['limits']['max_micro_usd']==1000
    assert all(a['dispatches_claimed']==0 for a in state['accounts'])
    assert state['approvals'][0]['before']['limits']['max_micro_usd']==0
    changed=approval(state,amount=2000)
    changed['expected_policy_sha256']='a'*64
    assert client.post('/spending/approvals',json=changed).status_code==409


def test_no_budget_creation_by_claiming_a_model_is_free(client,app_spend_policy):
    task=client.post('/tasks',json={'description':'synthetic'}).json()
    before=client.get(f"/spending/tasks/{task['id']}").json()
    payload=approval(before,amount=10001)
    assert client.post('/spending/approvals',json=payload).status_code==409
    assert client.post('/spending/approvals',json={**payload,'amount_micro_usd':1000,'tariff':{'cost':0}}).status_code==422
    assert client.get(f"/spending/tasks/{task['id']}").json()['approvals']==[]


def test_default_model_check_and_legacy_round_never_dispatch(client,write_models_config,monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(main,'settings',replace(main.settings,openrouter_api_key='synthetic-not-real'))
    write_models_config({role:['openrouter/fixture/paid'] for role in ('manager','architect','designer','coder','reviewer')})
    checked=client.post('/models/check').json()
    assert not checked['ok'] and all(r['status']=='blocked_budget' for r in checked['results'])
    task=client.post('/tasks',json={'description':'synthetic format report'}).json()
    assert client.post(f"/tasks/{task['id']}/run",json={}).status_code==202
    with closing(connect(main.settings.database_path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM spend_events WHERE code='pricing_policy_unavailable'").fetchone()[0]>=5


def test_real_hermes_contract_cannot_create_an_unbudgeted_operation(client,monkeypatch):
    from backend.app.taskflow import hermes_api
    from backend.app.taskflow.hermes import HermesDirector
    from backend.app.providers.omniroute import OmniRouteConfig
    from backend.app.taskflow.hermes_config import HermesConfig
    from dataclasses import replace
    monkeypatch.setattr(main,'settings',replace(main.settings,omniroute=OmniRouteConfig(enabled=True,base_url='https://gateway.invalid',api_key='synthetic')))
    calls=[]
    class Available(HermesDirector):
        @property
        def available(self):return True
    def factory(*args):
        return Available(HermesConfig(model='fixture/paid',expected_commit='f'*40),
                         OmniRouteConfig(enabled=True,base_url='https://gateway.invalid',api_key='synthetic'),
                         runner=lambda *args:calls.append(args))
    monkeypatch.setattr(hermes_api,'HermesDirector',factory)
    task=client.post('/tasks',json={'description':'synthetic'}).json()
    run=client.post(f"/taskflow/v2/tasks/{task['id']}/runs",json={'idempotency_key':'create'}).json()
    root=f"/taskflow/v2/runs/{run['run_id']}"
    assert client.post(root+'/commands',json={'idempotency_key':'start','action':'start','expected_version':1}).status_code==200
    denied=client.post(root+'/commands',json={'idempotency_key':'step','action':'step','expected_version':2})
    assert denied.status_code==402 and denied.json()['detail']['code']=='pricing_policy_unavailable'
    assert calls==[]
    assert client.get(root+'/operations').json()==[]
    assert client.get(f"/spending/tasks/{task['id']}").json()['events'][0]['code']=='pricing_policy_unavailable'
