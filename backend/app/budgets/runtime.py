"""Runtime admission using the existing ledger on the operation's connection."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import re
import time
from uuid import uuid4

from .ledger import BudgetError, BudgetLedger, _dump
from .models import Policy, Route, Request, Outcome, Usage
from .runtime_policy import load_runtime_policy

ROLE = {'manager':'analyst', 'designer':'architect', 'coder':'developer',
        'analyst':'analyst', 'architect':'architect', 'developer':'developer', 'reviewer':'reviewer'}


class SpendDenied(BudgetError):
    """A local refusal: no permission to dispatch, independent of model text."""


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def retry_after_delay(value):
    if value is None:
        return None
    try:
        delay = float(value)
    except (ValueError,TypeError):
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:return None
            delay = stamp.timestamp()-time.time()
        except (ValueError,TypeError,OverflowError):
            return None
    return min(86400,max(0,math.ceil(delay))) if math.isfinite(delay) else None


def usage_from_response(raw, *, cost_unit=None, credit_micro_usd=None):
    if not isinstance(raw, dict) or not isinstance(raw.get('usage'), dict):
        return None
    usage = raw['usage']
    incoming, outgoing = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if type(incoming) is not int or type(outgoing) is not int or not 0 <= incoming <= 10**9 or not 0 <= outgoing <= 10**9:
        return None
    cost = None
    factor = 10**6 if cost_unit == 'usd' else credit_micro_usd if cost_unit == 'credits' else None
    if factor is not None and type(usage.get('cost')) in (int, float, str):
        try:
            amount = Decimal(str(usage['cost']))
            if amount.is_finite() and 0 <= amount <= 9*10**9:
                converted = (amount*factor).to_integral_value(rounding=ROUND_CEILING)
                if converted <= 9*10**15:cost = int(converted)
        except (InvalidOperation, ValueError):
            pass
    return Usage(input_tokens=incoming, output_tokens=outgoing, cost_micro_usd=cost)


def reported_usage(raw):
    """Whitelist counters/decimal amount; no provider body or opaque strings."""
    parsed = usage_from_response(raw)
    if parsed is None:
        return None
    result = {'prompt_tokens':parsed.input_tokens,'completion_tokens':parsed.output_tokens}
    amount = raw['usage'].get('cost')
    if type(amount) in (int,float,str):
        try:
            number = Decimal(str(amount))
            if number.is_finite() and 0 <= number <= 9*10**9:result['cost'] = str(number)
        except InvalidOperation:
            pass
    return result


class RuntimeSpend:
    def __init__(self, conn, settings, *, clock_ms=None):
        self.conn, self.settings = conn, settings
        self.clock = clock_ms or (lambda:time.time_ns()//1_000_000)
        self.ledger = BudgetLedger.for_connection(conn, clock_ms=self.clock)
        self.pending = {}
        self.pending_usage = {}

    def policy(self):
        value = load_runtime_policy(self.settings.spend_policy_file, now_ms=self.clock())
        if value is None:
            raise SpendDenied('pricing_policy_unavailable')
        return value

    def credential_sha256(self, provider):
        key = self.settings.openrouter_api_key if provider=='openrouter' else self.settings.omniroute.api_key
        return hashlib.sha256(key.encode()).hexdigest()

    def route(self, provider, model, role, base_url):
        policy = self.policy()
        canonical = ROLE.get(role)
        route = next((r for r in policy.routes if r.provider == provider and r.model_id == model
                      and canonical in r.roles and r.base_url == base_url.rstrip('/')), None)
        if route is None:
            raise SpendDenied('route_not_allowed')
        if route.tariff is None:
            raise SpendDenied('pricing_unknown')
        if route.account_sha256 != self.credential_sha256(provider):
            raise SpendDenied('account_policy_mismatch')
        if not (route.fixed_route_verified and route.billing_categories_verified and route.provider_caps_verified):
            raise SpendDenied('provider_caps_unknown')
        if provider == 'openrouter' and route.tariff.quote(route.max_input_tokens,route.max_output_tokens)>0 and route.upstream_tariff is None:
            raise SpendDenied('provider_caps_unknown')
        if self.clock() + route.timeout_seconds*1000 > policy.valid_until_ms:
            raise SpendDenied('pricing_evidence_expiring')
        return policy, route

    def task_context(self, task_id):
        if task_id is None:
            return None, 'model_check'
        task = self.conn.execute('SELECT project_id,description FROM tasks WHERE id=?',(task_id,)).fetchone()
        if task is None:
            raise SpendDenied('task_not_found')
        from ..orchestration.role_router import detect_task_type
        return task['project_id'], detect_task_type(task['description'])

    def scopes(self, task_id, project_id):
        return ['runtime.global'] + ([f'runtime.project.{project_id}', f'runtime.task.{task_id}'] if task_id is not None else [])

    def account_policy(self, policy):
        routes = tuple(Route(role=role, model_id=r.reference, tariff=r.tariff)
                       for r in policy.routes for role in r.roles)
        return Policy(routes=routes, limits=policy.limits())

    def ensure_scope(self, account_id, policy):
        saved = self.conn.execute('SELECT * FROM spend_scopes WHERE id=?',(account_id,)).fetchone()
        if saved:
            if saved['policy_hash'] != policy.fingerprint:
                old = self.ledger.report(account_id)
                revised = self.account_policy(policy)
                limits = revised.limits.model_copy(update={'max_micro_usd':old['limits']['max_micro_usd'],'allow_paid':False})
                revised = revised.model_copy(update={'limits':limits})
                self.conn.execute('UPDATE budget_accounts SET policy_json=? WHERE id=?',(_dump(revised),account_id))
                self.conn.execute("UPDATE spend_scopes SET policy_hash=?,revision=revision+1,paid_references_json='[]' WHERE id=?",(policy.fingerprint,account_id))
                saved = self.conn.execute('SELECT * FROM spend_scopes WHERE id=?',(account_id,)).fetchone()
            return dict(saved)
        self.ledger.create_account(account_id, self.account_policy(policy))
        self.conn.execute('INSERT INTO spend_scopes(id,policy_hash) VALUES(?,?)',(account_id,policy.fingerprint))
        return dict(self.conn.execute('SELECT * FROM spend_scopes WHERE id=?',(account_id,)).fetchone())

    def event(self, task_id, provider, model, code, operation_id=None):
        project_id, category = self.task_context(task_id)
        with self.ledger._transaction():
            self.conn.execute('''INSERT INTO spend_events(operation_id,task_id,project_id,category,code,provider,model_id,created_ms)
                VALUES(?,?,?,?,?,?,?,?)''',(operation_id,task_id,project_id,category,code,provider,model,self.clock()))

    def prepare(self, *, task_id, run_id, origin, role, provider, model, base_url,
                context_sha256, operation_id=None, expected_policy_hash=None):
        operation_id = operation_id or uuid4().hex
        policy, route = self.route(provider,model,role,base_url)
        if not re.fullmatch('[0-9a-f]{32}', operation_id) or not re.fullmatch('[0-9a-f]{64}', context_sha256):
            raise SpendDenied('invalid_permission_identity')
        if expected_policy_hash is not None and policy.fingerprint != expected_policy_hash:
            raise SpendDenied('server_policy_changed')
        if origin not in {'legacy','legacy_retry','hermes','model_check'} or (task_id is None and origin!='model_check'):
            raise SpendDenied('invalid_operation_scope')
        project_id, category = self.task_context(task_id)
        if run_id is not None:
            row = self.conn.execute('SELECT task_id FROM taskflow_runs WHERE id=?',(run_id,)).fetchone()
            if row is None or row['task_id'] != task_id:
                raise SpendDenied('operation_binding_invalid')
        reservations = []
        paid = route.tariff.quote(route.max_input_tokens, route.max_output_tokens) > 0
        with self.ledger._transaction():
            if self.conn.execute('SELECT 1 FROM spend_permissions WHERE id=?',(operation_id,)).fetchone():
                raise SpendDenied('dispatch_permission_exists')
            for n, scope_id in enumerate(self.scopes(task_id,project_id)):
                scope = self.ensure_scope(scope_id, policy)
                if paid and route.reference not in json.loads(scope['paid_references_json']):
                    raise SpendDenied('paid_approval_required')
                request = Request(operation_id=f'{operation_id}.{n}',role=ROLE[role],model_id=route.reference,
                                  input_tokens=route.max_input_tokens,output_tokens=route.max_output_tokens,
                                  timeout_seconds=route.timeout_seconds)
                decision = self.ledger.reserve(scope_id, request)
                if not decision.allowed:
                    raise SpendDenied(decision.code)
                reservations.append((scope_id,request.operation_id))
            now = self.clock()
            self.conn.execute('''INSERT INTO spend_permissions(id,task_id,project_id,run_id,origin,role,provider,model_id,policy_hash,context_sha256,reservations_json,state,created_ms,updated_ms,policy_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (operation_id,task_id,project_id,run_id,origin,ROLE[role],provider,model,
                 policy.fingerprint,context_sha256,json.dumps(reservations),'reserved',now,now,policy.model_dump_json()))
            # All scopes and the parent StepOperation share the caller's commit.
            self.conn.execute('''INSERT INTO spend_events(operation_id,task_id,project_id,category,code,provider,model_id,created_ms)
                VALUES(?,?,?,?,?,?,?,?)''',(operation_id,task_id,project_id,category,
                'paid_within_approved_budget' if paid else 'verified_free_route',provider,model,now))
        return operation_id, route

    def permission(self, operation_id):
        row = self.conn.execute('SELECT * FROM spend_permissions WHERE id=?',(operation_id,)).fetchone()
        if row is None:
            raise SpendDenied('dispatch_permission_missing')
        return dict(row)

    def claim(self, operation_id, *, context_sha256, base_url):
        # The permission is spent once, durably, immediately before transport.
        if self.conn.in_transaction:
            raise SpendDenied('dispatch_inside_transaction')
        with self.ledger._transaction():
            row = self.permission(operation_id)
            policy, route = self.route(row['provider'],row['model_id'],row['role'],base_url)
            if row['state'] != 'reserved' or row['policy_hash'] != policy.fingerprint or row['context_sha256'] != context_sha256:
                raise SpendDenied('dispatch_permission_stale')
            if row['run_id'] is not None:
                operation = self.conn.execute('SELECT state,cancel_requested FROM taskflow_operations WHERE id=?',(operation_id,)).fetchone()
                if operation is None or operation['state'] != 'running' or operation['cancel_requested']:
                    raise SpendDenied('operation_cancelled_or_stale')
            for account_id, reservation in json.loads(row['reservations_json']):
                scope = self.ensure_scope(account_id,policy)
                if route.tariff.quote(route.max_input_tokens,route.max_output_tokens) and route.reference not in json.loads(scope['paid_references_json']):
                    raise SpendDenied('paid_approval_required')
                self.ledger.claim(reservation)
            self.conn.execute("UPDATE spend_permissions SET state='dispatched',updated_ms=? WHERE id=?",(self.clock(),operation_id))
        return route

    def finish(self, operation_id, outcome=None, reported=None):
        reported = reported or self.pending_usage.pop(operation_id,None)
        outcome = outcome or self.pending.pop(operation_id, None) or Outcome(kind='unknown')
        with self.ledger._transaction():
            row = self.permission(operation_id)
            if row['state'] != 'dispatched':
                if row['state'] == 'reserved':
                    for _, reservation in json.loads(row['reservations_json']):
                        self.ledger.release_unclaimed(reservation)
                    self.conn.execute("UPDATE spend_permissions SET state='released',updated_ms=? WHERE id=?",(self.clock(),operation_id))
                return
            if reported is not None:
                policy = json.loads(row['policy_json'])
                route = next(r for r in policy['routes'] if r['provider']==row['provider'] and r['model_id']==row['model_id'])
                evidence = {'usage':reported,'reported_cost_unit':route['reported_cost_unit'],'credit_micro_usd':route['credit_micro_usd']}
                self.conn.execute('UPDATE spend_permissions SET usage_evidence_json=? WHERE id=?',(json.dumps(evidence,allow_nan=False),operation_id))
            for _, reservation in json.loads(row['reservations_json']):
                self.ledger.record(reservation,outcome)
            self.conn.execute('UPDATE spend_permissions SET state=?,updated_ms=? WHERE id=?',(outcome.kind,self.clock(),operation_id))

    def call(self, *, task_id, origin, role, provider, model, base_url, messages, send, timeout_seconds, cancelled=lambda:False, output_limit=None, credential_sha256=None):
        if credential_sha256 is not None and credential_sha256 != self.credential_sha256(provider):
            raise SpendDenied('account_policy_mismatch')
        if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise SpendDenied('invalid_request')
        if cancelled():
            raise SpendDenied('cancelled_before_dispatch')
        policy, route = self.route(provider,model,role,base_url)
        payload = {'model':model,'messages':messages,'max_tokens':min(route.max_output_tokens,output_limit) if output_limit is not None else route.max_output_tokens,'stream':False}
        if provider == 'openrouter':
            upstream = route.upstream_tariff or route.tariff
            payload['provider'] = {'allow_fallbacks':False,'require_parameters':True,'sort':'price',
                'max_price':{'prompt':upstream.input_micro_usd_per_million/1000000,
                             'completion':upstream.output_micro_usd_per_million/1000000,
                             'request':upstream.request_micro_usd/1000000,'image':0}}
            payload['modalities'] = ['text']
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()
        if len(encoded) > route.max_payload_bytes:
            raise SpendDenied('input_limit')
        context = fingerprint(payload)
        operation_id, route = self.prepare(task_id=task_id,run_id=None,origin=origin,role=role,provider=provider,
            model=model,base_url=base_url,context_sha256=context,expected_policy_hash=policy.fingerprint)
        if cancelled():
            self.finish(operation_id)
            raise SpendDenied('cancelled_before_dispatch')
        try:
            self.claim(operation_id,context_sha256=context,base_url=base_url)
        except BudgetError:
            self.finish(operation_id)
            raise
        try:
            result = send(payload,min(timeout_seconds,route.timeout_seconds))
        except BaseException as error:
            # Only structured explicit responses may resolve an ambiguous send.
            status = getattr(error,'http_status',getattr(error,'status_code',None))
            if status == 429:
                delay = getattr(error,'budget_retry_after_seconds',getattr(error,'retry_after_seconds',None))
                delay = min(86400,max(0,math.ceil(delay))) if type(delay) in (int,float) and math.isfinite(delay) else None
                outcome = Outcome(kind='quota',http_status=429,retry_after_seconds=delay)
            elif status in {400,401,403,404,405,413,422}:
                outcome = Outcome(kind='rejected',http_status=status)
            else:
                outcome = Outcome(kind='unknown')
            self.finish(operation_id,outcome)
            raise
        else:
            reported = getattr(result,'reported_usage',None)
            raw = {'usage':reported} if reported is not None else result.raw
            usage = usage_from_response(raw,cost_unit=route.reported_cost_unit,credit_micro_usd=route.credit_micro_usd)
            self.finish(operation_id,Outcome(kind='completed',http_status=200,usage=usage),reported=reported or reported_usage(result.raw))
            return result

    def recover_interrupted(self):
        """No replay: revoke unclaimed grants and conservatively hold dispatched ones."""
        with self.ledger._transaction():
            rows = self.conn.execute("SELECT id FROM spend_permissions WHERE state IN ('reserved','dispatched')").fetchall()
            for row in rows:
                self.finish(row['id'],Outcome(kind='unknown'))
                self.conn.execute("UPDATE taskflow_operations SET state='unknown',worker_hash='' WHERE id=? AND state='running'",(row['id'],))

    def status(self, task_id=None, run_id=None, mode="mock"):
        with self.ledger._transaction(write=False):
            return self._status(task_id,run_id,mode)

    def _status(self, task_id=None, run_id=None, mode="mock"):
        project_id, _ = self.task_context(task_id)
        try:
            policy = self.policy()
        except SpendDenied:
            policy = None
        accounts = []
        for account_id in self.scopes(task_id,project_id):
            scope = self.conn.execute('SELECT * FROM spend_scopes WHERE id=?',(account_id,)).fetchone()
            if scope:
                report = self.ledger.report(account_id)
                outcomes = [json.loads(r['outcome_json']) if r['outcome_json'] else None for r in self.conn.execute("SELECT outcome_json FROM budget_requests WHERE account_id=? AND state NOT IN ('reserved','released')",(account_id,))]
                complete = bool(outcomes) and all(o is not None and o.get('usage') is not None for o in outcomes)
                report['reported_input_tokens'] = sum(o['usage']['input_tokens'] for o in outcomes) if complete else None
                report['reported_output_tokens'] = sum(o['usage']['output_tokens'] for o in outcomes) if complete else None
                report.update(scope=account_id.split('.')[1],revision=scope['revision'],
                              policy_current=bool(policy and scope['policy_hash']==policy.fingerprint),
                              approved_paid_references=json.loads(scope['paid_references_json']))
            else:
                report = {'scope':account_id.split('.')[1],'revision':0,'policy_current':bool(policy),
                          'limits':{'allow_paid':False,'max_micro_usd':0},'budget_committed_micro_usd':0,
                          'known_cost_subtotal_micro_usd':0,'reported_cost_micro_usd':None,
                          'reported_cost_complete':False,'dispatches_claimed':0,'tokens_committed':0,
                          'remaining_micro_usd':0,'approved_paid_references':[],'states':{},'reported_input_tokens':None,'reported_output_tokens':None}
            report['scope_id'] = account_id
            if report['dispatches_claimed'] == 0:
                report['reported_cost_micro_usd'] = None
            accounts.append(report)
        where, params = ('WHERE task_id=?',(task_id,)) if task_id is not None else ('',())
        events = [dict(row) for row in self.conn.execute(f'SELECT category,code,provider,model_id,created_ms FROM spend_events {where} ORDER BY id DESC LIMIT 101',params)]
        scope_ids = self.scopes(task_id,project_id)
        approvals = [dict(row) for row in self.conn.execute('SELECT * FROM spend_approvals WHERE scope_id IN ('+','.join('?' for _ in scope_ids)+') ORDER BY created_ms DESC,id DESC LIMIT 101',scope_ids)]
        for approval in approvals:
            approval['before'] = json.loads(approval.pop('before_json'))
            approval['after'] = json.loads(approval.pop('after_json'))
            approval.pop('request_sha256')
        usage_evidence = [dict(row) for row in self.conn.execute(f'SELECT id,provider,model_id,usage_evidence_json,updated_ms FROM spend_permissions {where + " AND" if where else "WHERE"} usage_evidence_json IS NOT NULL ORDER BY created_ms DESC LIMIT 101',params)]
        for evidence in usage_evidence:evidence['reported'] = json.loads(evidence.pop('usage_evidence_json'))
        estimates = []
        for route in policy.routes if policy else ():
            estimates.append({'provider':route.provider,'model_id':route.model_id,'roles':list(route.roles),
                'maximum_input_tokens':route.max_input_tokens,'maximum_output_tokens':route.max_output_tokens,
                'expected_input_tokens':None,'expected_output_tokens':None,
                'upper_bound_micro_usd':route.tariff.quote(route.max_input_tokens,route.max_output_tokens) if route.tariff else None,
                'accuracy':'owner_verified_upper_bound' if route.tariff and route.billing_categories_verified and route.provider_caps_verified and route.fixed_route_verified else 'unknown',
                'source':'API','fixed_route_verified':route.fixed_route_verified})
        return {'scope':'runtime_spending','task_id':task_id,'project_id':project_id,'currency':'USD',
                'policy_available':policy is not None,'policy_sha256':policy.fingerprint if policy else None,
                'selected_route':self.preview(task_id,run_id,mode,accounts),
                'valid_until_ms':policy.valid_until_ms if policy else None,
                'server_ceilings_micro_usd':{scope:policy.ceiling(scope) if policy else 0 for scope in ('global','project','task')},
                'accounts':accounts,'estimates':estimates,'events':events[:100],'events_limited':len(events)>100,
                'approvals':approvals[:100],'approvals_limited':len(approvals)>100,
                'usage_evidence':usage_evidence[:100],'usage_evidence_limited':len(usage_evidence)>100,
                'subscription_quota_remaining':None,'subscription_money_cost':None,
                'local_compute_money_cost':None,'manual_chatgpt_money_cost':None,
                'real_cash_payment_micro_usd':None,'automatic_paid_escalation':False}

    def preview(self, task_id, run_id, mode, accounts):
        if task_id is None:
            return None
        role = 'analyst'
        if run_id is not None:
            run = self.conn.execute('SELECT * FROM taskflow_runs WHERE id=? AND task_id=?',(run_id,task_id)).fetchone()
            if run is None or run['director_mode'] != mode:
                raise SpendDenied('run_mode_mismatch')
            stage = self.conn.execute("SELECT role FROM taskflow_stages WHERE run_id=? AND status!='completed' ORDER BY position LIMIT 1",(run_id,)).fetchone()
            if stage is None:
                return {'source':'local','dispatch_required':False,'reason':'no_pending_model_role'}
            role = stage['role']
        if mode == 'mock':
            return {'source':'local','dispatch_required':False,'reason':'deterministic_mock_workflow','role':role}
        from ..taskflow.hermes import HermesDirector
        director = HermesDirector(self.settings.hermes,self.settings.omniroute)
        base = {'source':'API','dispatch_required':True,'provider':'omniroute','model_id':self.settings.hermes.model,
                'role':role,'upper_bound_micro_usd':None,'accuracy':'unknown'}
        if not director.available:
            return {**base,'block_code':'hermes_unavailable'}
        if run_id is not None and run['director_binding'] != director.binding:
            return {**base,'block_code':'director_config_changed'}
        try:
            policy, route = self.route('omniroute',self.settings.hermes.model,role,self.settings.omniroute.base_url)
        except SpendDenied as error:
            return {**base,'block_code':error.code}
        bound = route.tariff.quote(route.max_input_tokens,route.max_output_tokens)
        block = None
        for account in accounts:
            if account['states'].get('unknown'):block='outcome_unknown'
            elif account.get('overrun'):block='usage_overrun'
            elif account.get('cooldown_until_ms',0)>self.clock():block='provider_cooldown'
            elif account.get('remaining_requests')==0:block='request_limit'
            elif account.get('remaining_tokens',policy.max_total_tokens)<route.max_input_tokens+route.max_output_tokens:block='token_budget'
            elif account.get('deadline_ms',self.clock()+86400000)<self.clock()+route.timeout_seconds*1000:block='deadline_exhausted'
            elif bound and (not account['limits']['allow_paid'] or route.reference not in account['approved_paid_references']):block='paid_approval_required'
            elif bound>account['remaining_micro_usd']:block='money_budget'
            if block:break
        return {**base,'upper_bound_micro_usd':bound,'accuracy':'owner_verified_upper_bound',
                'maximum_input_tokens':route.max_input_tokens,'maximum_output_tokens':route.max_output_tokens,
                'block_code':block}

    def approve(self, *, task_id, scope, amount_micro_usd, allow_paid, reference,
                expected_revision, approval_id, actor, expected_policy_sha256):
        if scope not in {'global','project','task'} or type(amount_micro_usd) is not int or amount_micro_usd < 0:
            raise SpendDenied('invalid_approval')
        if not re.fullmatch('[0-9a-f]{32}', approval_id):
            raise SpendDenied('invalid_approval_identity')
        if scope != 'global' and task_id is None:
            raise SpendDenied('task_required')
        project_id, _ = self.task_context(task_id)
        account_id = 'runtime.global' if scope == 'global' else f'runtime.{scope}.{project_id if scope=="project" else task_id}'
        request_hash = fingerprint({'task_id':task_id,'scope':scope,'amount':amount_micro_usd,'allow_paid':allow_paid,
                                    'reference':reference,'expected_revision':expected_revision,'policy_sha256':expected_policy_sha256})
        with self.ledger._transaction():
            prior = self.conn.execute('SELECT * FROM spend_approvals WHERE id=?',(approval_id,)).fetchone()
            if prior:
                if prior['request_sha256'] != request_hash or prior['actor'] != actor:
                    raise SpendDenied('approval_conflict')
                return json.loads(prior['after_json'])
            policy = self.policy()
            if policy.fingerprint != expected_policy_sha256:
                raise SpendDenied('server_policy_changed')
            # The same global unknown remains blocking across projects/keys/IDs.
            if allow_paid and self.conn.execute("SELECT 1 FROM spend_permissions WHERE state='unknown'").fetchone():
                raise SpendDenied('outcome_unknown')
            if allow_paid and self.conn.execute("SELECT 1 FROM budget_accounts WHERE id='runtime.global' AND overrun=1").fetchone():
                raise SpendDenied('usage_overrun')
            saved = self.conn.execute('SELECT * FROM spend_scopes WHERE id=?',(account_id,)).fetchone()
            if saved is None:
                self.ensure_scope(account_id,policy)
                saved = self.conn.execute('SELECT * FROM spend_scopes WHERE id=?',(account_id,)).fetchone()
            if saved['revision'] != expected_revision:
                raise SpendDenied('stale_budget_revision')
            before = self.ledger.report(account_id)
            references = json.loads(saved['paid_references_json']) if saved['policy_hash']==policy.fingerprint else []
            if allow_paid:
                route = next((r for r in policy.routes if r.reference == reference), None)
                if route is None:
                    raise SpendDenied('route_not_allowed')
                self.route(route.provider,route.model_id,route.roles[0],route.base_url)
                if amount_micro_usd == 0 or amount_micro_usd > policy.ceiling(scope):
                    raise SpendDenied('server_ceiling_exceeded')
                if amount_micro_usd < before['budget_committed_micro_usd']:
                    raise SpendDenied('budget_below_committed')
                if reference not in references:
                    references.append(reference)
            else:
                if amount_micro_usd != 0:
                    raise SpendDenied('revocation_requires_zero')
                references = []
            revised = self.account_policy(policy)
            limits = revised.limits.model_copy(update={'allow_paid':allow_paid,'max_micro_usd':amount_micro_usd})
            revised = revised.model_copy(update={'limits':limits})
            self.conn.execute('UPDATE budget_accounts SET policy_json=? WHERE id=?',(_dump(revised),account_id))
            # Explicit owner renewal extends time only: old spend, tokens, requests
            # and unresolved holds are retained, never reset into a fresh account.
            if self.clock() >= before['deadline_ms']:
                self.conn.execute('UPDATE budget_accounts SET created_ms=?,last_seen_ms=? WHERE id=?',(self.clock(),self.clock(),account_id))
            self.conn.execute('UPDATE spend_scopes SET policy_hash=?,revision=revision+1,paid_references_json=? WHERE id=?',
                              (policy.fingerprint,json.dumps(references),account_id))
            after = self.ledger.report(account_id)
            after.update(revision=expected_revision+1,approved_paid_references=references)
            self.conn.execute('INSERT INTO spend_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (approval_id,account_id,expected_revision+1,actor,self.clock(),after['created_ms'],after['deadline_ms'],
                 policy.fingerprint,json.dumps(before),json.dumps(after),request_hash))
            return after


class ProviderBudget:
    """Server-created context for one role; raw providers deny without it."""
    def __init__(self, runtime, *, task_id, origin, role, cancelled=lambda:False):
        self.runtime, self.task_id, self.origin, self.role = runtime,task_id,origin,role
        self.cancelled = cancelled

    def chat(self, *, provider, model, base_url, messages, timeout_seconds, send, credential_sha256, output_limit=None):
        from ..providers.base import ProviderError
        try:
            return self.runtime.call(task_id=self.task_id,origin=self.origin,role=self.role,
                provider=provider,model=model,base_url=base_url,messages=messages,timeout_seconds=timeout_seconds,
                send=send,cancelled=self.cancelled,output_limit=output_limit,credential_sha256=credential_sha256)
        except SpendDenied as error:
            try:
                self.runtime.event(self.task_id,provider,model,error.code)
            except BudgetError:
                pass
            failure = ProviderError('Запрос модели не отправлен: '+error.code,'spend_blocked')
            failure.budget_code = error.code
            raise failure from None

    def free_first(self, references):
        """Prefer verified zero-price entries in the EXISTING legacy config only."""
        try:
            policy = self.runtime.policy()
        except SpendDenied:
            return references
        free = {r.reference for r in policy.routes if ROLE.get(self.role) in r.roles
                and r.tariff is not None and r.tariff.quote(r.max_input_tokens,r.max_output_tokens)==0
                and r.fixed_route_verified and r.billing_categories_verified and r.provider_caps_verified}
        return [r for r in references if r in free] + [r for r in references if r not in free]
