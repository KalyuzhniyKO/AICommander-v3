"""Explicitly initialized standalone SQLite ledger; never installed into the application DB automatically."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import re
import sqlite3
import time
from uuid import uuid4

from .models import Decision, Outcome, Policy, Request


class BudgetError(Exception):
    def __init__(self, code):
        self.code=code
        super().__init__(code)


def _identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}',value):
        raise BudgetError('invalid_identifier')
    return value


def _dump(value):
    payload = value.model_dump(mode='json', warnings=False)
    # Preserve v1 policy fingerprints for existing zero-fee routes.
    if isinstance(value, Policy):
        for route in payload['routes']:
            if route.get('tariff') is not None and route['tariff'].get('request_micro_usd') == 0:
                route['tariff'].pop('request_micro_usd')
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


SCHEMA = (
    'CREATE TABLE IF NOT EXISTS budget_version(version INTEGER PRIMARY KEY)',
    '''CREATE TABLE IF NOT EXISTS budget_accounts(
        id TEXT PRIMARY KEY, policy_json TEXT NOT NULL, created_ms INTEGER NOT NULL,
        last_seen_ms INTEGER NOT NULL, cooldown_until_ms INTEGER NOT NULL DEFAULT 0,
        quota_streak INTEGER NOT NULL DEFAULT 0, overrun INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS budget_requests(
        operation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES budget_accounts(id),
        request_json TEXT NOT NULL, estimated_micro_usd INTEGER NOT NULL CHECK(estimated_micro_usd>=0),
        state TEXT NOT NULL CHECK(state IN ('reserved','dispatched','completed','quota','rejected','unknown','released')),
        outcome_json TEXT, updated_ms INTEGER NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS budget_requests_account ON budget_requests(account_id)',
)


class BudgetLedger:
    def __init__(self, path: Path, *, clock_ms=None):
        if str(path)==':memory:':raise BudgetError('file_backed_ledger_required')
        self.path=Path(path)
        self.clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)

    @classmethod
    def for_connection(cls, conn: sqlite3.Connection, *, clock_ms=None):
        """Use the caller's application connection; nested work never commits it."""
        ledger = cls.__new__(cls)
        ledger.path = None
        ledger._connection = conn
        ledger.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        return ledger

    @contextmanager
    def _shared_transaction(self, *, write, initialize):
        conn = self._connection
        nested = conn.in_transaction
        savepoint = 'budget_' + uuid4().hex
        opened = False
        try:
            conn.execute('SAVEPOINT ' + savepoint if nested else ('BEGIN IMMEDIATE' if write else 'BEGIN'))
            opened = True
            if not initialize and [r[0] for r in conn.execute('SELECT version FROM budget_version')] != [1]:
                raise BudgetError('unsupported_ledger_version')
            yield conn
            conn.execute('RELEASE SAVEPOINT ' + savepoint) if nested else conn.commit()
        except BaseException as error:
            if opened:
                if nested:
                    conn.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
                    conn.execute('RELEASE SAVEPOINT ' + savepoint)
                else:
                    conn.rollback()
            if isinstance(error, sqlite3.Error):
                raise BudgetError('storage_error') from None
            raise

    @contextmanager
    def _transaction(self, *, write=True, initialize=False):
        if getattr(self, '_connection', None) is not None:
            with self._shared_transaction(write=write, initialize=initialize) as conn:
                yield conn
            return
        if not initialize and not self.path.is_file():
            raise BudgetError('ledger_uninitialized')
        conn=None
        try:
            conn=sqlite3.connect(self.path,timeout=5)
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if not initialize and [r[0] for r in conn.execute('SELECT version FROM budget_version')]!=[1]:
                raise BudgetError('unsupported_ledger_version')
            yield conn
            conn.commit()
        except sqlite3.Error:
            if conn is not None:conn.rollback()
            raise BudgetError('storage_error') from None
        except BaseException:
            if conn is not None:conn.rollback()
            raise
        finally:
            if conn is not None:conn.close()

    def initialize(self):
        # An explicit operator action on a test/dedicated file; no application migration.
        with self._transaction(initialize=True) as conn:
            for statement in SCHEMA:conn.execute(statement)
            versions=[r[0] for r in conn.execute('SELECT version FROM budget_version')]
            if versions not in ([],[1]):raise BudgetError('unsupported_ledger_version')
            conn.execute('INSERT OR IGNORE INTO budget_version VALUES(1)')

    def _now(self, account=None):
        now=self.clock_ms()
        if type(now) is not int or now<0:raise BudgetError('invalid_clock')
        if account is not None and now<account['last_seen_ms']:raise BudgetError('clock_regressed')
        return now

    def create_account(self, account_id, policy: Policy):
        account_id=_identifier(account_id)
        policy=Policy.model_validate_json(_dump(policy)) # Isolate from mutable caller containers.
        encoded=_dump(policy)
        with self._transaction() as conn:
            old=conn.execute('SELECT policy_json FROM budget_accounts WHERE id=?',(account_id,)).fetchone()
            if old:
                if old['policy_json']!=encoded:raise BudgetError('policy_conflict')
                return
            now=self._now()
            conn.execute('INSERT INTO budget_accounts(id,policy_json,created_ms,last_seen_ms) VALUES(?,?,?,?)',(account_id,encoded,now,now))

    def _account(self,conn,account_id):
        row=conn.execute('SELECT * FROM budget_accounts WHERE id=?',(_identifier(account_id),)).fetchone()
        if row is None:raise BudgetError('account_not_found')
        try:policy=Policy.model_validate_json(row['policy_json'])
        except ValueError:raise BudgetError('ledger_corrupt') from None
        return row,policy

    def _request(self,conn,operation_id):
        row=conn.execute('SELECT * FROM budget_requests WHERE operation_id=?',(_identifier(operation_id),)).fetchone()
        if row is None:raise BudgetError('reservation_not_found')
        return row

    def _totals(self,conn,account_id):
        rows=conn.execute("SELECT * FROM budget_requests WHERE account_id=? AND state!='released'",(account_id,)).fetchall()
        tokens=committed=known_cost=dispatched=0
        cost_complete=True
        states={}
        for row in rows:
            request=Request.model_validate_json(row['request_json'])
            outcome=Outcome.model_validate_json(row['outcome_json']) if row['outcome_json'] else None
            usage=outcome.usage if outcome else None
            tokens += usage.input_tokens+usage.output_tokens if usage else request.input_tokens+request.output_tokens
            actual=usage.cost_micro_usd if usage else None
            committed += actual if actual is not None else row['estimated_micro_usd']
            if row['state']!='reserved':
                dispatched+=1
                if actual is None:cost_complete=False
                else:known_cost+=actual
            states[row['state']]=states.get(row['state'],0)+1
        return {'requests_committed':len(rows),'dispatches_claimed':dispatched,'tokens_committed':tokens,
                'budget_committed_micro_usd':committed,'known_cost_subtotal_micro_usd':known_cost,
                'reported_cost_complete':cost_complete,'reported_cost_micro_usd':known_cost if cost_complete else None,
                'states':states}

    def _gate(self,account,policy,totals,now,request,quote,*,new):
        limits=policy.limits
        if account['overrun']:return 'usage_overrun'
        if totals['states'].get('unknown',0):return 'outcome_unknown'
        if now+request.timeout_seconds*1000 > account['created_ms']+limits.lifetime_seconds*1000:return 'deadline_exhausted'
        if now<account['cooldown_until_ms']:return 'provider_cooldown'
        if totals['requests_committed']+int(new)>limits.max_requests:return 'request_limit'
        if totals['tokens_committed']+(request.input_tokens+request.output_tokens if new else 0)>limits.max_total_tokens:return 'token_budget'
        if quote > 0 and totals['budget_committed_micro_usd']+(quote if new else 0)>limits.max_micro_usd:return 'money_budget'
        return None

    def reserve(self, account_id, request: Request) -> Decision:
        request=Request.model_validate_json(_dump(request))
        encoded=_dump(request)
        with self._transaction() as conn:
            account,policy=self._account(conn,account_id)
            now=self._now(account)
            conn.execute('UPDATE budget_accounts SET last_seen_ms=? WHERE id=?',(now,account_id))
            old=conn.execute('SELECT * FROM budget_requests WHERE operation_id=?',(request.operation_id,)).fetchone()
            if old:
                if old['account_id']!=account_id or old['request_json']!=encoded:raise BudgetError('operation_conflict')
                return Decision(allowed=False,code='reservation_exists',operation_id=request.operation_id,
                    estimated_micro_usd=old['estimated_micro_usd'],state=old['state'])
            route=next((r for r in policy.routes if r.role==request.role and r.model_id==request.model_id),None)
            code=None;quote=None
            if route is None:code='route_not_allowed'
            elif route.tariff is None:code='pricing_unknown'
            else:
                quote=route.tariff.quote(request.input_tokens,request.output_tokens)
                paid=bool(route.tariff.request_micro_usd or route.tariff.input_micro_usd_per_million or route.tariff.output_micro_usd_per_million)
                if paid and not policy.limits.allow_paid:code='paid_approval_required'
                elif request.input_tokens>policy.limits.max_input_tokens:code='input_limit'
                elif request.output_tokens>policy.limits.max_output_tokens:code='output_limit'
                elif request.timeout_seconds>policy.limits.max_request_seconds:code='timeout_limit'
                else:code=self._gate(account,policy,self._totals(conn,account_id),now,request,quote,new=True)
            if code:return Decision(allowed=False,code=code,operation_id=request.operation_id,
                estimated_micro_usd=quote,cooldown_until_ms=account['cooldown_until_ms'] if code=='provider_cooldown' else None)
            conn.execute("INSERT INTO budget_requests VALUES(?,?,?,?,'reserved',NULL,?)",(request.operation_id,account_id,encoded,quote,now))
            return Decision(allowed=True,code='allowed_declared_free' if not paid else 'allowed_paid_approved',
                operation_id=request.operation_id,estimated_micro_usd=quote,state='reserved',created=True)

    def claim(self, operation_id) -> Request:
        """Persist permission exactly once BEFORE any external call. No takeover/retry."""
        with self._transaction() as conn:
            row=self._request(conn,operation_id)
            if row['state']!='reserved':raise BudgetError('dispatch_already_claimed_or_closed')
            account,policy=self._account(conn,row['account_id']);now=self._now(account)
            request=Request.model_validate_json(row['request_json'])
            code=self._gate(account,policy,self._totals(conn,account['id']),now,request,row['estimated_micro_usd'],new=False)
            if not code:
                conn.execute("UPDATE budget_requests SET state='dispatched',updated_ms=? WHERE operation_id=?",(now,operation_id))
            conn.execute('UPDATE budget_accounts SET last_seen_ms=? WHERE id=?',(now,account['id']))
        if code:raise BudgetError(code)
        return request

    def release_unclaimed(self, operation_id):
        with self._transaction() as conn:
            row=self._request(conn,operation_id)
            if row['state']=='released':return
            if row['state']!='reserved':raise BudgetError('cannot_release_dispatched_request')
            account,_=self._account(conn,row['account_id']);now=self._now(account)
            conn.execute("UPDATE budget_requests SET state='released',updated_ms=? WHERE operation_id=?",(now,operation_id))
            conn.execute('UPDATE budget_accounts SET last_seen_ms=? WHERE id=?',(now,account['id']))

    def record(self,operation_id,outcome: Outcome):
        outcome=Outcome.model_validate_json(_dump(outcome))
        encoded=_dump(outcome)
        with self._transaction() as conn:
            row=self._request(conn,operation_id)
            if row['outcome_json']:
                if row['outcome_json']==encoded:return
                raise BudgetError('outcome_conflict')
            if row['state']!='dispatched':raise BudgetError('request_not_dispatched')
            account,policy=self._account(conn,row['account_id']);now=self._now(account)
            request=Request.model_validate_json(row['request_json'])
            usage=outcome.usage
            overrun=bool(usage and (usage.input_tokens>request.input_tokens or usage.output_tokens>request.output_tokens
                or (usage.cost_micro_usd is not None and usage.cost_micro_usd>row['estimated_micro_usd'])))
            streak=account['quota_streak'];cooldown=account['cooldown_until_ms']
            if outcome.kind=='quota':
                streak+=1
                exponential=min(policy.limits.max_backoff_seconds,policy.limits.base_backoff_seconds*(2**min(streak-1,20)))
                cooldown=max(cooldown,now+max(exponential,outcome.retry_after_seconds or 0)*1000)
            elif outcome.kind=='completed' and now>=cooldown:streak=0
            conn.execute('UPDATE budget_requests SET state=?,outcome_json=?,updated_ms=? WHERE operation_id=?',(outcome.kind,encoded,now,operation_id))
            conn.execute('UPDATE budget_accounts SET last_seen_ms=?,quota_streak=?,cooldown_until_ms=?,overrun=MAX(overrun,?) WHERE id=?',
                (now,streak,cooldown,int(overrun),account['id']))

    def report(self,account_id):
        with self._transaction(write=False) as conn:
            account,policy=self._account(conn,account_id)
            totals=self._totals(conn,account_id)
            return {'schema_version':'1.0','scope':'offline_admission_accounting','currency':'USD',
                'account_id':account_id,'policy_sha256':sha256(account['policy_json'].encode()).hexdigest(),
                'limits':policy.limits.model_dump(),'created_ms':account['created_ms'],
                'deadline_ms':account['created_ms']+policy.limits.lifetime_seconds*1000,
                'cooldown_until_ms':account['cooldown_until_ms'],'quota_streak':account['quota_streak'],
                'overrun':bool(account['overrun']),**totals,
                'remaining_requests':max(0,policy.limits.max_requests-totals['requests_committed']),
                'remaining_tokens':max(0,policy.limits.max_total_tokens-totals['tokens_committed']),
                'remaining_micro_usd':max(0,policy.limits.max_micro_usd-totals['budget_committed_micro_usd'])}
