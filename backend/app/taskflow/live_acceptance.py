"""Explicit, bounded synthetic acceptance; credentials arrive only via stdin."""
import argparse
from uuid import uuid4
import os
from backend.app.config import Settings
from backend.app.budgets.runtime import RuntimeSpend
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

from backend.app.providers.omniroute import OmniRouteConfig
from backend.app.storage.db import connect, init_db
from backend.app.storage.repositories import Repository
from .hermes import HermesDirector, HermesPreflightError, run_bridge
from .hermes_config import HermesConfig
from .models import Command, HermesCreateRun, OwnerDecision
from .service import FlowError, Taskflow

GOAL = ('Учебный проект без пользовательских данных: короткий план чистой функции add(a,b). '
        'Критерии: add(2,3)=5, add(-1,1)=0. Не исполнять код; ответы краткие.')
MAX_CALLS = 8  # 3 + 4 roles, then one max_steps probe. No fallback/retry.


def _require(condition):
    if not condition:
        raise AssertionError('Acceptance invariant failed')


def snapshot_hash(snapshot):
    return sha256(snapshot.model_dump_json().encode()).hexdigest()


class ReplayOnly:
    binding = 'unconfigured-replay-only'

    def produce_with_context(self, *args):
        raise AssertionError('Replay must never dispatch a new external request')


def replay_only(payload):
    with closing(connect(Path(payload['database']))) as conn:
        flow = Taskflow(conn, ReplayOnly(), mode='hermes')
        before = flow.get(payload['run_id'])
        replay = flow.apply(payload['run_id'], Command.model_validate(payload['command']))
        after = flow.get(payload['run_id'])
        if before != after:
            raise AssertionError('Replay changed the persisted state')
        return {'snapshot_hash': snapshot_hash(after), 'replay_hash': snapshot_hash(replay)}


def run_acceptance(director, root, settings, emit=lambda event: None, *, approved_micro_usd=0):
    """root belongs to the caller; the acceptance DB must not already exist."""
    database = Path(root) / 'acceptance.sqlite3'
    if database.exists():
        raise ValueError('Acceptance database must be new')
    if type(approved_micro_usd) is not int or approved_micro_usd < 0:
        raise ValueError('Invalid explicit budget')
    # Reserve the file exclusively; never truncate or reuse prior financial evidence.
    fd = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    events = []
    def bind(conn, task_id):
        runtime = RuntimeSpend(conn, settings)
        director.spending = runtime
        policy, route = runtime.route('omniroute', director.config.model, 'analyst', director.omni.base_url)
        for role in ('architect', 'developer', 'reviewer'):
            runtime.route('omniroute', director.config.model, role, director.omni.base_url)
        if route.tariff.quote(route.max_input_tokens, route.max_output_tokens) > 0:
            if not approved_micro_usd:
                raise ValueError('Explicit paid budget required')
            for scope in ('global', 'project', 'task'):
                account = next(a for a in runtime.status(task_id)['accounts'] if a['scope'] == scope)
                if account['limits']['allow_paid']:
                    continue  # Keep existing total ceiling and committed costs across chains.
                runtime.approve(task_id=task_id, scope=scope, amount_micro_usd=approved_micro_usd,
                    allow_paid=True, reference=route.reference, expected_revision=account['revision'],
                    approval_id=uuid4().hex, actor='acceptance-cli-owner', expected_policy_sha256=policy.fingerprint)
        return runtime
    def record(**event):
        events.append(event)
        emit(event)
    for architect in (False, True):
        roles = ['analyst', 'architect', 'developer', 'reviewer'] if architect else ['analyst', 'developer', 'reviewer']
        with closing(connect(database)) as conn:
            init_db(conn)
            task = Repository(conn).create_task(GOAL)
            bind(conn, task['id'])
            flow = Taskflow(conn, director, mode='hermes')
            run = flow.create(task['id'], HermesCreateRun(idempotency_key='create', include_architect=architect, max_steps=len(roles)))
            run = flow.apply(run.run_id, Command(idempotency_key='start', expected_version=run.version, action='start'))
            for role in roles:
                previous = run
                command = Command(idempotency_key='step-'+role, expected_version=run.version, action='step')
                started = time.monotonic()
                run = flow.apply(run.run_id, command)
                record(check='role_step', architect=architect, role=role, status=run.status,
                       artifact_count=len(run.artifacts), duration_seconds=round(time.monotonic()-started, 3))
                if len(run.artifacts) != len(previous.artifacts)+1 or run.artifacts[-1].role != role:
                    raise AssertionError('Role did not complete; no further calls permitted')
                artifact = run.artifacts[-1]
                _require(artifact.input_artifact_ids == [a.artifact_id for a in previous.artifacts])
                _require(artifact.provenance.tools_count == 0 and artifact.provenance.inference_requests == 1)
                if role == 'architect':
                    _require(artifact.content.requirements == previous.artifacts[0].content)
                elif role == 'developer':
                    _require(artifact.content.requirements == previous.artifacts[0].content)
                    _require(artifact.content.design == (previous.artifacts[1].content if architect else None))
                elif role == 'reviewer':
                    _require(artifact.content.plan == previous.artifacts[-1].content)
                _require(flow.apply(run.run_id, command) == run)
            _require(run.status == 'awaiting_owner')
            replay_hash = snapshot_hash(run)
            accepted = flow.apply(run.run_id, OwnerDecision(idempotency_key='owner-test', expected_version=run.version,
                                    decision='approve', reason='Синтетическая проверка сохранения решения владельца'))
            _require(accepted.status == 'done' and not accepted.code_executed and not accepted.qa_verified)
            expected_hash = snapshot_hash(accepted)
        payload = {'database': str(database), 'run_id': run.run_id, 'command': command.model_dump()}
        reopened = subprocess.run([sys.executable, '-m', 'backend.app.taskflow.live_acceptance', '--replay-only'], input=json.dumps(payload),
                                  text=True, capture_output=True, timeout=20)
        if reopened.returncode:
            raise AssertionError('Separate-process replay failed')
        summary = json.loads(reopened.stdout)
        _require(summary == {'snapshot_hash': expected_hash, 'replay_hash': replay_hash})
        record(check='pipeline_complete', architect=architect, roles=roles, owner_state='done',
               separate_process_reopen=True, replay_without_credentials=True, typed_handoff=True,
               code_executed=False, qa_verified=False)
    with closing(connect(database)) as conn:
        task = Repository(conn).create_task(GOAL)
        bind(conn, task['id'])
        flow = Taskflow(conn, director, mode='hermes')
        run = flow.create(task['id'], HermesCreateRun(idempotency_key='limit-create', max_steps=1))
        run = flow.apply(run.run_id, Command(idempotency_key='limit-start', expected_version=run.version, action='start'))
        run = flow.apply(run.run_id, Command(idempotency_key='limit-step', expected_version=run.version, action='step'))
        _require(run.status == 'blocked' and run.block_code == 'step_limit' and len(run.artifacts) == 1)
        try:
            flow.apply(run.run_id, Command(idempotency_key='limit-resume', expected_version=run.version, action='resume'))
        except FlowError as exc:
            _require(exc.code == 'step_limit')
        else:
            raise AssertionError('Step limit was bypassed')
        _require(len(flow.list_operations(run.run_id)) == 1)
        record(check='max_steps', max_steps=1, status=run.status, resume_rejected=True, operations=1)
    return events


def main(argv=None):
    parser = argparse.ArgumentParser(description='Ограниченная проверка Hermes; только синтетическая временная БД')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--allow-inference', action='store_true', help='Явно разрешить максимум 8 вызовов по настроенному маршруту')
    mode.add_argument('--replay-only', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--workspace', help='New private acceptance directory; retained on failure and success')
    parser.add_argument('--spend-policy', default='', help='Absolute owner-verified runtime policy path')
    parser.add_argument('--approved-micro-usd', type=int, default=0, help='Explicit total paid ceiling in micro-USD, never a top-up')
    args = parser.parse_args(argv)
    calls = 0
    try:
        encoded = sys.stdin.buffer.read(65537)
        if len(encoded) > 65536:
            raise ValueError('Acceptance input exceeds limit')
        payload = json.loads(encoded)
        if args.replay_only:
            print(json.dumps(replay_only(payload)))
            return 0
        if not args.workspace or not Path(args.workspace).is_absolute():
            raise ValueError('New absolute workspace required')
        root = Path(args.workspace)
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        hermes = HermesConfig(enabled=True, python=payload['python'], source=payload['source'],
                              expected_commit=payload['expected_commit'], model=payload['model'],
                              timeout_seconds=90, max_output_tokens=768)
        omni = OmniRouteConfig(enabled=True, base_url=payload['base_url'], api_key=payload['api_key'], max_get_attempts=1)
        def observed_runner(config, request, cancelled):
            nonlocal calls
            if calls >= MAX_CALLS:
                raise RuntimeError('Acceptance dispatch limit reached')
            calls += 1
            try:
                return run_bridge(config, request, cancelled)
            except HermesPreflightError as exc:
                print(json.dumps({'check':'bridge_preflight_failed','phase':exc.phase,'error_type':exc.error_type,'inference_dispatched':False}), flush=True)
                raise
        director = HermesDirector(hermes, omni, runner=observed_runner)
        if not director.available:
            raise ValueError('Hermes preflight unavailable')
        emit = lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)
        settings = Settings(omniroute=omni, hermes=hermes, spend_policy_file=args.spend_policy)
        events = run_acceptance(director, root, settings, emit, approved_micro_usd=args.approved_micro_usd)
        _require(calls == MAX_CALLS)
        emit({'check': 'acceptance_complete', 'passed': True, 'bridge_calls': calls, 'event_count': len(events),
              'scope': 'real_planning_artifacts_only', 'code_executed': False, 'qa_verified': False})
        return 0
    except Exception as exc:
        # No SDK output, credentials, paths, prompts or raw exception messages.
        print(json.dumps({'check': 'acceptance_failed', 'passed': False, 'error_type': type(exc).__name__,
                          'code': exc.code if isinstance(exc, FlowError) else None, 'bridge_calls': calls}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
