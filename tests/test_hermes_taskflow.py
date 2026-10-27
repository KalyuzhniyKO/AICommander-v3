"""Fixture bridge tests real-mode contracts, not live Hermes evidence."""
from dataclasses import replace
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from threading import Event

import pytest

from backend.app.providers.omniroute import OmniRouteConfig
from backend.app.storage.db import connect
from backend.app.taskflow.hermes import HermesDirector
from backend.app.taskflow.hermes_config import HermesConfig
from backend.app.taskflow.models import HermesCreateRun, CreateRun, Command, OwnerDecision
from backend.app.taskflow.operations import StepOperations
from backend.app.taskflow.ports import UncertainOperation
from backend.app.taskflow.service import Taskflow, FlowError


@pytest.fixture(autouse=True)
def declared_free_sdk_policy(tmp_path, monkeypatch):
    import time
    from backend.app import main
    from backend.app.budgets.runtime import RuntimeSpend
    now=time.time_ns()//1_000_000
    path=tmp_path/'sdk-spend-policy.json'
    path.write_text(json.dumps({'schema_version':1,'verified_at_ms':now-1000,'valid_until_ms':now+86399000,
        'routes':[{'provider':'omniroute','base_url':'https://gateway.invalid','model_id':'synthetic/model',
                   'roles':['analyst','architect','developer','reviewer'],'account_sha256':sha256(b'fixture-private-key').hexdigest(),
                   'tariff':{'input_micro_usd_per_million':0,'output_micro_usd_per_million':0},
                   'fixed_route_verified':True,'billing_categories_verified':True,'provider_caps_verified':True}]}),encoding='utf-8')
    settings=replace(main.settings,spend_policy_file=str(path),omniroute=OmniRouteConfig(enabled=True,base_url='https://gateway.invalid',api_key='fixture-private-key'))
    monkeypatch.setattr(main,'settings',settings)
    original=FixtureDirector.reserve_budget
    def reserve(self,conn,**kwargs):
        if self.spending is None:self.spending=RuntimeSpend(conn,settings)
        return original(self,conn,**kwargs)
    monkeypatch.setattr(FixtureDirector,'reserve_budget',reserve)


class FixtureRPC:
    def __init__(self):self.calls=[]
    def __call__(self, config, payload, cancelled):
        self.calls.append(payload)
        assert not cancelled()
        schema=payload['output_schema']
        assert schema['additionalProperties'] is False
        assert set(schema['properties'])==set(schema['required'])
        assert set(schema['required'])=={'analyst':{'objective','acceptance_criteria'},'architect':{'components'},
            'developer':{'steps'},'reviewer':{'verdict','findings'}}[payload['role']]
        role=payload['role']
        data={'analyst':{'objective':'synthetic acceptance flow','acceptance_criteria':['Reject an invalid quantity']},
              'architect':{'components':['validation boundary']},
              'developer':{'steps':['Implement validation from the supplied requirements']},
              'reviewer':{'verdict':'approve','findings':['Plan addresses the acceptance criterion; tests have not run']}}[role]
        return {'ok':True,'summary':json.dumps(data),'provenance':{
            'origin':'hermes','operation_id':payload['operation_id'],'model_id':config.model,'provider':'custom',
            'route':'omniroute','sdk_commit':config.expected_commit,'parent_session_id':'fixture-parent',
            'child_session_id':'fixture-child','tools_count':0,'inference_requests':1,
            'input_sha256':sha256(payload['context'].encode()).hexdigest()}}


class FixtureDirector(HermesDirector):
    @property
    def available(self):return True


def fixture_director(rpc=None):
    return FixtureDirector(HermesConfig(model='synthetic/model',expected_commit='f'*40),
        OmniRouteConfig(enabled=True,base_url='https://gateway.invalid',api_key='fixture-private-key'),runner=rpc or FixtureRPC())


def prepare(repository,director,architect=False,max_steps=6):
    flow=Taskflow(repository.conn,director,mode='hermes')
    task=repository.create_task('synthetic owner goal')
    run=flow.create(task['id'],HermesCreateRun(idempotency_key='create-real',include_architect=architect,max_steps=max_steps))
    run=flow.apply(run.run_id,Command(idempotency_key='start-real',expected_version=1,action='start'))
    return flow,run


def step(flow,run,key=None):
    return flow.apply(run.run_id,Command(idempotency_key=key or 'step-'+str(run.version),expected_version=run.version,action='step'))


@pytest.mark.parametrize('architect',[False,True])
def test_typed_real_handoff_optional_architect_and_owner_approval(repository,architect):
    rpc=FixtureRPC();director=fixture_director(rpc);flow,run=prepare(repository,director,architect)
    roles=['analyst','architect','developer','reviewer'] if architect else ['analyst','developer','reviewer']
    assert [s.role for s in run.stages]==roles
    for role in roles:
        before=run;run=step(flow,run)
        assert run.artifacts[-1].role==role
        assert run.artifacts[-1].input_artifact_ids==[a.artifact_id for a in before.artifacts]
        sent=json.loads(rpc.calls[-1]['context'])
        assert [a['content'] for a in sent['artifacts']]==[a.content.model_dump() for a in before.artifacts]
        assert run.artifacts[-1].provenance.operation_id==rpc.calls[-1]['operation_id']
    assert run.status=='awaiting_owner' and run.director=='hermes' and run.simulated is False
    assert run.schema_version=='2.0' and run.code_executed is False and run.qa_verified is False
    plan=next(a for a in run.artifacts if a.type=='plan').content
    assert plan.requirements==run.artifacts[0].content and plan.execution=='not_executed'
    assert run.artifacts[-1].content.plan==plan and run.artifacts[-1].content.scope=='planning_only'
    assert (plan.design is not None)==architect
    saved=flow.apply(run.run_id,OwnerDecision(idempotency_key='accept',expected_version=run.version,decision='approve'))
    assert saved.status=='done' and saved.completion_scope=='real_planning_artifacts_only'
    assert len(rpc.calls)==len(roles)
    assert flow.apply(run.run_id,Command(idempotency_key='step-'+str(before.version),expected_version=before.version,action='step'))==run
    assert len(rpc.calls)==len(roles)


def test_saved_real_result_can_finish_after_sql_failure_without_another_inference(repository):
    rpc=FixtureRPC();flow,run=prepare(repository,fixture_director(rpc))
    repository.conn.execute("CREATE TRIGGER fail_real BEFORE INSERT ON taskflow_events WHEN NEW.action='step' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):step(flow,run)
    assert StepOperations(flow).list_for_run(run.run_id)[0]['state']=='result_ready'
    repository.conn.execute('DROP TRIGGER fail_real')
    result=step(flow,run)
    assert len(result.artifacts)==1 and len(rpc.calls)==1 and result.artifacts[0].simulated is False


def test_unknown_timeout_does_not_retry_and_operator_failure_counts_once(repository):
    class TimedOut(FixtureRPC):
        def __call__(self,*args):self.calls.append(args);raise UncertainOperation('private diagnostics must not escape')
    rpc=TimedOut();flow,run=prepare(repository,fixture_director(rpc))
    with pytest.raises(FlowError) as exc:step(flow,run)
    assert exc.value.code=='operation_unknown' and len(rpc.calls)==1
    with pytest.raises(FlowError):step(flow,run)
    assert len(rpc.calls)==1
    ops=StepOperations(flow);operation=ops.list_for_run(run.run_id)[0]
    assert operation['state']=='unknown' and flow.get(run.run_id).steps_done==0
    with pytest.raises(FlowError) as exc:ops.resolve_unknown(operation['id'],result={'objective':'fake','acceptance_criteria':['fake']})
    assert exc.value.code=='provenance_required'
    failed=ops.resolve_unknown(operation['id'])
    assert failed.status=='failed' and failed.steps_done==1 and failed.artifacts==[]


def test_cancel_terminates_context_producer_and_discards_uncertain_result(repository,tmp_path):
    entered=Event()
    class WaitForCancel(FixtureRPC):
        def __call__(self,config,payload,cancelled):
            import time
            self.calls.append(payload);entered.set()
            for _ in range(100):
                if cancelled():raise UncertainOperation('cancelled in transport')
                time.sleep(.02)
            raise AssertionError('Cancellation did not arrive')
    rpc=WaitForCancel();flow,run=prepare(repository,fixture_director(rpc))
    with closing(connect(tmp_path/'repository.db')) as conn,ThreadPoolExecutor(max_workers=1) as pool:
        worker=Taskflow(conn,fixture_director(rpc),mode='hermes')
        future=pool.submit(step,worker,run)
        assert entered.wait(5)
        cancelled=flow.apply(run.run_id,Command(idempotency_key='cancel-real',expected_version=run.version,action='cancel'))
        assert future.result(timeout=5)==cancelled
    assert cancelled.status=='cancelled' and cancelled.artifacts==[] and len(rpc.calls)==1
    assert StepOperations(flow).list_for_run(run.run_id)[0]['state']=='discarded'


def test_route_is_bound_to_run_and_different_mode_cannot_relabel_artifacts(repository):
    director=fixture_director();flow,run=prepare(repository,director)
    changed=fixture_director();changed.config=replace(changed.config,model='synthetic/other')
    with pytest.raises(FlowError) as exc:step(Taskflow(repository.conn,changed,mode='hermes'),run)
    assert exc.value.code=='director_config_changed'
    with pytest.raises(FlowError) as exc:Taskflow(repository.conn).get(run.run_id)
    assert exc.value.code=='director_mode_mismatch'
    assert Taskflow(repository.conn).list_for_task(run.task_id)==[]


def test_step_limit_blocks_real_pipeline_and_resume_does_not_bypass_it(repository):
    rpc=FixtureRPC();flow,run=prepare(repository,fixture_director(rpc),max_steps=1)
    run=step(flow,run)
    assert run.status=='blocked' and run.steps_done==1 and len(rpc.calls)==1
    with pytest.raises(FlowError) as exc:
        flow.apply(run.run_id,Command(idempotency_key='resume',expected_version=run.version,action='resume'))
    assert exc.value.code=='step_limit' and len(rpc.calls)==1


def test_v2_api_is_explicit_authenticated_and_v1_remains_mock(client,monkeypatch):
    from backend.app.taskflow import hermes_api
    capabilities=client.get('/taskflow/v2/capabilities').json()
    assert capabilities['configured'] is False
    task=client.post('/tasks',json={'description':'synthetic'}).json()
    path=f"/taskflow/v2/tasks/{task['id']}/runs"
    assert client.post(path,json={'idempotency_key':'disabled'}).status_code==503
    rpc=FixtureRPC();monkeypatch.setattr(hermes_api,'HermesDirector',lambda *args:fixture_director(rpc))
    response=client.post(path,json={'idempotency_key':'real','include_architect':False})
    assert response.status_code==200 and response.json()['simulated'] is False
    run=response.json();base=f"/taskflow/v2/runs/{run['run_id']}"
    assert client.get(base,headers={'Authorization':''}).status_code==401
    assert client.post(base+'/commands',headers={'X-AICommander-Request':''},json={'idempotency_key':'start','expected_version':1,'action':'start'}).status_code==403
    for action,version in [('start',1),('step',2),('step',3),('step',4)]:
        result=client.post(base+'/commands',json={'idempotency_key':action+str(version),'expected_version':version,'action':action})
        assert result.status_code==200
    final=result.json();assert final['status']=='awaiting_owner' and len(final['artifacts'])==3
    assert client.get(f"/taskflow/runs/{run['run_id']}").status_code==409
    assert client.get(f"/taskflow/tasks/{task['id']}/runs").json()==[]
    assert client.get('/tasks').json()[0]['flow_status'] is None
    assert client.get(base+'/operations').json()[-1]['role']=='reviewer'
    accepted=client.post(base+'/owner-decision',json={'idempotency_key':'owner','expected_version':final['version'],'decision':'approve'})
    assert accepted.json()['status']=='done' and accepted.json()['qa_verified'] is False


def test_published_v2_contract_matches_validated_models():
    from backend.app.taskflow.models import HermesRunSnapshot
    contracts=Path(__file__).resolve().parents[1]/'docs'/'contracts'
    assert json.loads((contracts/'taskflow-v2.schema.json').read_text())==HermesRunSnapshot.model_json_schema()
    requests=json.loads((contracts/'taskflow-v2.requests.json').read_text())
    assert requests=={m.__name__:m.model_json_schema() for m in (HermesCreateRun,Command,OwnerDecision)}


def test_budget_reservation_and_step_operation_insert_rollback_together(repository):
    rpc=FixtureRPC();flow,run=prepare(repository,fixture_director(rpc))
    repository.conn.execute("CREATE TRIGGER abort_operation BEFORE INSERT ON taskflow_operations BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):step(flow,run)
    assert rpc.calls==[]
    assert repository.conn.execute('SELECT COUNT(*) FROM spend_permissions').fetchone()[0]==0
    assert repository.conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0]==0


def test_budget_outcome_and_saved_model_result_rollback_together(repository):
    rpc=FixtureRPC();director=fixture_director(rpc);flow,run=prepare(repository,director)
    repository.conn.execute("CREATE TRIGGER abort_result BEFORE UPDATE ON taskflow_operations WHEN NEW.state='result_ready' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):step(flow,run)
    assert len(rpc.calls)==1
    assert repository.conn.execute('SELECT state FROM spend_permissions').fetchone()[0]=='dispatched'
    assert repository.conn.execute('SELECT state FROM taskflow_operations').fetchone()[0]=='running'
    director.spending.recover_interrupted()
    assert repository.conn.execute('SELECT state FROM spend_permissions').fetchone()[0]=='unknown'
    assert director.spending.ledger.report('runtime.global')['states']=={'unknown':1}
    with pytest.raises(FlowError):step(flow,run)
    assert len(rpc.calls)==1
