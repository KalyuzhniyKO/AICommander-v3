"""Real application DB / runtime policy, synthetic provider charges only."""
from dataclasses import replace
from contextlib import closing
import json
from hashlib import sha256
from uuid import uuid4

import pytest

from backend.app.budgets.runtime import RuntimeSpend, SpendDenied, fingerprint
from backend.app.budgets.models import Outcome, Usage
from backend.app.config import get_settings
from backend.app.storage.db import connect, init_db


@pytest.fixture
def runtime_store(tmp_path):
    now = [2_000_000]
    path = tmp_path/'runtime.db'
    with closing(connect(path)) as conn:
        init_db(conn)
        conn.execute("INSERT INTO tasks(title,description,project_id) VALUES('Fixture','test warehouse',1)")
        conn.commit()
        document = {
            'schema_version':1,'verified_at_ms':now[0]-1000,'valid_until_ms':now[0]+86400*1000-1000,
            'global_ceiling_micro_usd':10000,'project_ceiling_micro_usd':10000,'task_ceiling_micro_usd':10000,
            'routes':[{'provider':'openrouter','base_url':'https://models.invalid/v1','model_id':'fixture/free','roles':['analyst'],
                       'tariff':{'input_micro_usd_per_million':0,'output_micro_usd_per_million':0},
                       'fixed_route_verified':True,'billing_categories_verified':True,'provider_caps_verified':True,
                       'max_input_tokens':100,'max_output_tokens':10,'timeout_seconds':1},
                      {'provider':'openrouter','base_url':'https://models.invalid/v1','model_id':'fixture/paid','roles':['analyst'],
                       'reported_cost_unit':'usd','upstream_tariff':{'input_micro_usd_per_million':1000000,'output_micro_usd_per_million':1000000,'request_micro_usd':1},'tariff':{'input_micro_usd_per_million':1000000,'output_micro_usd_per_million':1000000,'request_micro_usd':1},
                       'fixed_route_verified':True,'billing_categories_verified':True,'provider_caps_verified':True,
                       'max_input_tokens':100,'max_output_tokens':10,'timeout_seconds':1}]}
        for route in document['routes']:route['account_sha256']=sha256(b'synthetic-key').hexdigest()
        policy = tmp_path/'policy.json'
        policy.write_text(json.dumps(document),encoding='utf-8')
        runtime = RuntimeSpend(conn,replace(get_settings(),spend_policy_file=str(policy),openrouter_api_key='synthetic-key'),clock_ms=lambda:now[0])
        yield runtime,document,policy,now,path


def prepare(runtime, model='fixture/free', task_id=1):
    return runtime.prepare(task_id=task_id,run_id=None,origin='legacy',role='manager',provider='openrouter',model=model,
                           base_url='https://models.invalid/v1',context_sha256=fingerprint({'task':task_id}))


def approve(runtime, scope, amount=1000, revision=0, reference='openrouter/fixture/paid'):
    return runtime.approve(task_id=1,scope=scope,amount_micro_usd=amount,allow_paid=True,reference=reference,
                           expected_revision=revision,approval_id=uuid4().hex,actor='fixture-owner',expected_policy_sha256=runtime.policy().fingerprint)


def test_verified_free_reserves_three_scopes_and_cost_is_not_invented(runtime_store):
    runtime,_,_,_,_ = runtime_store
    operation,route = prepare(runtime)
    status = runtime.status(1)
    assert len(status['accounts']) == 3
    assert all(account['limits']['allow_paid'] is False for account in status['accounts'])
    runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation,Outcome(kind='completed',http_status=200,usage=Usage(input_tokens=12,output_tokens=2)))
    status = runtime.status(1)
    assert all(a['reported_cost_micro_usd'] is None for a in status['accounts'])
    assert all(a['dispatches_claimed']==1 for a in status['accounts'])
    assert status['subscription_quota_remaining'] is None
    assert status['events'][0]['code'] == 'verified_free_route'


def test_paid_default_denial_and_three_explicit_owner_approvals(runtime_store):
    runtime,_,_,_,_ = runtime_store
    with pytest.raises(SpendDenied,match='paid_approval_required'):
        prepare(runtime,'fixture/paid')
    assert runtime.conn.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0] == 0
    for scope in ('global','project','task'):
        approve(runtime,scope)
    operation,route = prepare(runtime,'fixture/paid')
    assert all(a['budget_committed_micro_usd']==111 for a in runtime.status(1)['accounts'])
    runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation,Outcome(kind='completed',http_status=200,usage=Usage(input_tokens=12,output_tokens=2,cost_micro_usd=15)))
    assert all(a['reported_cost_micro_usd']==15 and a['remaining_micro_usd']==985 for a in runtime.status(1)['accounts'])
    assert len(runtime.status(1)['approvals']) == 3


def test_global_unknown_blocks_new_task_and_owner_cannot_erase_it(runtime_store):
    runtime,_,_,_,_ = runtime_store
    operation,route = prepare(runtime)
    runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation,Outcome(kind='unknown'))
    runtime.conn.execute("INSERT INTO tasks(title,description,project_id) VALUES('Other','Other fixture',1)")
    runtime.conn.commit()
    with pytest.raises(SpendDenied,match='outcome_unknown'):
        prepare(runtime,task_id=2)
    with pytest.raises(SpendDenied,match='outcome_unknown'):
        approve(runtime,'global')


def test_parent_rollback_discards_all_reservations_and_permission(runtime_store):
    runtime,_,_,_,path = runtime_store
    runtime.conn.execute('BEGIN IMMEDIATE')
    prepare(runtime)
    with closing(connect(path)) as other:
        assert other.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0] == 0
        assert other.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0] == 0
    runtime.conn.rollback()
    assert runtime.conn.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0] == 0
    assert runtime.conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0] == 0


def test_paid_money_limit_does_not_block_a_verified_zero_price_route(runtime_store):
    runtime,_,_,_,_ = runtime_store
    for scope in ('global','project','task'):
        approve(runtime,scope,amount=111)
    operation,route = prepare(runtime,'fixture/paid')
    runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation,Outcome(kind='completed',http_status=200,usage=Usage(input_tokens=100,output_tokens=10,cost_micro_usd=111)))
    with pytest.raises(SpendDenied,match='money_budget'):
        prepare(runtime,'fixture/paid')
    free,_=prepare(runtime)
    assert runtime.permission(free)['state']=='reserved'


def test_policy_refresh_never_clears_old_unknown_or_enables_paid(runtime_store):
    runtime,document,path,now,_ = runtime_store
    for scope in ('global','project','task'):approve(runtime,scope)
    operation,route=prepare(runtime)
    runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation,Outcome(kind='unknown'))
    document['verified_at_ms']+=1
    path.write_text(json.dumps(document),encoding='utf-8')
    with pytest.raises(SpendDenied,match='outcome_unknown'):prepare(runtime)
    assert runtime.ledger.report('runtime.global')['states']['unknown']==1
    with pytest.raises(SpendDenied):prepare(runtime,'fixture/paid')


def test_claim_is_one_shot_and_policy_changes_revoke_unclaimed_permissions(runtime_store):
    runtime,document,path,now,_=runtime_store
    operation,route=prepare(runtime)
    document['verified_at_ms']+=1
    path.write_text(json.dumps(document),encoding='utf-8')
    with pytest.raises(SpendDenied,match='dispatch_permission_stale'):
        runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation)
    assert runtime.permission(operation)['state']=='released'
    fresh,route=prepare(runtime)
    runtime.claim(fresh,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    with pytest.raises(SpendDenied,match='dispatch_permission_stale'):
        runtime.claim(fresh,context_sha256=fingerprint({'task':1}),base_url=route.base_url)


def test_provider_does_not_send_without_server_context(monkeypatch):
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.providers.omniroute import OmniRouteProvider, OmniRouteConfig
    from backend.app.providers.base import ChatMessage, ProviderError
    class Closed:
        def open(self,*a,**kw):raise AssertionError('Unapproved HTTP dispatch')
    providers=[OpenRouterProvider('synthetic-key',opener=Closed()),
               OmniRouteProvider(OmniRouteConfig(enabled=True,base_url='https://gateway.invalid',api_key='synthetic-key'),opener=Closed())]
    for provider in providers:
        with pytest.raises(ProviderError) as error:
            provider.chat('fixture/paid',[ChatMessage('user','synthetic')],1)
        assert error.value.category=='spend_blocked'


def test_actual_provider_call_uses_reservation_and_confirmed_usage(runtime_store):
    import io
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.providers.base import ChatMessage
    from backend.app.budgets.runtime import ProviderBudget
    runtime,_,_,_,_=runtime_store
    for scope in ('global','project','task'):approve(runtime,scope)
    calls=[]
    class Opener:
        def open(self,request,timeout):
            payload=json.loads(request.data)
            calls.append(payload)
            assert payload['max_tokens']==10 and timeout==1
            assert runtime.permission(runtime.conn.execute('SELECT id FROM spend_permissions').fetchone()[0])['state']=='dispatched'
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'synthetic'}}],
                'usage':{'prompt_tokens':12,'completion_tokens':2,'cost':'0.000015'}}).encode())
    provider=OpenRouterProvider('synthetic-key','https://models.invalid/v1',opener=Opener(),
        budget=ProviderBudget(runtime,task_id=1,origin='legacy',role='manager'))
    assert provider.chat('fixture/paid',[ChatMessage('user','synthetic')],1).content=='synthetic'
    assert len(calls)==1
    assert all(a['reported_cost_micro_usd']==15 for a in runtime.status(1)['accounts'])
    evidence=runtime.status(1)['usage_evidence'][0]['reported']
    assert evidence=={'usage':{'prompt_tokens':12,'completion_tokens':2,'cost':'0.000015'},'reported_cost_unit':'usd','credit_micro_usd':None}


def test_missing_cost_unit_preserves_reserve_and_does_not_call_credits_dollars(runtime_store):
    from backend.app.budgets.runtime import usage_from_response
    raw={'usage':{'prompt_tokens':12,'completion_tokens':2,'cost':'0.000015'}}
    assert usage_from_response(raw).cost_micro_usd is None
    assert usage_from_response(raw,cost_unit='credits').cost_micro_usd is None
    assert usage_from_response(raw,cost_unit='credits',credit_micro_usd=2000000).cost_micro_usd==30
    assert usage_from_response(raw,cost_unit='usd').cost_micro_usd==15


def test_free_first_uses_existing_allowed_references_and_skips_paid(runtime_store,repository):
    import io
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.providers.base import ChatMessage
    from backend.app.budgets.runtime import ProviderBudget
    from backend.app.orchestration.fallback import run_with_fallback
    runtime,_,_,_,_=runtime_store
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(json.loads(request.data)['model'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'free fixture'}}]}).encode())
    provider=OpenRouterProvider('synthetic-key','https://models.invalid/v1',opener=Opener(),
        budget=ProviderBudget(runtime,task_id=1,origin='legacy',role='manager'))
    outcome=run_with_fallback(role='manager',model_refs=['openrouter/fixture/paid','openrouter/fixture/free'],
        messages=[ChatMessage('user','synthetic')],timeout_seconds=1,max_attempts_per_model=10,
        repository=repository,round_id=None,openrouter=provider)
    assert outcome.success and calls==['fixture/free']


def test_parallel_tasks_cannot_spend_the_last_global_reservation_twice(runtime_store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier,Lock
    from backend.app.providers.base import ChatResult
    runtime,_,_,_,path=runtime_store
    runtime.conn.execute("INSERT INTO tasks(title,description,project_id) VALUES('Second','synthetic',1)")
    runtime.conn.commit()
    approve(runtime,'global',amount=111)
    approve(runtime,'project')
    approve(runtime,'task')
    runtime.approve(task_id=2,scope='task',amount_micro_usd=1000,allow_paid=True,reference='openrouter/fixture/paid',
        expected_revision=0,approval_id=uuid4().hex,actor='fixture-owner',expected_policy_sha256=runtime.policy().fingerprint)
    barrier=Barrier(2);lock=Lock();sent=[]
    def worker(task_id):
        with closing(connect(path)) as conn:
            account=RuntimeSpend(conn,runtime.settings,clock_ms=runtime.clock)
            barrier.wait(timeout=5)
            def send(payload,timeout):
                assert not conn.in_transaction
                with lock:sent.append(task_id)
                return ChatResult('openrouter','fixture/paid','synthetic',1,
                                  raw={'usage':{'prompt_tokens':1,'completion_tokens':1}})
            try:
                account.call(task_id=task_id,origin='legacy',role='manager',provider='openrouter',model='fixture/paid',
                    base_url='https://models.invalid/v1',messages=[{'role':'user','content':'synthetic'}],timeout_seconds=1,send=send)
                return 'sent'
            except SpendDenied as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,(1,2)))
    assert sorted(results)==['money_budget','sent'] and len(sent)==1
    assert runtime.ledger.report('runtime.global')['budget_committed_micro_usd']==111
    assert runtime.conn.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0]==1
    assert runtime.conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0]==3


def test_restart_holds_dispatched_work_and_releases_only_unclaimed_grants(runtime_store):
    runtime,_,_,_,_=runtime_store
    sent,route=prepare(runtime)
    runtime.claim(sent,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    unclaimed,_=prepare(runtime)
    runtime.recover_interrupted()
    assert runtime.permission(sent)['state']=='unknown'
    assert runtime.permission(unclaimed)['state']=='released'
    with pytest.raises(SpendDenied,match='outcome_unknown'):prepare(runtime)
    assert runtime.ledger.report('runtime.global')['states']=={'unknown':1}


def test_overrun_is_recorded_and_cannot_be_cleared_with_a_new_budget(runtime_store):
    runtime,_,_,_,_=runtime_store
    for scope in ('global','project','task'):approve(runtime,scope)
    operation,route=prepare(runtime,'fixture/paid')
    runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation,Outcome(kind='completed',http_status=200,
        usage=Usage(input_tokens=101,output_tokens=10,cost_micro_usd=112)))
    assert runtime.ledger.report('runtime.global')['overrun']
    with pytest.raises(SpendDenied,match='usage_overrun'):prepare(runtime)
    with pytest.raises(SpendDenied,match='usage_overrun'):approve(runtime,'global',amount=2000,revision=1)


def test_timeout_never_retries_or_changes_to_another_account_model(runtime_store,repository):
    import urllib.error
    from backend.app.providers.base import ChatMessage
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.budgets.runtime import ProviderBudget
    from backend.app.orchestration.fallback import run_with_fallback
    runtime,_,_,_,_=runtime_store
    calls=[]
    class Timeout:
        def open(self,*args,**kwargs):calls.append(1);raise urllib.error.URLError(TimeoutError())
    provider=OpenRouterProvider('synthetic-key','https://models.invalid/v1',opener=Timeout(),
        budget=ProviderBudget(runtime,task_id=1,origin='legacy',role='manager'))
    result=run_with_fallback(role='manager',model_refs=['openrouter/fixture/free','openrouter/fixture/paid'],
        messages=[ChatMessage('user','synthetic')],timeout_seconds=1,max_attempts_per_model=20,
        repository=repository,round_id=None,openrouter=provider)
    assert not result.success and len(calls)==1
    assert runtime.ledger.report('runtime.global')['states']=={'unknown':1}


def test_full_retry_after_is_preserved_for_quota_not_just_short_get_retry(runtime_store):
    import io
    import urllib.error
    from backend.app.providers.base import ChatMessage,ProviderError
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.budgets.runtime import ProviderBudget
    runtime,_,_,now,_=runtime_store
    class Quota:
        def open(self,*args,**kwargs):
            raise urllib.error.HTTPError('https://models.invalid',429,'synthetic',{'Retry-After':'600'},io.BytesIO(b''))
    provider=OpenRouterProvider('synthetic-key','https://models.invalid/v1',opener=Quota(),
        budget=ProviderBudget(runtime,task_id=1,origin='legacy',role='manager'))
    with pytest.raises(ProviderError):provider.chat('fixture/free',[ChatMessage('user','synthetic')],1)
    assert runtime.ledger.report('runtime.global')['cooldown_until_ms']==now[0]+600000
    with pytest.raises(SpendDenied,match='provider_cooldown'):prepare(runtime)


@pytest.mark.parametrize('mutation',['tariff','caps','gross','unit'])
def test_unknown_or_insufficient_metadata_never_grants_inference(runtime_store,mutation):
    runtime,document,path,now,_=runtime_store
    route=document['routes'][1]
    if mutation=='tariff':route['tariff']=None
    elif mutation=='caps':route['provider_caps_verified']=False
    elif mutation=='gross':route['upstream_tariff']['input_micro_usd_per_million']*=2
    else:route['reported_cost_unit']='unknown-dollars'
    path.write_text(json.dumps(document),encoding='utf-8')
    with pytest.raises(SpendDenied):prepare(runtime,'fixture/paid')
    assert runtime.conn.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0]==0


def test_expired_window_requires_owner_renewal_without_resetting_spend(runtime_store):
    runtime,document,path,now,_=runtime_store
    for scope in ('global','project','task'):approve(runtime,scope)
    operation,route=prepare(runtime,'fixture/paid')
    runtime.claim(operation,context_sha256=fingerprint({'task':1}),base_url=route.base_url)
    runtime.finish(operation,Outcome(kind='completed',http_status=200,usage=Usage(input_tokens=1,output_tokens=1,cost_micro_usd=3)))
    now[0]+=86400*1000
    document['verified_at_ms']=now[0]-1000
    document['valid_until_ms']=now[0]+86399000
    path.write_text(json.dumps(document),encoding='utf-8')
    for scope in ('global','project','task'):approve(runtime,scope,amount=1000,revision=1)
    report=runtime.ledger.report('runtime.global')
    assert report['created_ms']==now[0] and report['known_cost_subtotal_micro_usd']==3
    assert report['requests_committed']==1 and report['remaining_micro_usd']==997
    prepare(runtime,'fixture/paid')


def test_rotating_transport_key_cannot_reuse_another_account_permission(runtime_store):
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.providers.base import ChatMessage,ProviderError
    from backend.app.budgets.runtime import ProviderBudget
    runtime,_,_,_,_=runtime_store
    for scope in ('global','project','task'):approve(runtime,scope)
    class Closed:
        def open(self,*args,**kwargs):raise AssertionError('Different account dispatched')
    provider=OpenRouterProvider('another-synthetic-key','https://models.invalid/v1',opener=Closed(),
        budget=ProviderBudget(runtime,task_id=1,origin='legacy',role='manager'))
    with pytest.raises(ProviderError) as error:provider.chat('fixture/paid',[ChatMessage('user','synthetic')],1)
    assert error.value.budget_code=='account_policy_mismatch'
    assert runtime.conn.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0]==0


def test_unknown_schema_is_rejected_before_other_app_migrations(runtime_store):
    from backend.app.budgets.ledger import BudgetError
    runtime,_,_,_,_=runtime_store
    runtime.conn.execute('UPDATE spend_migrations SET version=42')
    runtime.conn.commit()
    before=runtime.conn.execute('SELECT name,sql FROM sqlite_schema ORDER BY name').fetchall()
    with pytest.raises(BudgetError,match='unsupported_runtime_budget_version'):init_db(runtime.conn)
    after=runtime.conn.execute('SELECT name,sql FROM sqlite_schema ORDER BY name').fetchall()
    assert before==after and runtime.conn.execute('SELECT version FROM spend_migrations').fetchone()[0]==42


def test_upstream_caps_are_not_mistaken_for_total_fee_inclusive_reserve(runtime_store):
    import io
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.providers.base import ChatMessage
    from backend.app.budgets.runtime import ProviderBudget
    runtime,document,path,_,_=runtime_store
    document['routes'][1]['upstream_tariff']={'input_micro_usd_per_million':500000,'output_micro_usd_per_million':500000,'request_micro_usd':0}
    path.write_text(json.dumps(document),encoding='utf-8')
    for scope in ('global','project','task'):approve(runtime,scope)
    class CheckCaps:
        def open(self,request,timeout):
            body=json.loads(request.data)
            assert body['provider']=={'allow_fallbacks':False,'require_parameters':True,'sort':'price',
                'max_price':{'prompt':0.5,'completion':0.5,'request':0,'image':0}}
            assert runtime.ledger.report('runtime.global')['budget_committed_micro_usd']==111
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'fixture'}}]}).encode())
    provider=OpenRouterProvider('synthetic-key','https://models.invalid/v1',opener=CheckCaps(),
        budget=ProviderBudget(runtime,task_id=1,origin='legacy',role='manager'))
    provider.chat('fixture/paid',[ChatMessage('user','fixture')],1)
    assert runtime.ledger.report('runtime.global')['budget_committed_micro_usd']==111


def test_stale_policy_during_claim_revokes_unsent_reservations(runtime_store,monkeypatch):
    from backend.app.providers.base import ChatResult
    runtime,document,path,_,_=runtime_store
    claim=runtime.claim
    def changed(*args,**kwargs):
        document['verified_at_ms']+=1
        path.write_text(json.dumps(document),encoding='utf-8')
        return claim(*args,**kwargs)
    monkeypatch.setattr(runtime,'claim',changed)
    def closed(*args):raise AssertionError('Stale policy dispatched')
    with pytest.raises(SpendDenied):runtime.call(task_id=1,origin='legacy',role='manager',provider='openrouter',model='fixture/free',
        base_url='https://models.invalid/v1',messages=[{'role':'user','content':'fixture'}],timeout_seconds=1,send=closed)
    assert runtime.conn.execute('SELECT state FROM spend_permissions').fetchone()[0]=='released'
    assert runtime.ledger.report('runtime.global')['requests_committed']==0
