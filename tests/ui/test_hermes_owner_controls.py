"""Owner planning controls over real runtime/SQLite and a synthetic upstream RPC."""
from dataclasses import replace
from hashlib import sha256
import json
import os
import sys
from pathlib import Path
import time

import pytest

pytestmark=pytest.mark.skipif(os.environ.get('RUN_UI_TESTS')!='1' or os.name!='posix',reason='POSIX Chromium planning fixture')


@pytest.fixture
def planner_app(tmp_path,monkeypatch):
    from backend.app import main
    from backend.app.taskflow import hermes_api
    from backend.app.taskflow.hermes import HermesDirector
    from backend.app.taskflow.hermes_config import HermesConfig
    from backend.app.providers.omniroute import OmniRouteConfig
    sdk=tmp_path/'synthetic-sdk';sdk.mkdir();(sdk/'run_agent.py').write_text('# Never executed; fixture RPC replaces upstream only.\n')
    cfg=HermesConfig(enabled=True,python=sys.executable,source=str(sdk),model='fixture/planner',expected_commit='f'*40,timeout_seconds=2,max_output_tokens=64)
    omni=OmniRouteConfig(enabled=True,base_url='https://gateway.invalid',api_key='synthetic-planner-key',max_output_tokens=64)
    now=time.time_ns()//1000000
    policy=tmp_path/'planner-price.json'
    policy.write_text(json.dumps({'schema_version':1,'verified_at_ms':now-1000,'valid_until_ms':now+86399000,
        'routes':[{'provider':'omniroute','base_url':omni.base_url,'model_id':cfg.model,'roles':['analyst','architect','developer','reviewer'],
                   'account_sha256':sha256(omni.api_key.encode()).hexdigest(),'tariff':{'input_micro_usd_per_million':0,'output_micro_usd_per_million':0},
                   'reported_cost_unit':'usd','fixed_route_verified':True,'billing_categories_verified':True,'provider_caps_verified':True,
                   'max_output_tokens':64,'timeout_seconds':2}]}),encoding='utf-8')
    monkeypatch.setattr(main,'settings',replace(main.settings,hermes=cfg,omniroute=omni,spend_policy_file=str(policy)))
    calls=[]
    def rpc(config,payload,cancelled):
        calls.append(payload['role'])
        content={'analyst':{'objective':'Synthetic planning','acceptance_criteria':['Validate a fixture quantity']},
                 'architect':{'components':['Fixture validation boundary']},'developer':{'steps':['Prepare the fixture implementation plan']},
                 'reviewer':{'verdict':'approve','findings':['Planning fixture only; no code or QA execution']}}[payload['role']]
        return {'ok':True,'summary':json.dumps(content),'usage':{'prompt_tokens':1,'completion_tokens':1,'cost':0},
            'provenance':{'origin':'hermes','operation_id':payload['operation_id'],'model_id':config.model,'provider':'custom',
                'route':'omniroute','sdk_commit':config.expected_commit,'parent_session_id':'fixture-parent','child_session_id':'fixture-child',
                'tools_count':0,'inference_requests':1,'input_sha256':sha256(payload['context'].encode()).hexdigest()}}
    monkeypatch.setattr(hermes_api,'HermesDirector',lambda config,gateway:HermesDirector(config,gateway,runner=rpc))
    return calls


@pytest.fixture
def owner_page(planner_app,browser_page):
    page,expect=browser_page
    page.locator('#taskText').fill('Synthetic owner planning task')
    page.locator('#taskForm button').click()
    page.locator('#flowMode').select_option('hermes')
    expect(page.locator('#newRun')).to_be_enabled()
    return page,expect,planner_app


def create_start(page,expect):
    page.locator('#newRun').click()
    expect(page.locator('#runTask')).to_be_enabled()
    page.locator('#runTask').click()
    expect(page.locator('#stepTask')).to_be_enabled()


def test_owner_can_plan_pause_resume_and_approve_without_terminal(owner_page,monkeypatch):
    page,expect,calls=owner_page
    page.locator('#includeArchitect').check()
    expect(page.locator('#maxSteps')).to_have_value('4')
    create_start(page,expect)
    assert calls==[]
    page.locator('#pauseTask').click()
    expect(page.locator('#runTask')).to_be_enabled()
    assert calls==[]
    page.locator('#runTask').click()
    for count in range(1,5):
        page.locator('#stepTask').click()
        expect(page.locator('#artifacts > details')).to_have_count(count)
    assert calls==['analyst','architect','developer','reviewer']
    from backend.app import main
    monkeypatch.setattr(main,'settings',replace(main.settings,hermes=replace(main.settings.hermes,enabled=False)))
    page.locator('#refreshTasks').click()
    expect(page.locator('#newRun')).to_be_hidden()
    expect(page.locator('#approveTask')).to_have_text('Согласовать план')
    expect(page.locator('#approveTask')).to_be_enabled()
    expect(page.locator('#actionHint')).to_contain_text('не подтверждает код или тесты')
    page.locator('#approveTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('План согласован')
    run_id=page.locator('#runSelect').input_value()
    result=page.request.get('/taskflow/v2/runs/'+run_id).json()
    assert result['status']=='done' and result['code_executed'] is False and result['qa_verified'] is False
    assert result['completion_scope']=='real_planning_artifacts_only'
    assert len(calls)==4
    assert all(a['dispatches_claimed']==4 for a in page.request.get('/spending/tasks/1').json()['accounts'])
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#approveTask')).to_have_text('Approve plan')
    if os.environ.get('OWNER_CONTROL_SCREENSHOTS'):
        folder=Path(os.environ['OWNER_CONTROL_SCREENSHOTS']);folder.mkdir(parents=True,exist_ok=True)
        page.locator('#taskDetail').screenshot(path=str(folder/'planning-owner-en.png'))


def test_owner_step_cannot_bypass_default_paid_deny(owner_page,monkeypatch):
    from backend.app import main
    page,expect,calls=owner_page
    monkeypatch.setattr(main,'settings',replace(main.settings,spend_policy_file=''))
    create_start(page,expect)
    page.locator('#stepTask').click()
    expect(page.locator('#status')).to_contain_text('Запрос модели не отправлен')
    assert calls==[]
    run_id=page.locator('#runSelect').input_value()
    assert page.request.get('/taskflow/v2/runs/'+run_id+'/operations').json()==[]
    expect(page.locator('#spendStatus')).to_contain_text('Стоимость неизвестна')
    page.locator('#stopTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Отменён')
    assert calls==[]


def test_lost_model_step_response_replays_same_command_without_new_inference(owner_page):
    page,expect,calls=owner_page
    create_start(page,expect)
    bodies=[]
    def lose(route):
        body=route.request.post_data_json
        if body.get('action')!='step':route.continue_();return
        bodies.append(body)
        if len(bodies)==1:
            assert route.fetch().ok
            route.abort()
        else:route.continue_()
    page.route('**/taskflow/v2/runs/*/commands',lose)
    page.locator('#stepTask').click()
    expect(page.locator('#retryRequest')).to_be_visible()
    assert calls==['analyst']
    page.locator('#retryRequest').click()
    expect(page.locator('#artifacts > details')).to_have_count(1)
    assert len(bodies)==2 and bodies[0]==bodies[1] and calls==['analyst']


def test_capability_failure_hides_new_model_controls_but_keeps_cancel(owner_page):
    page,expect,calls=owner_page
    create_start(page,expect)
    page.route('**/taskflow/v2/capabilities',lambda route:route.fulfill(status=503,json={'detail':'fixture unavailable'}))
    page.locator('#refreshTasks').click()
    expect(page.locator('#modeHint')).to_contain_text('не подтверждены')
    expect(page.locator('#newRun')).to_be_hidden()
    expect(page.locator('#stepTask')).to_be_hidden()
    expect(page.locator('#stopTask')).to_be_enabled()
    page.locator('#stopTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Отменён')
    assert calls==[]


def test_unknown_operation_blocks_new_model_actions_until_explicit_cancel(owner_page):
    from contextlib import closing
    from uuid import uuid4
    from backend.app import main
    from backend.app.storage.db import connect
    from backend.app.taskflow.service import Taskflow
    page,expect,calls=owner_page
    create_start(page,expect)
    run_id=int(page.locator('#runSelect').input_value())
    with closing(connect(main.settings.database_path)) as conn:
        snapshot=Taskflow(conn,mode='hermes').get(run_id)
        conn.execute("""INSERT INTO taskflow_operations(id,run_id,key_hash,request_hash,expected_version,role,goal,input_json,reason,state,worker_hash)
            VALUES(?,?,?,?,?,'analyst','fixture',?,'fixture','unknown','')""",(uuid4().hex,run_id,'fixture-key','fixture-request',snapshot.version,snapshot.model_dump_json()))
        conn.commit()
    page.locator('#refreshTasks').click()
    expect(page.locator('#operations')).to_contain_text('Исход неизвестен')
    expect(page.locator('#stepTask')).to_be_disabled()
    expect(page.locator('#runTask')).to_be_disabled()
    expect(page.locator('#newRun')).to_be_disabled()
    expect(page.locator('#stopTask')).to_be_enabled()
    page.locator('#stopTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Отменён')
    assert calls==[]
