"""Approved disposable Python fixtures test containment; no installed Hermes needed."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time

import pytest

from backend.app.taskflow import hermes
from backend.app.taskflow.hermes_config import HermesConfig
from backend.app.taskflow.ports import UncertainOperation


pytestmark=pytest.mark.skipif(os.name!='posix',reason='Live Hermes bridge targets the configured POSIX/Zorin host')


def script(tmp_path,monkeypatch,body):
    path=tmp_path/'hermes_bridge.py';path.write_text(body)
    monkeypatch.setattr(hermes,'__file__',str(tmp_path/'hermes.py'))
    return HermesConfig(python=sys.executable,timeout_seconds=2)


def test_secret_uses_stdin_not_argv_env_or_profile_file(tmp_path,monkeypatch):
    config=script(tmp_path,monkeypatch,"""
import sys,json,os
from pathlib import Path
d=json.load(sys.stdin)
print(json.dumps({'ok':True,'secret_in_argv':d['api_key'] in repr(sys.argv),
 'secret_in_env':d['api_key'] in repr(dict(os.environ)),
 'unrelated_secret_in_env':'UNRELATED_API_KEY' in os.environ,
 'scratch_exists':Path(os.environ['HERMES_HOME']).is_dir()}))
""")
    monkeypatch.setenv('UNRELATED_API_KEY','must-not-be-inherited')
    result=hermes.run_bridge(config,{'api_key':'synthetic-stdin-only'},lambda:False)
    assert result=={'ok':True,'secret_in_argv':False,'secret_in_env':False,'unrelated_secret_in_env':False,'scratch_exists':True}


@pytest.mark.parametrize('kind',['timeout','cancel','output_limit','bad_json'])
def test_incomplete_transport_results_are_uncertain_and_do_not_echo_output(tmp_path,monkeypatch,kind,capsys):
    body={'timeout':'import time;time.sleep(20)',
          'cancel':'import time;time.sleep(20)',
          'output_limit':"import sys;sys.stdout.write('synthetic-private-data'*20000);sys.stdout.flush()",
          'bad_json':"print('synthetic-private-data')"}[kind]
    config=script(tmp_path,monkeypatch,body)
    if kind=='timeout':config=replace(config,timeout_seconds=1)
    start=time.monotonic()
    cancelled=(lambda:time.monotonic()-start>.2) if kind=='cancel' else (lambda:False)
    with pytest.raises(UncertainOperation) as exc:hermes.run_bridge(config,{},cancelled)
    assert time.monotonic()-start<5
    assert 'synthetic-private-data' not in str(exc.value)
    assert capsys.readouterr().out==''


def test_known_preflight_failure_and_prelaunch_cancel_are_not_reported_as_dispatch(tmp_path,monkeypatch):
    config=script(tmp_path,monkeypatch,"print('{\"ok\":false,\"dispatched\":false}')")
    with pytest.raises(RuntimeError,match='preflight'):hermes.run_bridge(config,{},lambda:False)
    with pytest.raises(RuntimeError,match='before dispatch'):hermes.run_bridge(config,{},lambda:True)


def test_config_env_bounds_do_not_echo_invalid_values(monkeypatch):
    monkeypatch.setenv('HERMES_ENABLED','true');monkeypatch.setenv('HERMES_TIMEOUT_SECONDS','secret-invalid-timeout')
    with pytest.raises(ValueError) as exc:HermesConfig.from_env()
    assert str(exc.value)=='Invalid Hermes configuration'


@pytest.mark.parametrize('violation',[None,'context','model','schema','repeat','spend_tokens','spend_payload','multiplier','fallback','server_tools'])
def test_bridge_checks_actual_http_payload_before_dispatch(tmp_path,violation):
    """Execute the real bootstrap against an approved fake SDK and MockTransport."""
    source=tmp_path/'sdk';(source/'pm').mkdir(parents=True);(source/'tools').mkdir()
    (source/'pm'/'__init__.py').write_text('')
    (source/'tools'/'__init__.py').write_text('')
    (source/'pm'/'environments.py').write_text('def selected_venv(source):return source\ndef site_packages(root):return root\n')
    (source/'install-stamp.json').write_text(json.dumps({'commit':'f'*40}))
    (source/'run_agent.py').write_text('''
import httpx
from pathlib import Path
def reply(request):
 with Path(__file__).with_name("http-dispatches").open("a") as trace:trace.write(request.method+"\\n")
 return httpx.Response(200,stream=httpx.ByteStream(b'{"usage":{"prompt_tokens":3,"completion_tokens":2,"cost":"0.000001","private":"must-not-export"}}'))
class AIAgent:
 count=0
 def __init__(self,**kwargs):
  assert all(kwargs.get(k) is True for k in ('skip_context_files','skip_memory','skip_background_review'))
  assert kwargs.get('save_trajectories') is False and kwargs.get('load_soul_identity') is False
  self.kwargs=kwargs;self.__dict__.update(kwargs);self.tools=[];self._client_kwargs={}
  AIAgent.count+=1;self.session_id='fixture-'+str(AIAgent.count)
  self.client=httpx.Client(transport=httpx.MockTransport(reply))
  # Optional SDK metadata lookup must be denied but remain catchable during init.
  try:self.client.get(self.base_url+'/unsupported-metadata')
  except Exception:pass
 def close(self):self.client.close()
''')
    (source/'tools'/'delegation_output_schema.py').write_text('def append_output_contract(context,schema):return context\n')
    (source/'tools'/'delegate_tool.py').write_text('''
import json
from run_agent import AIAgent
VIOLATION = '''+repr(violation)+'''
def delegate_task(goal,context,parent_agent,background):
 child=AIAgent(**parent_agent.kwargs)
 body={'model':child.model,'messages':[{'role':'user','content':context}],
       'response_format':child.request_overrides['response_format'],'max_tokens':child.max_tokens}
 if VIOLATION=='context':body['messages']=[]
 if VIOLATION=='model':body['model']='synthetic/other'
 if VIOLATION=='schema':body.pop('response_format')
 if VIOLATION=='spend_tokens':body['max_tokens']*=2
 if VIOLATION=='spend_payload':body['messages'][0]['content']+='x'*20000
 if VIOLATION=='multiplier':body['n']=2
 if VIOLATION=='fallback':body['models']=['expensive/other']
 if VIOLATION=='server_tools':body['functions']=[{'name':'expensive-tool'}]
 child.client.post(child.base_url+'/chat/completions',json=body)
 if VIOLATION=='repeat':child.client.post(child.base_url+'/chat/completions',json=body)
 return json.dumps({'results':[{'status':'completed','summary':'{}'}]})
''')
    config=HermesConfig(python=sys.executable,timeout_seconds=10)
    payload={'source':str(source),'expected_commit':'f'*40,'base_url':'http://127.0.0.1:9/v1',
             'api_key':'synthetic-private-stdin','model':'synthetic/model','max_tokens':64,
             'budget_seconds':8,'operation_id':'fixture-operation','instruction':'synthetic',
             'context':'exact synthetic handoff','output_schema':hermes.output_schema('analyst'),
             'spend_caps':{'max_payload_bytes':16384,'max_output_tokens':64}}
    if violation is None:
        result=hermes.run_bridge(config,payload,lambda:False)
        assert result['usage']=={'prompt_tokens':3,'completion_tokens':2,'cost':'0.000001'}
        assert result['provenance']['inference_requests']==1
        assert result['provenance']['parent_session_id']!=result['provenance']['child_session_id']
    else:
        expected=UncertainOperation if violation=='repeat' else RuntimeError
        with pytest.raises(expected) as exc:hermes.run_bridge(config,payload,lambda:False)
        assert payload['api_key'] not in str(exc.value)
    trace=source/'http-dispatches'
    sent=trace.read_text().splitlines() if trace.exists() else []
    assert sent==(['POST'] if violation in (None,'repeat') else [])


@pytest.mark.parametrize('error',[OSError,RuntimeError])
def test_cancellation_probe_failure_after_launch_is_uncertain(tmp_path,monkeypatch,error):
    config=script(tmp_path,monkeypatch,'import time;time.sleep(20)')
    calls=0
    def broken_probe():
        nonlocal calls
        calls+=1
        if calls>1:raise error('synthetic-private-database-error')
        return False
    with pytest.raises(UncertainOperation) as exc:
        hermes.run_bridge(config,{},broken_probe)
    assert 'synthetic-private-database-error' not in str(exc.value)


def test_stream_usage_keeps_only_confirmed_counters_and_never_exports_private_fields():
    from backend.app.taskflow.hermes_bridge import _usage
    chunks=[{'overflow':False,'data':bytearray(b'data: {"choices":[]}\n\ndata: {"usage":{"prompt_tokens":4,"completion_tokens":2,"cost":0,"private":"hidden"}}\n\ndata: [DONE]\n')}]
    assert _usage(chunks)=={'prompt_tokens':4,'completion_tokens':2,'cost':'0'}
    chunks[0]['overflow']=True
    assert _usage(chunks) is None


def test_sdk_quota_preserves_structured_retry_after_without_replay(tmp_path,monkeypatch):
    config=script(tmp_path,monkeypatch,"print('{\"ok\":false,\"dispatched\":true,\"http_status\":429,\"retry_after_seconds\":600}')")
    with pytest.raises(UncertainOperation) as error:hermes.run_bridge(config,{},lambda:False)
    assert error.value.budget_outcome.kind=='quota'
    assert error.value.budget_outcome.retry_after_seconds==600
