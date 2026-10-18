"""Connect trusted text edits, existing worktrees/QA and a review request bundle."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import unicodedata
import uuid

from ..execution import ExecutionError, OfflineExecutor, Workspace
from ..execution.executor import _identity, _no_links, _within, relative_parts
from ..qa import QaRunner
from ..qa.runner import _hash

FILES = ('requirements.md', 'changes.diff', 'qa-report.json', 'review-request.md')
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 12 * 1024 * 1024
STATES = ('awaiting_review', 'qa_failed', 'cancelled', 'stale', 'no_changes')


def _text(value, limit):
    try:
        valid = isinstance(value, str) and len(value.encode('utf-8')) <= limit
    except UnicodeError:
        valid = False
    if not valid:
        raise ExecutionError('Invalid or oversized local task text')


@dataclass(frozen=True)
class TextEdit:
    path: str
    content: str

    def __post_init__(self):
        relative_parts(self.path)
        _text(self.path, 256)
        _text(self.content, 1024 * 1024)


@dataclass(frozen=True)
class LocalTask:
    task_id: str
    requirements: str
    risks: str
    edits: tuple[TextEdit, ...]

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', self.task_id):
            raise ExecutionError('Invalid local task identifier')
        for text in (self.requirements, self.risks):
            _text(text, 16384)
            if not text.strip():
                raise ExecutionError('Declare requirements and risks')
        if (not isinstance(self.edits, tuple) or not 1 <= len(self.edits) <= 32
                or any(type(edit) is not TextEdit for edit in self.edits)):
            raise ExecutionError('Approve between 1 and 32 text edits')
        names = [unicodedata.normalize('NFC', '/'.join(relative_parts(e.path))).casefold() for e in self.edits]
        if len(set(names)) != len(names) or sum(len(e.content.encode('utf-8')) for e in self.edits) > 1024 * 1024:
            raise ExecutionError('Duplicate paths or oversized edit plan')

    @classmethod
    def from_dict(cls, data):
        try:
            if not isinstance(data, dict) or set(data) != {'task_id', 'requirements', 'risks', 'edits'} or not isinstance(data['edits'], list) or not 1 <= len(data['edits']) <= 32:
                raise ValueError
            edits = []
            for edit in data['edits']:
                if not isinstance(edit, dict) or set(edit) != {'path', 'content'}:
                    raise ValueError
                edits.append(TextEdit(**edit))
            return cls(**{k: data[k] for k in ('task_id', 'requirements', 'risks')}, edits=tuple(edits))
        except (TypeError, ValueError):
            raise ExecutionError('Invalid local task document') from None


@dataclass(frozen=True)
class LocalResult:
    run_id: str
    workspace: Workspace
    bundle: Path
    manifest_sha256: str
    state: str
    qa_passed: bool


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _write(path, data):
    if len(data) > MAX_FILE:
        raise ExecutionError('Local artifact exceeds its limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate property')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('Nonfinite JSON value')


def _decode(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_nonfinite)


def _read(path, limit):
    _no_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ExecutionError('Invalid local artifact file')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) != info.st_size or len(data) > limit:
        raise ExecutionError('Local artifact changed or exceeded its limit')
    return data


def verify_bundle(directory: Path, *, allowed_root: Path, expected_manifest_sha256=None):
    """Integrity only, not authenticity, current source, independent review or permission."""
    try:
        root, directory = Path(allowed_root).absolute(), Path(directory).absolute()
        _within(directory, root)
        # Bound enumeration; never recursively walk or follow artifact filenames.
        entries = []
        for entry in directory.iterdir():
            entries.append(entry.name)
            if len(entries) > len(FILES) + 1:
                raise ExecutionError('Unexpected local bundle member')
        if set(entries) != {*FILES, 'manifest.json'}:
            raise ExecutionError('Incomplete local bundle')
        raw_manifest = _read(directory / 'manifest.json', 16384)
        if expected_manifest_sha256 is not None and _sha(raw_manifest) != expected_manifest_sha256:
            raise ExecutionError('Local bundle manifest changed')
        manifest = _decode(raw_manifest)
        expected = {'schema_version', 'scope', 'run_id', 'task_id', 'workspace_id', 'branch',
                    'qa_run_id', 'base_commit', 'head', 'evidence_sha256', 'command_plan_sha256',
                    'report_sha256', 'edit_plan_sha256', 'edit_count', 'state', 'qa_passed',
                    'source_current_at_export', 'code_changed', 'developer_agent_executed',
                    'independent_review', 'owner_approval', 'ready_for_pr', 'ready_for_merge', 'files'}
        if (not isinstance(manifest, dict) or set(manifest) != expected
                or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                or manifest['scope'] != 'trusted_local_fixture' or manifest['state'] not in STATES
                or any(manifest[k] is not False for k in ('ready_for_pr', 'ready_for_merge', 'developer_agent_executed'))
                or any(manifest[k] != 'required' for k in ('independent_review', 'owner_approval'))
                or any(type(manifest[k]) is not bool for k in ('qa_passed', 'source_current_at_export', 'code_changed'))
                or not isinstance(manifest['files'], dict) or set(manifest['files']) != set(FILES)):
            raise ExecutionError('Invalid local manifest contract')
        for key in ('run_id', 'workspace_id', 'qa_run_id'):
            if not isinstance(manifest[key], str) or not re.fullmatch('[0-9a-f]{32}', manifest[key]):
                raise ExecutionError('Invalid local run identity')
        for key in ('evidence_sha256', 'command_plan_sha256', 'report_sha256', 'edit_plan_sha256'):
            if not isinstance(manifest[key], str) or not re.fullmatch('[0-9a-f]{64}', manifest[key]):
                raise ExecutionError('Invalid local evidence hash')
        for key in ('base_commit', 'head'):
            if not isinstance(manifest[key], str) or not re.fullmatch('[0-9a-f]{40}', manifest[key]):
                raise ExecutionError('Invalid local source commit')
        if (not isinstance(manifest['task_id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', manifest['task_id'])
                or manifest['branch'] != f"codex/offline-{manifest['task_id']}-{manifest['workspace_id']}"
                or type(manifest['edit_count']) is not int or not 1 <= manifest['edit_count'] <= 32):
            raise ExecutionError('Invalid local task binding')
        contents, total = {}, len(raw_manifest)
        for name in FILES:
            specification = manifest['files'][name]
            if (not isinstance(specification, dict) or set(specification) != {'sha256', 'bytes'}
                    or type(specification['bytes']) is not int or not isinstance(specification['sha256'], str)):
                raise ExecutionError('Invalid artifact metadata')
            data = _read(directory / name, MAX_FILE)
            total += len(data)
            if total > MAX_TOTAL or manifest['files'][name] != {'sha256': _sha(data), 'bytes': len(data)}:
                raise ExecutionError('Local artifact checksum mismatch or limit exceeded')
            contents[name] = data
        report = _decode(contents['qa-report.json'])
        if not isinstance(report, dict) or report.get('report_sha256') != _hash({k:v for k,v in report.items() if k != 'report_sha256'}):
            raise ExecutionError('QA report checksum mismatch')
        if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
                or report.get('scope') != 'offline_trusted_fixture'
                or type(report.get('source_unchanged')) is not bool or type(report.get('evidence_complete')) is not bool
                or report.get('required_checks') not in (['pytest'], ['pytest','django_check','django_migrations'])
                or not isinstance(report.get('checks'), list) or len(report['checks']) != len(report['required_checks'])
                or not isinstance(report.get('blockers'), list) or any(not isinstance(v,str) for v in report['blockers'])):
            raise ExecutionError('Invalid QA report contract')
        for name, check in zip(report['required_checks'], report['checks']):
            if not isinstance(check, dict) or check.get('command_id') != name or type(check.get('passed')) is not bool:
                raise ExecutionError('Invalid QA check identity')
            passed = (check.get('outcome') == 'exited' and type(check.get('exit_code')) is int
                      and check['exit_code'] == 0 and check.get('output_truncated') is False)
            if check['passed'] != passed:
                raise ExecutionError('Inconsistent QA check result')
        passed = (all(c['passed'] for c in report['checks']) and report['source_unchanged']
                  and report['evidence_complete'] and not report['blockers'])
        if report.get('qa_passed') is not passed:
            raise ExecutionError('Inconsistent QA summary')
        for field in ('workspace_id', 'base_commit', 'head', 'evidence_sha256', 'command_plan_sha256', 'report_sha256', 'qa_passed'):
            if report.get(field) != manifest[field] or type(report.get(field)) is not type(manifest[field]):
                raise ExecutionError('QA and local manifest do not match')
        if (report.get('run_id') != manifest['qa_run_id'] or report.get('ready_for_pr') is not False
                or report.get('independent_review') != 'required' or report.get('owner_approval') != 'required'
                or contents['changes.diff'].decode('utf-8') != report.get('diff')):
            raise ExecutionError('Invalid local QA handoff')
        if manifest['code_changed'] != bool(report['diff']) or (manifest['source_current_at_export'] and not (report['source_unchanged'] and report['evidence_complete'])):
            raise ExecutionError('Inconsistent local source state')
        valid_state = {
            'awaiting_review': manifest['qa_passed'] and manifest['source_current_at_export'] and manifest['code_changed'],
            'qa_failed': not manifest['qa_passed'] and manifest['source_current_at_export'],
            'stale': not manifest['source_current_at_export'],
            'cancelled': True,
            'no_changes': manifest['qa_passed'] and manifest['source_current_at_export'] and not manifest['code_changed'],
        }[manifest['state']]
        if not valid_state:
            raise ExecutionError('Invalid local review state')
        return {'integrity_valid': True, 'authenticated': False, 'current_source_verified': False,
                'ready_for_pr': False, 'ready_for_merge': False, 'manifest': manifest,
                'manifest_sha256': _sha(raw_manifest)}
    except ExecutionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ExecutionError('Local bundle could not be verified') from None


class LocalQaSession:
    """Fixed sequence over existing helpers; trusted inputs, no models or shell callback.

    No auto cleanup/retry/resume/commit/publish. Interrupted worktrees and partial
    bundles are retained; a manifest is written last. Not an OS sandbox/journal.
    """
    def __init__(self, executor: OfflineExecutor, *, django: bool, evidence_directory: Path):
        self.executor, self.qa = executor, QaRunner(executor, django=django)
        self.evidence = Path(evidence_directory).absolute()
        _within(self.evidence, executor.root)
        if not self.evidence.is_dir() or any(self.evidence.is_relative_to(p) or p.is_relative_to(self.evidence)
                                           for p in (executor.repository, executor.pool)):
            raise ExecutionError('Use a separate existing evidence directory')
        self._evidence_identity = _identity(self.evidence)
        self._lock, self._results = threading.Lock(), {}

    def _root(self):
        _within(self.evidence, self.executor.root)
        if _identity(self.evidence) != self._evidence_identity:
            raise ExecutionError('Evidence directory was replaced')

    def _plan_hash(self):
        return _hash({name: asdict(self.executor.commands[name]) for name in self.qa.required})

    def _current(self, key, report):
        try:
            _, digest, complete = self.executor.review_evidence(key)
            return (complete and report['source_unchanged'] and digest == report['evidence_sha256']
                    and self._plan_hash() == report['command_plan_sha256'])
        except (ExecutionError, OSError, KeyError):
            return False

    def run(self, task: LocalTask, *, cancel: threading.Event | None = None):
        if type(task) is not LocalTask or self.executor.redact(task.task_id) != task.task_id:
            raise ExecutionError('Use a validated task without credentials in its identifier')
        if not self._lock.acquire(blocking=False):
            raise ExecutionError('Local workflow is busy')
        try:
            self._root()
            cancel = cancel or threading.Event()
            if cancel.is_set():
                raise ExecutionError('Local workflow cancelled before setup')
            workspace = self.executor.create(task.task_id)
            for edit in task.edits:
                if cancel.is_set():
                    break
                self.executor.write_text(workspace.id, edit.path, edit.content)
            if not cancel.is_set():
                self.executor.stage(workspace.id, tuple(edit.path for edit in task.edits))
            report = self.qa.run(workspace.id, cancel=cancel)
            run_id = uuid.uuid4().hex
            self._root()
            directory = self.evidence / run_id
            directory.mkdir(mode=0o700)
            bundle_identity = _identity(directory)
            requirements = self.executor.redact('# Требования\n\n' + task.requirements + '\n\n## Ограничения и риски\n\n' + task.risks + '\n')
            review = ('# Запрос независимого ревью\n\nВердикт ещё не получен.\n\n'
                      'Проверить requirements.md, changes.diff и qa-report.json: выполнение требований, '
                      'ошибки, безопасность, покрытие и применимость тестов. Diff и логи отредактированы; '
                      'не применять diff автоматически. Сверить source/evidence/command/report hashes '
                      'из manifest.json с актуальным worktree.\n\n'
                      'QA не является review или разрешением владельца. Нужны отдельные доверенные '
                      'решения ревьюера и владельца; библиотека их не создаёт.\n')
            data = {'requirements.md': requirements.encode('utf-8'), 'changes.diff': report['diff'].encode('utf-8'),
                    'qa-report.json': _json_bytes(report), 'review-request.md': review.encode('utf-8')}
            if sum(map(len, data.values())) > MAX_TOTAL - 16384:
                raise ExecutionError('Local bundle exceeds its size limit')
            for name, content in data.items():
                self._root()
                _no_links(directory)
                if _identity(directory) != bundle_identity:
                    raise ExecutionError("Bundle directory was replaced")
                _write(directory / name, content)
            current = self._current(workspace.id, report)
            changed = bool(report['diff'])
            state = ('cancelled' if cancel.is_set() else 'stale' if not current else
                     'qa_failed' if not report['qa_passed'] else 'no_changes' if not changed else 'awaiting_review')
            manifest = dict(schema_version=1, scope='trusted_local_fixture', run_id=run_id,
                task_id=task.task_id, workspace_id=workspace.id, branch=workspace.branch,
                qa_run_id=report['run_id'], **{k: report[k] for k in ('base_commit','head','evidence_sha256','command_plan_sha256','report_sha256','qa_passed')},
                edit_plan_sha256=_hash([asdict(e) for e in task.edits]), edit_count=len(task.edits),
                state=state, source_current_at_export=current, code_changed=changed,
                developer_agent_executed=False, independent_review='required', owner_approval='required',
                ready_for_pr=False, ready_for_merge=False,
                files={name:{'sha256':_sha(content),'bytes':len(content)} for name, content in data.items()})
            raw = _json_bytes(manifest)
            self._root()
            _no_links(directory)
            if _identity(directory) != bundle_identity:
                raise ExecutionError('Bundle directory was replaced')
            _write(directory / 'manifest.json', raw)
            verify_bundle(directory, allowed_root=self.evidence, expected_manifest_sha256=_sha(raw))
            result = LocalResult(run_id, workspace, directory, _sha(raw), state, report['qa_passed'])
            self._results[run_id] = result
            return result
        finally:
            self._lock.release()

    def is_current(self, result: LocalResult):
        """Fresh evidence only. Even True is never review, owner approval or readiness."""
        if type(result) is not LocalResult or self._results.get(result.run_id) is not result:
            raise ExecutionError('Result does not belong to this local session')
        try:
            self._root()
            verified = verify_bundle(result.bundle, allowed_root=self.evidence,
                                     expected_manifest_sha256=result.manifest_sha256)
            report = _decode(_read(result.bundle / 'qa-report.json', MAX_FILE))
            return verified['manifest']['source_current_at_export'] and self._current(result.workspace.id, report)
        except (ExecutionError, OSError):
            return False
