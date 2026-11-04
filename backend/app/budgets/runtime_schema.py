"""Idempotent application budget tables; existing data is never cleared."""
from .ledger import BudgetLedger, BudgetError

TABLES = {'spend_migrations','spend_scopes','spend_permissions','spend_events','spend_approvals'}

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS spend_migrations(version INTEGER PRIMARY KEY)',
    '''CREATE TABLE IF NOT EXISTS spend_scopes(
        id TEXT PRIMARY KEY, policy_hash TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
        paid_references_json TEXT NOT NULL DEFAULT '[]')''',
    '''CREATE TABLE IF NOT EXISTS spend_permissions(
        id TEXT PRIMARY KEY, task_id INTEGER, project_id INTEGER, run_id INTEGER,
        origin TEXT NOT NULL, role TEXT NOT NULL, provider TEXT NOT NULL, model_id TEXT NOT NULL,
        policy_hash TEXT NOT NULL, context_sha256 TEXT NOT NULL,
        reservations_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('reserved','dispatched','completed','quota','rejected','unknown','released')),
        policy_json TEXT NOT NULL, usage_evidence_json TEXT,
        created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS spend_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT, task_id INTEGER, project_id INTEGER,
        category TEXT NOT NULL, code TEXT NOT NULL, provider TEXT NOT NULL, model_id TEXT NOT NULL,
        created_ms INTEGER NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS spend_approvals(
        id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL,
        created_ms INTEGER NOT NULL, period_start_ms INTEGER NOT NULL, period_end_ms INTEGER NOT NULL,
        policy_hash TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
        UNIQUE(scope_id,revision))''',
    'CREATE INDEX IF NOT EXISTS spend_permissions_task ON spend_permissions(task_id,created_ms)',
    'CREATE INDEX IF NOT EXISTS spend_events_task ON spend_events(task_id,id)',
)


def initialize_runtime_budget(conn):
    ledger = BudgetLedger.for_connection(conn)
    with ledger._transaction(initialize=True):
        ledger.initialize()
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if 'spend_migrations' in existing:
            if [row[0] for row in conn.execute('SELECT version FROM spend_migrations')] != [1] or not TABLES <= existing:
                raise BudgetError('unsupported_runtime_budget_version')
        elif TABLES & existing:
            raise BudgetError('incomplete_runtime_budget_schema')
        for statement in SCHEMA:
            conn.execute(statement)
        conn.execute('INSERT OR IGNORE INTO spend_migrations VALUES(1)')


def preflight_runtime_budget(conn):
    """Reject unknown/partial financial state before unrelated app DDL commits."""
    existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    budget_tables = {'budget_version','budget_accounts','budget_requests'}
    if budget_tables & existing:
        if not budget_tables <= existing or [row[0] for row in conn.execute('SELECT version FROM budget_version')] != [1]:
            raise BudgetError('unsupported_ledger_version')
    if TABLES & existing:
        if not TABLES <= existing or [row[0] for row in conn.execute('SELECT version FROM spend_migrations')] != [1]:
            raise BudgetError('unsupported_runtime_budget_version')
