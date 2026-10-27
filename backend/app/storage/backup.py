"""Explicit workspace-scoped snapshots. Stdlib only; no app startup, migrations or external effects."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import tempfile
import time
import zipfile

DB_NAME='state.sqlite3'
MANIFEST_NAME='manifest.json'
MAX_MANIFEST=64*1024
DEFAULT_MAX_BYTES=256*1024*1024
_monotonic=time.monotonic
APP_TABLES={'tasks','rounds','role_outputs','model_errors','model_status'}
FLOW_TABLES={'taskflow_runs','taskflow_stages','taskflow_artifacts','taskflow_events','taskflow_commands'}
BUDGET_TABLES={'budget_accounts','budget_requests','budget_version'}
SPEND_TABLES={'spend_migrations','spend_scopes','spend_permissions','spend_events','spend_approvals'}
COUNT_TABLES=APP_TABLES|FLOW_TABLES|BUDGET_TABLES|SPEND_TABLES|{'taskflow_operations','projects','owner_decisions'}


class BackupError(Exception):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


class _Deadline:
    def __init__(self,seconds):
        if type(seconds) is not int or not 1<=seconds<=120:raise BackupError('invalid_limits')
        self.end=_monotonic()+seconds
    def check(self):
        if _monotonic()>=self.end:raise BackupError('operation_timeout')
    def interrupted(self):return int(_monotonic()>=self.end)


def _limit(value):
    if type(value) is not int or not 1<=value<=1024**3:raise BackupError('invalid_limits')
    return value


def _root(root):
    root=Path(root).resolve(strict=True)
    if not root.is_dir() or root==Path(root.anchor) or root==Path.home().resolve():
        raise BackupError('invalid_workspace')
    return root


def _path(root,value,*,output=False):
    candidate=Path(value)
    if '..' in candidate.parts:raise BackupError('path_outside_workspace')
    if not candidate.is_absolute():candidate=root/candidate
    try:parts=candidate.relative_to(root).parts
    except ValueError:raise BackupError('path_outside_workspace') from None
    current=root
    for part in parts:
        current=current/part
        if current.is_symlink():raise BackupError('symlink_not_allowed')
    # Resolve native redirects too (e.g. Windows junctions are not is_symlink).
    try:candidate.resolve(strict=False).relative_to(root)
    except ValueError:raise BackupError('path_outside_workspace') from None
    if output:
        if candidate.exists():raise BackupError('destination_exists')
        if not candidate.parent.is_dir():raise BackupError('destination_parent_missing')
        if any(Path(str(candidate)+suffix).exists() or Path(str(candidate)+suffix).is_symlink() for suffix in ('-wal','-shm','-journal')):
            raise BackupError('destination_sidecar_exists')
    else:
        if not candidate.is_file() or candidate.stat().st_nlink!=1:raise BackupError('invalid_source')
        # SQLite may open associated files even through a read-only main connection.
        for suffix in ('-wal','-shm','-journal'):
            sidecar=Path(str(candidate)+suffix)
            try:info=sidecar.lstat()
            except FileNotFoundError:continue # A normal WAL checkpoint may remove it.
            if stat.S_ISLNK(info.st_mode):raise BackupError('symlink_not_allowed')
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise BackupError('invalid_source')
    return candidate


@contextmanager
def _protected():
    try:yield
    except BackupError:raise
    except (sqlite3.Error,OSError,ValueError,TypeError,zipfile.BadZipFile,RuntimeError):
        # Never echo file paths, SQL, row contents, ZIP comments or exception text.
        raise BackupError('invalid_or_unreadable_snapshot') from None


def _hash(path,deadline):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):
            deadline.check();digest.update(chunk)
    return digest.hexdigest()


def _readonly(path,deadline):
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.05)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.set_progress_handler(deadline.interrupted,1000)
        return conn
    except BaseException:
        conn.close()
        raise


def _versions(conn,tables,table):
    if table not in tables:return []
    rows=conn.execute('SELECT version FROM '+table+' ORDER BY version').fetchmany(17)
    if len(rows)>16 or any(type(r[0]) is not int for r in rows):raise BackupError('unsupported_schema')
    return [r[0] for r in rows]


def _inspect(path,deadline,max_bytes):
    deadline.check()
    size=path.stat().st_size
    if size>max_bytes:raise BackupError('database_too_large')
    with path.open('rb') as stream:
        if stream.read(16)!=b'SQLite format 3\x00':raise BackupError('invalid_database')
    with closing(_readonly(path,deadline)) as conn:
        try:
            if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise BackupError('integrity_check_failed')
            if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:raise BackupError('foreign_key_check_failed')
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            app=APP_TABLES<=tables;budget=BUDGET_TABLES<=tables
            if not app and not budget:raise BackupError('unsupported_database')
            if BUDGET_TABLES & tables and not budget:raise BackupError('unsupported_schema')
            flow=_versions(conn,tables,'taskflow_migrations');panel=_versions(conn,tables,'panel_migrations');bv=_versions(conn,tables,'budget_version')
            if conn.execute('PRAGMA user_version').fetchone()[0]!=0:raise BackupError('unsupported_schema')
            if flow not in ([],[1],[1,2],[1,2,3]) or panel not in ([],[1]) or bv not in ([],[1]):raise BackupError('unsupported_schema')
            if ('taskflow_migrations' in tables and not flow) or ('panel_migrations' in tables and not panel):raise BackupError('unsupported_schema')
            if flow and not FLOW_TABLES<=tables:raise BackupError('unsupported_schema')
            if len(flow)>=2 and 'taskflow_operations' not in tables:raise BackupError('unsupported_schema')
            if not flow and FLOW_TABLES & tables:raise BackupError('unsupported_schema')
            if budget and bv!=[1]:raise BackupError('unsupported_schema')
            spending=_versions(conn,tables,'spend_migrations')
            if SPEND_TABLES & tables and (not SPEND_TABLES<=tables or spending!=[1] or not budget):raise BackupError('unsupported_schema')
            counts={name:conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in sorted(COUNT_TABLES & tables)}
            active={}
            for table,column,states in (
                ('tasks','status',('running','stopping')),
                ('taskflow_runs','status',('running',)),
                ('taskflow_operations','state',('running','result_ready','unknown')),
                ('budget_requests','state',('reserved','dispatched','unknown')),
                ('spend_permissions','state',('reserved','dispatched','unknown'))):
                if table in tables:
                    for state in states:
                        active[table+'.'+state]=conn.execute('SELECT COUNT(*) FROM '+table+' WHERE '+column+'=?',(state,)).fetchone()[0]
            deadline.check()
            versions={'taskflow':flow,'panel':panel,'budget':bv}
            if spending:versions['spending']=spending
            return {'kind':'app_and_budget' if app and budget else 'app' if app else 'budget',
                'versions':versions,'row_counts':counts,
                'unresolved_counts':active,'operator_recovery_required':any(active.values())}
        except sqlite3.Error:
            deadline.check()
            raise BackupError('invalid_database') from None


def _new_file(path):
    return os.fdopen(os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600),'w+b')


def _publish(root,staged,destination,deadline):
    deadline.check();_path(root,destination,output=True)
    try:os.link(staged,destination)
    except FileExistsError:raise BackupError('destination_exists') from None
    # The staging link disappears on cleanup. Existing destinations are never replaced.


def _manifest(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise BackupError('invalid_manifest')
            result[key]=value
        return result
    try:manifest=json.loads(data,object_pairs_hook=unique)
    except (ValueError,UnicodeError):raise BackupError('invalid_manifest') from None
    keys={'schema_version','created_utc','sqlite_runtime_version','sqlite_bytes','sqlite_sha256','metadata'}
    if not isinstance(manifest,dict) or set(manifest)!=keys:raise BackupError('invalid_manifest')
    if manifest['schema_version']!='1.0':raise BackupError('unsupported_snapshot_version')
    if type(manifest['sqlite_bytes']) is not int or manifest['sqlite_bytes']<=0:raise BackupError('invalid_manifest')
    if not isinstance(manifest['sqlite_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',manifest['sqlite_sha256']):raise BackupError('invalid_manifest')
    if not isinstance(manifest['sqlite_runtime_version'],str) or not re.fullmatch(r'\d+\.\d+\.\d+',manifest['sqlite_runtime_version']):raise BackupError('invalid_manifest')
    created=manifest['created_utc']
    if not isinstance(created,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',created):raise BackupError('invalid_manifest')
    try:datetime.strptime(created,'%Y-%m-%dT%H:%M:%SZ')
    except ValueError:raise BackupError('invalid_manifest') from None
    return manifest


def _unpack(bundle,target,deadline,max_bytes):
    if bundle.stat().st_size>max_bytes+MAX_MANIFEST+256*1024:raise BackupError('archive_too_large')
    # Our bounded two-member format has no ZIP64, comments or trailing data.
    # Check counts/central-directory size BEFORE ZipFile allocates member objects.
    with bundle.open('rb') as stream:
        if bundle.stat().st_size<22:raise BackupError('invalid_archive_members')
        stream.seek(-22,os.SEEK_END)
        end=struct.unpack('<4s4H2LH',stream.read(22))
    signature,disk,start_disk,on_disk,total,central_bytes,central_offset,comment_bytes=end
    if (signature!=b'PK\x05\x06' or disk or start_disk or on_disk!=2 or total!=2
            or central_bytes>1024 or comment_bytes or central_offset+central_bytes!=bundle.stat().st_size-22):
        raise BackupError('invalid_archive_members')
    with zipfile.ZipFile(bundle,'r') as archive:
        infos=archive.infolist()
        if len(infos)!=2 or {i.filename for i in infos}!={DB_NAME,MANIFEST_NAME} or archive.comment:
            raise BackupError('invalid_archive_members')
        for info in infos:
            filetype=stat.S_IFMT(info.external_attr>>16)
            if (info.compress_type!=zipfile.ZIP_STORED or info.flag_bits & 1 or info.is_dir()
                    or filetype not in (0,stat.S_IFREG) or info.extra or info.comment or info.compress_size!=info.file_size):
                raise BackupError('invalid_archive_members')
        mi=archive.getinfo(MANIFEST_NAME);di=archive.getinfo(DB_NAME)
        if mi.file_size>MAX_MANIFEST:raise BackupError('invalid_manifest')
        manifest=_manifest(archive.read(MANIFEST_NAME))
        if di.file_size>max_bytes:raise BackupError('database_too_large')
        if di.file_size!=manifest['sqlite_bytes']:raise BackupError('checksum_mismatch')
        with archive.open(DB_NAME) as source,_new_file(target) as output:
            size=0
            while chunk:=source.read(1024*1024):
                deadline.check();size+=len(chunk)
                if size>max_bytes:raise BackupError('database_too_large')
                output.write(chunk)
            output.flush();os.fsync(output.fileno())
        if size!=manifest['sqlite_bytes'] or _hash(target,deadline)!=manifest['sqlite_sha256']:raise BackupError('checksum_mismatch')
        metadata=_inspect(target,deadline,max_bytes)
        if json.dumps(metadata,sort_keys=True)!=json.dumps(manifest['metadata'],sort_keys=True):raise BackupError('metadata_mismatch')
        return manifest


def create_backup(source,output,*,root,timeout_seconds=30,max_bytes=DEFAULT_MAX_BYTES):
    with _protected():
        root=_root(root);source=_path(root,source);output=_path(root,output,output=True)
        max_bytes=_limit(max_bytes);deadline=_Deadline(timeout_seconds)
        with tempfile.TemporaryDirectory(prefix='.aicommander-snapshot-',dir=output.parent) as scratch:
            scratch=Path(scratch);db=scratch/DB_NAME;bundle=scratch/'bundle.zip'
            with _new_file(db):pass
            with closing(_readonly(source,deadline)) as src,closing(sqlite3.connect(db)) as dst:
                page_size=src.execute('PRAGMA page_size').fetchone()[0]
                def progress(status,remaining,total):
                    deadline.check()
                    if total*page_size>max_bytes:raise BackupError('database_too_large')
                src.backup(dst,pages=64,progress=progress,sleep=.05)
                dst.execute('PRAGMA journal_mode=DELETE')
            metadata=_inspect(db,deadline,max_bytes)
            manifest={'schema_version':'1.0','created_utc':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'sqlite_runtime_version':sqlite3.sqlite_version,'sqlite_bytes':db.stat().st_size,
                'sqlite_sha256':_hash(db,deadline),'metadata':metadata}
            with _new_file(bundle) as raw:
                with zipfile.ZipFile(raw,'w',compression=zipfile.ZIP_STORED) as archive:
                    archive.write(db,DB_NAME)
                    archive.writestr(MANIFEST_NAME,json.dumps(manifest,sort_keys=True).encode())
                raw.flush();os.fsync(raw.fileno())
            _unpack(bundle,scratch/'check.sqlite3',deadline,max_bytes)
            _publish(root,bundle,output,deadline)
            return {'operation':'create','verified':True,'manifest':manifest}


def verify_backup(source,*,root,timeout_seconds=30,max_bytes=DEFAULT_MAX_BYTES):
    with _protected():
        root=_root(root);source=_path(root,source);deadline=_Deadline(timeout_seconds);max_bytes=_limit(max_bytes)
        with tempfile.TemporaryDirectory(prefix='.aicommander-verify-',dir=root) as scratch:
            manifest=_unpack(source,Path(scratch)/DB_NAME,deadline,max_bytes)
        return {'operation':'verify','verified':True,'manifest':manifest}


def restore_backup(source,output,*,root,timeout_seconds=30,max_bytes=DEFAULT_MAX_BYTES):
    with _protected():
        root=_root(root);source=_path(root,source);output=_path(root,output,output=True)
        deadline=_Deadline(timeout_seconds);max_bytes=_limit(max_bytes)
        with tempfile.TemporaryDirectory(prefix='.aicommander-restore-',dir=output.parent) as scratch:
            db=Path(scratch)/DB_NAME
            manifest=_unpack(source,db,deadline,max_bytes)
            _publish(root,db,output,deadline)
        return {'operation':'restore','verified':True,'workers_must_be_stopped_before_use':True,
            'external_effects_reconciliation_required':True,'manifest':manifest}


class _Parser(argparse.ArgumentParser):
    def error(self,message):raise BackupError('invalid_arguments')


def main(argv=None):
    parser=_Parser(description='Explicit offline SQLite snapshots; no overwrite, app startup or recovery execution.')
    parser.add_argument('action',choices=('create','verify','restore'))
    parser.add_argument('--root',required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--output')
    parser.add_argument('--timeout-seconds',type=int,default=30)
    parser.add_argument('--max-bytes',type=int,default=DEFAULT_MAX_BYTES)
    try:
        args=parser.parse_args(argv)
        kwargs={'root':args.root,'timeout_seconds':args.timeout_seconds,'max_bytes':args.max_bytes}
        if args.action=='verify':
            if args.output is not None:raise BackupError('invalid_arguments')
            report=verify_backup(args.source,**kwargs)
        else:
            if args.output is None:raise BackupError('invalid_arguments')
            report=(create_backup if args.action=='create' else restore_backup)(args.source,args.output,**kwargs)
        print(json.dumps(report,ensure_ascii=False,sort_keys=True));return 0
    except BackupError as exc:
        print(json.dumps({'ok':False,'code':exc.code}));return 2


if __name__=='__main__':raise SystemExit(main())
