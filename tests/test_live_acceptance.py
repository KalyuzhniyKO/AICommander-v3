"""The live acceptance harness is tested with fixtures; these are not live evidence."""
import json
import time
from hashlib import sha256
from backend.app.config import Settings
from backend.app.providers.omniroute import OmniRouteConfig
from backend.app.storage.db import connect
from backend.app.taskflow.ports import UncertainOperation
import subprocess
import sys

import pytest

from backend.app.taskflow.live_acceptance import run_acceptance
from test_hermes_taskflow import FixtureRPC, fixture_director

@pytest.fixture
def settings(tmp_path):
    now=time.time_ns()//1_000_000
    path=tmp_path/'policy.json'
    path.write_text(json.dumps({'schema_version':1,'verified_at_ms':now-1000,'valid_until_ms':now+86399000,
        'routes':[{'provider':'omniroute','base_url':'https://gateway.invalid','model_id':'synthetic/model',
                   'roles':['analyst','architect','developer','reviewer'],'account_sha256':sha256(b'fixture-private-key').hexdigest(),
                   'tariff':{'input_micro_usd_per_million':0,'output_micro_usd_per_million':0},
                   'fixed_route_verified':True,'billing_categories_verified':True,'provider_caps_verified':True}]}))
    return Settings(spend_policy_file=str(path),omniroute=OmniRouteConfig(enabled=True,base_url='https://gateway.invalid',api_key='fixture-private-key'))



def test_acceptance_runs_both_chains_reopens_in_fresh_process_and_limits_steps(tmp_path, settings):
    rpc=FixtureRPC()
    events=run_acceptance(fixture_director(rpc),tmp_path,settings)
    assert len(rpc.calls)==8
    completed=[e for e in events if e['check']=='pipeline_complete']
    assert [e['architect'] for e in completed]==[False,True]
    assert all(e['separate_process_reopen'] and e['replay_without_credentials'] for e in completed)
    assert events[-1]['resume_rejected'] and events[-1]['operations']==1


def test_failed_role_stops_acceptance_without_retry_or_next_role(tmp_path, settings):
    class FailedRPC(FixtureRPC):
        def __call__(self,config,payload,cancelled):
            self.calls.append(payload)
            raise RuntimeError('synthetic provider failure')
    rpc=FailedRPC()
    with pytest.raises(AssertionError,match='Role did not complete'):
        run_acceptance(fixture_director(rpc),tmp_path,settings)
    assert len(rpc.calls)==1


def test_existing_database_is_never_reused_or_overwritten(tmp_path, settings):
    path=tmp_path/'acceptance.sqlite3'
    path.write_bytes(b'existing data')
    with pytest.raises(ValueError,match='must be new'):
        run_acceptance(fixture_director(),tmp_path,settings)
    assert path.read_bytes()==b'existing data'


def test_cli_requires_explicit_opt_in_and_suppresses_input_credentials():
    command=[sys.executable,'-m','backend.app.taskflow.live_acceptance']
    result=subprocess.run(command,input='{}',text=True,capture_output=True,timeout=10)
    assert result.returncode==2
    result=subprocess.run(command+['--allow-inference'],input=json.dumps({'api_key':'private-test-credential'}),text=True,capture_output=True,timeout=10)
    assert result.returncode==1
    assert 'private-test-credential' not in result.stdout+result.stderr
    assert json.loads(result.stdout)['bridge_calls']==0


def test_acceptance_invariants_cannot_be_disabled_with_python_optimization():
    result=subprocess.run([sys.executable,'-O','-c',
        'from backend.app.taskflow.live_acceptance import _require; _require(False)'],
        text=True,capture_output=True,timeout=10)
    assert result.returncode==1
    assert 'Acceptance invariant failed' in result.stderr


def test_missing_policy_never_dispatches(tmp_path):
    rpc=FixtureRPC()
    with pytest.raises(Exception,match='pricing_policy_unavailable'):
        run_acceptance(fixture_director(rpc),tmp_path,Settings())
    assert rpc.calls == []


def test_unknown_stops_once_and_retains_financial_evidence(tmp_path, settings):
    class UnknownRPC(FixtureRPC):
        def __call__(self,*args):
            self.calls.append(args)
            raise UncertainOperation('synthetic')
    rpc=UnknownRPC()
    with pytest.raises(Exception):
        run_acceptance(fixture_director(rpc),tmp_path,settings)
    assert len(rpc.calls)==1
    conn=connect(tmp_path/'acceptance.sqlite3')
    try:
        assert conn.execute('SELECT state FROM spend_permissions').fetchone()[0]=='unknown'
        assert conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0]==3
    finally:conn.close()
    with pytest.raises(ValueError,match='must be new'):
        run_acceptance(fixture_director(rpc),tmp_path,settings)
    assert len(rpc.calls)==1


def test_unverified_price_never_becomes_free(tmp_path, settings):
    from pathlib import Path
    p=Path(settings.spend_policy_file);doc=json.loads(p.read_text());doc['routes'][0]['tariff']=None;p.write_text(json.dumps(doc))
    rpc=FixtureRPC()
    with pytest.raises(Exception,match='pricing_unknown'):
        run_acceptance(fixture_director(rpc),tmp_path,settings)
    assert rpc.calls==[]


def paid_policy(settings):
    from pathlib import Path
    p=Path(settings.spend_policy_file);doc=json.loads(p.read_text())
    doc.update(global_ceiling_micro_usd=10000,project_ceiling_micro_usd=10000,task_ceiling_micro_usd=10000)
    doc['routes'][0]['tariff']={'input_micro_usd_per_million':0,'output_micro_usd_per_million':0,'request_micro_usd':10}
    p.write_text(json.dumps(doc))


def test_paid_route_requires_explicit_ceiling_before_dispatch(tmp_path,settings):
    paid_policy(settings);rpc=FixtureRPC()
    with pytest.raises(ValueError,match='Explicit paid budget required'):
        run_acceptance(fixture_director(rpc),tmp_path,settings)
    assert rpc.calls==[]


def test_paid_ceiling_is_shared_across_chains_and_unknown_cost_stays_null(tmp_path,settings):
    paid_policy(settings);rpc=FixtureRPC()
    run_acceptance(fixture_director(rpc),tmp_path,settings,approved_micro_usd=80)
    conn=connect(tmp_path/'acceptance.sqlite3')
    try:
        from backend.app.budgets.runtime import RuntimeSpend
        account=RuntimeSpend(conn,settings).status()['accounts'][0]
        assert account['budget_committed_micro_usd']==80
        assert account['reported_cost_micro_usd'] is None
        assert account['dispatches_claimed']==8
        assert conn.execute("SELECT COUNT(*) FROM spend_approvals WHERE scope_id='runtime.global'").fetchone()[0]==1
    finally:conn.close()
