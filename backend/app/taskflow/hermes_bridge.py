"""Executed by the owner's installed Hermes Python. No source is copied from Hermes."""
import contextlib
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import json
import os
import re
import signal
from pathlib import Path
import socket
import sys
import threading
from urllib.parse import urlsplit


class BridgePolicyError(BaseException):
    """Abort a policy violation without SDK retry/error-recovery loops."""


class _QuietBuffer(io.StringIO):
    def write(self,text):return len(text)
    def flush(self):pass


def _usage(captures):
    result=None
    for capture in captures:
        if capture['overflow']:continue
        raw=bytes(capture['data'])
        values=[]
        try:values.append(json.loads(raw))
        except (ValueError,UnicodeError):
            for line in raw.splitlines():
                if line.startswith(b'data:'):
                    try:values.append(json.loads(line[5:].strip()))
                    except (ValueError,UnicodeError):pass
        for value in values:
            usage=value.get('usage') if isinstance(value,dict) else None
            if not isinstance(usage,dict):continue
            incoming,outgoing=usage.get('prompt_tokens'),usage.get('completion_tokens')
            if any(type(n) is not int or not 0 <= n <= 10**9 for n in (incoming,outgoing)):continue
            result={'prompt_tokens':incoming,'completion_tokens':outgoing}
            cost=usage.get('cost')
            if type(cost) in (str,int,float):
                try:
                    number=Decimal(str(cost))
                    if number.is_finite() and 0 <= number <= 9*10**9:result['cost']=str(number)
                except InvalidOperation:pass
    return result


def main():
    dispatched={'count':0,'http_status':None,'request_check':None}
    agents=[]
    captures=[]
    phase='input'
    try:
        request=json.loads(sys.stdin.buffer.read(1024*1024+1))
        def expire():
            if os.getpgrp()==os.getpid():os.killpg(os.getpgrp(),signal.SIGKILL)
            os._exit(124)
        watchdog=threading.Timer(min(125,max(1,int(request['budget_seconds'])+5)),expire)
        watchdog.daemon=True;watchdog.start()
        source=Path(request['source'])
        phase='revision'
        stamp=json.loads((source/'install-stamp.json').read_text())
        if stamp.get('commit')!=request['expected_commit']:
            raise ValueError('SDK revision mismatch')
        # Dependency selection is install metadata, not user configuration/chats.
        scratch=os.environ.pop('HERMES_HOME')
        phase='dependencies'
        sys.path.insert(0,str(source))
        try:
            from pm.environments import selected_venv,site_packages
            dependency_site=site_packages(selected_venv(source))
        finally:os.environ['HERMES_HOME']=scratch
        sys.path.insert(1,str(dependency_site))
        output_format={'type':'json_schema','json_schema':{'name':'role_artifact','strict':True,'schema':request['output_schema']}}
        base=request['base_url'];target=urlsplit(base);port=target.port or (443 if target.scheme=='https' else 80)
        addresses={x[4][0] for x in socket.getaddrinfo(target.hostname,port,type=socket.SOCK_STREAM)}
        def audit(event,args):
            if event=='socket.connect' and isinstance(args[1],tuple):
                if args[1][0] not in addresses or args[1][1]!=port:
                    raise PermissionError('Only the configured gateway is allowed')
        sys.addaudithook(audit)
        # All customizations and retry ladders live only in this ephemeral profile.
        Path(scratch,'config.yaml').write_text(json.dumps({'agent':{'api_max_retries':1,'auto_recovery_cycles':0,'max_tokens':request['max_tokens']},
            'delegation':{'max_iterations':1,'max_spawn_depth':1,'max_concurrent_children':1,'oneshot_max_children':1}}))
        with contextlib.redirect_stdout(_QuietBuffer()),contextlib.redirect_stderr(_QuietBuffer()):
            phase='runtime'
            import httpx
            original_send=httpx.Client._send_single_request
            lock=threading.Lock()
            def guarded_send(client,http_request):
                u=http_request.url
                actual_port=u.port or (443 if u.scheme=='https' else 80)
                if (u.host!=target.hostname or actual_port!=port or u.scheme!=target.scheme
                        or (http_request.method,str(u.path)) not in {('POST','/v1/chat/completions'),('GET','/v1/models')}):
                    raise PermissionError('Gateway route denied')
                if http_request.method=='POST':
                    body=json.loads(http_request.content)
                    caps=request.get('spend_caps',{})
                    limit=caps.get('max_payload_bytes')
                    output=caps.get('max_output_tokens')
                    if (type(limit) is not int or not 1 <= limit <= 512*1024
                            or type(output) is not int or output != request['max_tokens']
                            or len(http_request.content)>limit
                            or type(body.get('max_tokens')) is not int or body['max_tokens']!=output
                            or any(body.get(k) not in (None,1) or type(body.get(k)) is bool for k in ('n','best_of'))
                            or ('max_completion_tokens' in body and (type(body['max_completion_tokens']) is not int or not 0 < body['max_completion_tokens'] <= output))
                            or body.get('functions') or body.get('models') or body.get('plugins')
                            or body.get('route') or body.get('preset')
                            or body.get('service_tier') not in (None,'default')):
                        dispatched['request_check']='spend_caps_changed'
                        raise BridgePolicyError('Transport spend caps changed')
                    if body.get('model')!=request['model']:
                        dispatched['request_check']='model_changed'
                        raise BridgePolicyError('Requested model changed')
                    if body.get('tools'):
                        dispatched['request_check']='tools_present'
                        raise BridgePolicyError('Tools are not allowed')
                    if body.get('response_format')!=output_format:
                        dispatched['request_check']='schema_changed' if body.get('response_format') else 'schema_missing'
                        raise BridgePolicyError('Output schema changed')
                    dispatched['request_check']='handoff_context'
                    texts=[]
                    for message in body.get('messages',[]):
                        content=message.get('content')
                        if isinstance(content,str):texts.append(content)
                        elif isinstance(content,list):texts.extend(x.get('text','') for x in content if isinstance(x,dict))
                    if request['context'] not in '\n'.join(texts):
                        raise BridgePolicyError('Required handoff context was not sent')
                    with lock:
                        if dispatched['count']>=1:raise BridgePolicyError('Repeat inference blocked')
                        dispatched['count']+=1
                dispatched['request_check']='allowed'
                response=original_send(client,http_request)
                dispatched['http_status']=response.status_code
                capture={'data':bytearray(),'overflow':False}
                captures.append(capture)
                def retain(chunk):
                    if len(capture['data'])+len(chunk)>256*1024:
                        capture['overflow']=True
                    elif not capture['overflow']:
                        capture['data'].extend(chunk)
                if response.is_stream_consumed:
                    retain(response.content)
                else:
                    original_stream=response.stream
                    class MeteredStream(httpx.SyncByteStream):
                        def __iter__(self):
                            for chunk in original_stream:
                                retain(chunk)
                                yield chunk
                        def close(self):original_stream.close()
                    response.stream=MeteredStream()
                return response
            httpx.Client._send_single_request=guarded_send
            from run_agent import AIAgent
            original_init=AIAgent.__init__
            def checked_init(agent,*args,**kwargs):
                # delegate_task does not inherit every privacy option from its parent.
                kwargs.update(save_trajectories=False,skip_context_files=True,skip_memory=True,
                    skip_background_review=True,load_soul_identity=False,cwd=scratch,
                    max_tokens=request['max_tokens'],run_budget_seconds=request['budget_seconds'])
                original_init(agent,*args,**kwargs)
                if agent.provider!='custom' or agent.base_url!=base or agent.model!=request['model'] or agent.tools or agent.max_iterations!=1:
                    raise RuntimeError('Unexpected child capabilities or route')
                agent._api_max_retries=1;agent._auto_recovery_cycles=0
                agent._fallback_chain=[];agent._fallback_model=None
                agent._dump_api_request_debug=lambda *a,**kw: None
                agent._client_kwargs['max_retries']=0
                if agent.client is not None:agent.client.max_retries=0
                agents.append(agent)
            AIAgent.__init__=checked_init
            phase='parent'
            parent=AIAgent(provider='custom',requested_provider='custom',api_mode='chat_completions',base_url=base,
                api_key=request['api_key'],model=request['model'],max_iterations=1,enabled_toolsets=[],
                max_tokens=request['max_tokens'],request_overrides={'max_tokens':request['max_tokens'],'response_format':output_format},
                skip_context_files=True,skip_memory=True,skip_background_review=True,load_soul_identity=False,
                save_trajectories=False,quiet_mode=True,run_budget_seconds=request['budget_seconds'],fallback_model={},cwd=scratch)
            from tools.delegate_tool import delegate_task
            from tools.delegation_output_schema import append_output_contract
            phase='delegate'
            result=json.loads(delegate_task(goal=request['instruction'],context=append_output_contract(request['context'],request['output_schema']),parent_agent=parent,background=False))
            entries=result.get('results',[])
            phase='result'
            dispatched['result_status']=entries[0].get('status') if len(entries)==1 else 'wrong_count'
            dispatched['agent_count']=len(agents)
            if len(entries)==1:
                reason=entries[0].get('failure_reason')
                dispatched['failure_reason']=reason if reason in {'rate_limit','billing','timeout','authentication','context_length','api_error','connection','unknown'} else None
                error=str(entries[0].get('error','')).lower()
                dispatched['error_flags']=[x for x in ('timeout','rate','quota','permission','schema','stream','empty','response','budget','connection','401','429','400') if x in error]
                words={'iteration','budget','exhausted','max','iterations','seconds','time','timeout','thinking','response','empty','api','calls','call','limit','exceeded','generation','tokens','before','after','run','maximum','produce','valid','final','content','output','stopped','failed','model','retry','retries','remaining','request','elapsed'}
                dispatched['error_terms']=[w for w in re.findall(r'[a-z]+',error)[:40] if w in words]
            if len(entries)!=1 or entries[0].get('status')!='completed' or len(agents)!=2 or dispatched['count']!=1:
                raise RuntimeError('Hermes did not complete exactly one child request')
            summary=entries[0].get('summary')
            if not isinstance(summary,str) or not summary.strip():raise ValueError('Empty child output')
            usage=_usage(captures)
            response={'ok':True,'summary':summary,'usage':usage,'provenance':{'origin':'hermes','operation_id':request['operation_id'],
                'model_id':request['model'],'provider':'custom','route':'omniroute','sdk_commit':stamp['commit'],
                'parent_session_id':agents[0].session_id,'child_session_id':agents[1].session_id,'tools_count':0,
                'inference_requests':dispatched['count'],'input_sha256':sha256(request['context'].encode()).hexdigest()}}
            for agent in reversed(agents):agent.close()
        print(json.dumps(response));return 0
    except BaseException as exc:
        print(json.dumps({'ok':False,'dispatched':bool(dispatched['count']),'category':'bridge_failed',
                          'phase':phase,'error_type':type(exc).__name__,
                          'http_status':dispatched['http_status'],'request_check':dispatched['request_check'],
                          'result_status':dispatched.get('result_status'),'agent_count':dispatched.get('agent_count'),
                          'failure_reason':dispatched.get('failure_reason'),'error_flags':dispatched.get('error_flags'),
                          'error_terms':dispatched.get('error_terms')}));return 1


if __name__=='__main__':raise SystemExit(main())
