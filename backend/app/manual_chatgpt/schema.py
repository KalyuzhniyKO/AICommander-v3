from contextlib import contextmanager


class ManualError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


TABLES={'manual_chatgpt_version','manual_source_cache','manual_selections','manual_exports','manual_answers'}
SCHEMA=(
 'CREATE TABLE manual_chatgpt_version(version INTEGER PRIMARY KEY)',
 '''CREATE TABLE manual_source_cache(id TEXT PRIMARY KEY,attempt_ms INTEGER NOT NULL,succeeded_ms INTEGER,
      error_code TEXT,payload_json TEXT,attempt_id TEXT NOT NULL)''',
 '''CREATE TABLE manual_selections(id TEXT PRIMARY KEY,task_id INTEGER NOT NULL REFERENCES tasks(id),
      actor TEXT NOT NULL,created_ms INTEGER NOT NULL,request_sha256 TEXT NOT NULL,payload_json TEXT NOT NULL)''',
 '''CREATE TABLE manual_exports(id TEXT PRIMARY KEY,task_id INTEGER NOT NULL REFERENCES tasks(id),
      selection_id TEXT NOT NULL REFERENCES manual_selections(id),actor TEXT NOT NULL,created_ms INTEGER NOT NULL,
      context_sha256 TEXT NOT NULL,prompt TEXT NOT NULL,request_sha256 TEXT NOT NULL)''',
 '''CREATE TABLE manual_answers(id TEXT PRIMARY KEY,export_id TEXT NOT NULL REFERENCES manual_exports(id),
      actor TEXT NOT NULL,created_ms INTEGER NOT NULL,answer TEXT NOT NULL,answer_sha256 TEXT NOT NULL)''',
 'CREATE INDEX manual_selections_task ON manual_selections(task_id,created_ms)',
 'CREATE INDEX manual_exports_task ON manual_exports(task_id,created_ms)')


@contextmanager
def transaction(conn):
    if conn.in_transaction:raise ManualError('transaction_conflict')
    conn.execute('BEGIN IMMEDIATE')
    try:yield;conn.commit()
    except BaseException:conn.rollback();raise


def preflight(conn):
    existing={r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if TABLES & existing:
        if not TABLES<=existing or [r[0] for r in conn.execute('SELECT version FROM manual_chatgpt_version')]!=[1]:
            raise ManualError('unsupported_manual_schema')


def initialize(conn):
    preflight(conn)
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='manual_chatgpt_version'").fetchone():return
    with transaction(conn):
        preflight(conn)
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='manual_chatgpt_version'").fetchone():return
        for statement in SCHEMA:conn.execute(statement)
        conn.execute('INSERT INTO manual_chatgpt_version VALUES(1)')
