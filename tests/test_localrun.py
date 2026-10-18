"""Real local workflow on disposable repositories. No models, server or network."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import threading
from types import MappingProxyType
import venv

import pytest

from backend.app.execution import ExecutionError, OfflineExecutor
from backend.app.localrun import LocalQaSession, LocalTask, TextEdit, verify_bundle
from backend.app.localrun.workflow import FILES
from backend.app.localrun.__main__ import main
from backend.app.qa import python_commands

GIT = shutil.which('git')
pytestmark = pytest.mark.skipif(GIT is None, reason='Git required')


def git(repo, *args):
    return subprocess.run([GIT, '-c', 'core.autocrlf=false', *args], cwd=repo,
        env=dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                 GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL=''),
        capture_output=True, text=True, encoding='utf-8', check=True).stdout


@pytest.fixture(scope='session')
def direct_python(tmp_path_factory):
    if os.name == 'nt':
        return str(Path(sys.base_prefix) / 'python.exe')
    root = tmp_path_factory.mktemp('localrun-python')
    venv.EnvBuilder(symlinks=False, with_pip=False).create(root)
    packages = root / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    (packages / 'dev-packages.pth').write_text(sysconfig.get_path('purelib') + '\n')
    return str(root / 'bin' / 'python')


@pytest.fixture
def setup(tmp_path, direct_python):
    source, evidence = tmp_path / 'source', tmp_path / 'evidence'
    source.mkdir()
    evidence.mkdir()
    files = {'sample.py': 'VALUE = 1\n',
             'test_sample.py': 'from sample import VALUE\ndef test_value():\n    assert VALUE == 2\n',
             '.gitignore': '__pycache__/\n.pytest_cache/\n',
             'qa_settings.py': "SECRET_KEY='synthetic-only'\nINSTALLED_APPS=[]\nDATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':'never-open.sqlite3'}}\n"}
    for name, content in files.items(): (source / name).write_text(content, encoding='utf-8')
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Local Workflow Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'synthetic baseline')
    return tmp_path, source, evidence, direct_python


def manager(setup, *, django=False):
    root, source, evidence, python = setup
    executor = OfflineExecutor(allowed_root=root, repository=source, pool=root / 'pool',
        commands=python_commands(python, django_settings='qa_settings' if django else None),
        secrets=('literal-sensitive',))
    return executor, LocalQaSession(executor, django=django, evidence_directory=evidence)


def task(value=2, extra=True):
    edits = (TextEdit('sample.py', f'VALUE = {value}\n'),)
    if extra: edits += (TextEdit('notes/new.txt', 'synthetic new file\n'),)
    return LocalTask('fixture-task', 'Значение равно 2. literal-sensitive',
                     'Только синтетический проект; требуется независимое ревью.', edits)


def example(result, name):
    output = os.environ.get('LOCAL_RUN_EXAMPLES')
    if output:
        target = Path(output) / name
        target.mkdir(parents=True, exist_ok=True)
        for file in (*FILES, 'manifest.json'):
            shutil.copyfile(result.bundle / file, target / file)


def test_real_passing_chain_and_stale_source_plan_bundle_detection(setup):
    root, source, evidence, _ = setup
    # Owner edits in the source checkout must survive untouched.
    (source / 'sample.py').write_text('OWNER_WORK = True\n')
    head, index = git(source, 'rev-parse', 'HEAD'), (source / '.git/index').read_bytes()
    executor, session = manager(setup, django=True)
    result = session.run(task())
    verified = verify_bundle(result.bundle, allowed_root=evidence, expected_manifest_sha256=result.manifest_sha256)
    manifest = verified['manifest']
    assert result.state == 'awaiting_review' and result.qa_passed
    assert not verified['authenticated'] and not verified['current_source_verified']
    assert not manifest['ready_for_pr'] and not manifest['ready_for_merge']
    assert manifest['independent_review'] == manifest['owner_approval'] == 'required'
    assert not manifest['developer_agent_executed'] and manifest['code_changed']
    assert session.is_current(result)
    report = json.loads((result.bundle / 'qa-report.json').read_text())
    assert len(report['checks']) == 3 and all(c['exit_code'] == 0 for c in report['checks'])
    assert '1 passed' in report['checks'][0]['stdout']
    assert 'No changes detected' in report['checks'][2]['stdout']
    diff = (result.bundle / 'changes.diff').read_text()
    assert '+VALUE = 2' in diff and 'notes/new.txt' in diff and 'new file mode' in diff
    assert not (Path(result.workspace.path) / 'never-open.sqlite3').exists()
    assert 'literal-sensitive' not in (result.bundle / 'requirements.md').read_text()
    assert (source / 'sample.py').read_text() == 'OWNER_WORK = True\n'
    assert git(source, 'rev-parse', 'HEAD') == head and (source / '.git/index').read_bytes() == index
    assert git(source, 'branch', '--show-current').strip() == 'main'
    if os.name != 'nt':
        assert result.bundle.stat().st_mode & 0o777 == 0o700
        assert all((result.bundle / n).stat().st_mode & 0o777 == 0o600 for n in (*FILES, 'manifest.json'))
    example(result, 'passing')
    with pytest.raises(ExecutionError, match='belong'):
        session.is_current(replace(result))
    executor.write_text(result.workspace.id, 'sample.py', 'VALUE = 3\n')
    assert not session.is_current(result)
    executor.write_text(result.workspace.id, 'sample.py', 'VALUE = 2\n')
    assert session.is_current(result)
    old = executor.commands
    executor.commands = MappingProxyType({**old, 'pytest': replace(old['pytest'], timeout_seconds=29)})
    assert not session.is_current(result)
    executor.commands = old
    assert session.is_current(result)
    (result.bundle / 'requirements.md').write_text('tampered')
    assert not session.is_current(result)
    executor.cleanup(result.workspace.id, discard_changes=True)


def test_real_failing_chain_retains_exit_code_diff_and_review_block(setup):
    executor, session = manager(setup)
    result = session.run(task(3))
    assert result.state == 'qa_failed' and not result.qa_passed
    manifest = verify_bundle(result.bundle, allowed_root=setup[2])['manifest']
    report = json.loads((result.bundle / 'qa-report.json').read_text())
    assert report['checks'][0]['exit_code'] == 1 and '1 failed' in report['checks'][0]['stdout']
    assert '+VALUE = 3' in (result.bundle / 'changes.diff').read_text()
    assert manifest['state'] == 'qa_failed' and not manifest['ready_for_pr']
    assert session.is_current(result)  # current evidence is not a passing QA verdict
    example(result, 'failing')
    executor.cleanup(result.workspace.id, discard_changes=True)


def test_successful_test_modifying_code_cannot_enter_review(setup):
    executor, session = manager(setup)
    changed = LocalTask('mutating', 'Проверить целостность.', 'Только fixture.',
        (TextEdit('test_sample.py', "from pathlib import Path\ndef test_change():\n    Path('sample.py').write_text('VALUE = 5\\n')\n"),))
    result = session.run(changed)
    assert result.state == 'stale' and not result.qa_passed and not session.is_current(result)
    assert verify_bundle(result.bundle, allowed_root=setup[2])['manifest']['source_current_at_export'] is False
    executor.cleanup(result.workspace.id, discard_changes=True)


def test_precancel_has_no_worktree_or_bundle_and_late_cancel_blocks_review(setup, monkeypatch):
    executor, session = manager(setup)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ExecutionError, match='cancelled'):
        session.run(task(), cancel=cancel)
    assert not list(setup[2].iterdir())
    assert len(git(setup[1], 'worktree', 'list', '--porcelain').split('worktree ')) == 2
    cancel.clear()
    original = session.qa.run
    def after_qa(*args, **kwargs):
        report = original(*args, **kwargs)
        cancel.set()
        return report
    monkeypatch.setattr(session.qa, 'run', after_qa)
    result = session.run(task(), cancel=cancel)
    assert result.qa_passed and result.state == 'cancelled'
    assert verify_bundle(result.bundle, allowed_root=setup[2])['manifest']['ready_for_pr'] is False
    executor.cleanup(result.workspace.id, discard_changes=True)


def test_no_op_changes_do_not_promote_successful_qa(setup):
    executor, session = manager(setup)
    unchanged = LocalTask('no-op', 'Пройти существующий тест.', 'Только fixture.',
        (TextEdit('test_sample.py', 'from sample import VALUE\ndef test_value():\n    assert VALUE == 1\n'),))
    # Commit a passing baseline, then approve exactly identical text as an edit.
    (setup[1] / 'test_sample.py').write_text(unchanged.edits[0].content)
    git(setup[1], 'add', 'test_sample.py')
    git(setup[1], 'commit', '-m', 'passing baseline')
    result = session.run(unchanged)
    assert result.qa_passed and result.state == 'no_changes'
    assert not verify_bundle(result.bundle, allowed_root=setup[2])['manifest']['code_changed']
    executor.cleanup(result.workspace.id, discard_changes=True)


@pytest.mark.parametrize('path', ['../outside', '.git/config', '/absolute', r'C:\outside', 'x:stream', '.'])
def test_bad_edit_paths_rejected_before_run(path):
    with pytest.raises(ExecutionError): TextEdit(path, 'bad')


def test_duplicate_and_oversized_plan_and_unknown_fields_rejected():
    for edits in ((TextEdit('x.py','a'), TextEdit('X.py','b')),
                  (TextEdit('sub/x.py','a'), TextEdit(r'sub\x.py','b'))):
        with pytest.raises(ExecutionError): LocalTask('id','requirements','risks',edits)
    with pytest.raises(ExecutionError): TextEdit('x', 'a' * (1024 * 1024 + 1))
    with pytest.raises(ExecutionError): LocalTask.from_dict({'task_id':'x','edits':[], 'approval':True})


def test_literal_staging_does_not_expand_git_pathspecs(setup):
    executor, _ = manager(setup)
    workspace = executor.create('literal')
    for name in ('file[ab].py', 'filea.py'):
        executor.write_text(workspace.id, name, 'synthetic\n')
    executor.stage(workspace.id, ('file[ab].py',))
    assert git(workspace.path, 'diff', '--cached', '--name-only').strip() == 'file[ab].py'
    assert '?? filea.py' in executor.snapshot(workspace.id).status
    with pytest.raises(ExecutionError): executor.stage(workspace.id, ('../outside',))
    executor.cleanup(workspace.id, discard_changes=True)


def test_evidence_directory_overlap_and_replacement_rejected(setup):
    executor, session = manager(setup)
    for path in (setup[1], setup[1] / 'nested', setup[0], setup[0] / 'pool'):
        with pytest.raises(ExecutionError): LocalQaSession(executor, django=False, evidence_directory=path)
    setup[2].rename(setup[0] / 'old-evidence')
    setup[2].mkdir()
    with pytest.raises(ExecutionError, match='replaced'): session.run(task())


def test_export_failure_leaves_incomplete_bundle_and_retains_worktree(setup, monkeypatch):
    import backend.app.localrun.workflow as module
    executor, session = manager(setup)
    original = module._write
    def fail_manifest(path, data):
        if path.name == 'manifest.json': raise OSError('synthetic failure')
        original(path, data)
    monkeypatch.setattr(module, '_write', fail_manifest)
    with pytest.raises(OSError): session.run(task())
    bundle, = setup[2].iterdir()
    with pytest.raises(ExecutionError): verify_bundle(bundle, allowed_root=setup[2])
    assert 'codex/offline-' in git(setup[1], 'worktree', 'list')
    # Only this manager's fixture worktree may be explicitly discarded.
    for key in tuple(executor._workspaces): executor.cleanup(key, discard_changes=True)


def test_real_cli_run_and_verify_in_separate_processes(setup):
    root, source, evidence, python = setup
    plan = root / 'plan.json'
    plan.write_text(json.dumps({'task_id':'cli','requirements':'VALUE=2','risks':'Synthetic only',
                                'edits':[{'path':'sample.py','content':'VALUE = 2\n'}]}))
    command = [sys.executable, '-m', 'backend.app.localrun', 'run', '--root', str(root),
        '--repository', str(source), '--pool', str(root / 'pool'), '--evidence', str(evidence),
        '--plan', str(plan), '--python', python]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary['state'] == 'awaiting_review' and not summary['ready_for_pr']
    verification = subprocess.run([sys.executable, '-m', 'backend.app.localrun','verify',
        '--root', str(evidence), '--bundle', str(root / summary['bundle'])],
        capture_output=True, text=True, timeout=15)
    assert verification.returncode == 0
    assert json.loads(verification.stdout)['authenticated'] is False
    assert git(source, 'status', '--porcelain') == ''
    # The CLI deliberately leaves the fixture worktree for inspection.
    workspace = root / 'pool' / summary['workspace_id']
    git(source, 'worktree', 'remove', '--force', str(workspace))
    git(source, 'branch', '-D', summary['branch'])


def test_cli_policy_failure_does_not_echo_input_or_create_worktree(setup, capsys):
    root, source, evidence, python = setup
    plan = root / 'bad.json'
    plan.write_text('{"unexpected":"synthetic-sensitive"}')
    code = main(['run','--root',str(root),'--repository',str(source),'--pool',str(root/'pool'),
                 '--evidence',str(evidence),'--plan',str(plan),'--python',python])
    assert code == 2 and 'synthetic-sensitive' not in capsys.readouterr().out
    assert not list(evidence.iterdir())


@pytest.fixture
def saved_bundle(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'docs/local-run-examples/passing'
    return shutil.copytree(source, tmp_path / 'bundle')


@pytest.mark.parametrize('mode', ['modified', 'missing', 'extra', 'ready', 'future_version',
                                   'bool_version', 'traversal', 'bad_report', 'duplicate',
                                   'deep_json', 'size_limit', 'bool_size', 'invalid_state', 'future_qa', 'inconsistent_exit'])
def test_bundle_tampering_partial_future_and_bounds_fail_closed(saved_bundle, tmp_path, mode):
    path = saved_bundle / 'manifest.json'
    manifest = json.loads(path.read_text())
    if mode == 'modified': (saved_bundle / 'requirements.md').write_text('changed')
    elif mode == 'missing': (saved_bundle / 'qa-report.json').unlink()
    elif mode == 'extra': (saved_bundle / 'extra.txt').write_text('extra')
    elif mode == 'ready': manifest['ready_for_pr'] = True
    elif mode == 'future_version': manifest['schema_version'] = 2
    elif mode == 'bool_version': manifest['schema_version'] = True
    elif mode == 'traversal': manifest['files']['../outside'] = manifest['files'].pop('requirements.md')
    elif mode == 'bool_size': manifest['files']['changes.diff']['bytes'] = True
    elif mode == 'invalid_state': manifest['state'] = 'no_changes'
    elif mode == 'duplicate':
        path.write_text('{"schema_version":1,"schema_version":1}')
    elif mode == 'deep_json': path.write_text('[' * 2000 + ']' * 2000)
    elif mode == 'size_limit':
        with (saved_bundle / 'changes.diff').open('wb') as stream: stream.truncate(8 * 1024 * 1024 + 1)
    elif mode in ('bad_report', 'future_qa', 'inconsistent_exit'):
        qa = saved_bundle / 'qa-report.json'
        report = json.loads(qa.read_text())
        if mode == 'bad_report': report['qa_passed'] = False
        else:
            if mode == 'future_qa': report['schema_version'] = 2
            else: report['checks'][0]['exit_code'] = 1
            value = {k:v for k,v in report.items() if k != 'report_sha256'}
            report['report_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
            manifest['report_sha256'] = report['report_sha256']
        qa.write_text(json.dumps(report))
        raw = qa.read_bytes()
        manifest['files']['qa-report.json'] = {'sha256':hashlib.sha256(raw).hexdigest(), 'bytes':len(raw)}
    if mode not in ('duplicate', 'deep_json'): path.write_text(json.dumps(manifest))
    with pytest.raises(ExecutionError): verify_bundle(saved_bundle, allowed_root=tmp_path)


def test_recomputed_checksums_are_not_identity_or_permission(saved_bundle, tmp_path):
    manifest_path = saved_bundle / 'manifest.json'
    original_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text())
    changed = saved_bundle / 'requirements.md'
    changed.write_text('Forged requirements; checksums alone are not approval.')
    raw = changed.read_bytes()
    manifest['files']['requirements.md'] = {'sha256':hashlib.sha256(raw).hexdigest(), 'bytes':len(raw)}
    manifest_path.write_text(json.dumps(manifest))
    value = verify_bundle(saved_bundle, allowed_root=tmp_path)
    assert value['integrity_valid'] and not value['authenticated']
    assert not value['current_source_verified'] and not value['ready_for_pr'] and not value['ready_for_merge']
    with pytest.raises(ExecutionError, match='manifest changed'):
        verify_bundle(saved_bundle, allowed_root=tmp_path, expected_manifest_sha256=original_digest)


def test_hardlinked_bundle_member_is_not_read(saved_bundle, tmp_path):
    target = saved_bundle / 'changes.diff'
    preserved = tmp_path / 'preserved.diff'
    target.rename(preserved)
    os.link(preserved, target)
    before = preserved.read_bytes()
    with pytest.raises(ExecutionError): verify_bundle(saved_bundle, allowed_root=tmp_path)
    assert preserved.read_bytes() == before


def test_linked_bundle_directory_is_not_followed(saved_bundle, tmp_path):
    linked = tmp_path / 'linked'
    if os.name == 'nt':
        if any(c in str(linked) + str(saved_bundle) for c in '&|<>^()%!\r\n'):
            pytest.skip('Command metacharacters in native junction fixture paths')
        subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(linked),str(saved_bundle)],
                       check=True, capture_output=True)
    else:
        linked.symlink_to(saved_bundle, target_is_directory=True)
    try:
        with pytest.raises(ExecutionError): verify_bundle(linked, allowed_root=tmp_path)
        assert (saved_bundle / 'manifest.json').is_file()
    finally:
        if os.name == 'nt': linked.rmdir()
        else: linked.unlink()


def test_bundle_outside_explicit_root_is_not_read(saved_bundle, tmp_path):
    root = tmp_path / 'other-root'
    root.mkdir()
    with pytest.raises(ExecutionError): verify_bundle(saved_bundle, allowed_root=root)


def test_one_command_demo_runs_real_django_and_never_reuses_existing_root(tmp_path, direct_python):
    from backend.app.localrun.demo import run_demo
    root = tmp_path / 'new-demo'
    session, result = run_demo(root, direct_python)
    assert result.state == 'awaiting_review' and result.qa_passed and session.is_current(result)
    assert 'inventory.py' in (result.bundle / 'changes.diff').read_text()
    report = json.loads((result.bundle / 'qa-report.json').read_text())
    assert len(report['checks']) == 3 and all(c['exit_code'] == 0 for c in report['checks'])
    original = (root / 'source/inventory.py').read_bytes()
    with pytest.raises(FileExistsError): run_demo(root, direct_python)
    assert (root / 'source/inventory.py').read_bytes() == original
    session.executor.cleanup(result.workspace.id, discard_changes=True)


def test_failing_demo_cli_preserves_real_error_and_bundle(tmp_path, direct_python):
    root = tmp_path / 'failing-demo'
    result = subprocess.run([sys.executable,'-m','backend.app.localrun.demo','--root',str(root),
        '--python',direct_python,'--scenario','failing'],capture_output=True,text=True,timeout=120)
    assert result.returncode == 1, result.stdout + result.stderr
    value = json.loads(result.stdout)
    manifest = verify_bundle(root / value['bundle'], allowed_root=root)['manifest']
    assert value['state'] == manifest['state'] == 'qa_failed' and not manifest['ready_for_pr']
    report = json.loads((root / value['bundle'] / 'qa-report.json').read_text())
    assert report['checks'][0]['exit_code'] == 1 and all(c['exit_code'] == 0 for c in report['checks'][1:])
    git(root / 'source','worktree','remove','--force',str(root/'worktrees'/value['workspace_id']))
    git(root / 'source','branch','-D',value['branch'])


def test_example_bundles_remain_valid_with_autocrlf_checkout(tmp_path):
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / 'eol-source'
    source.mkdir()
    shutil.copyfile(project / '.gitattributes', source / '.gitattributes')
    shutil.copytree(project / 'docs/local-run-examples', source / 'docs/local-run-examples')
    # Windows-generated evidence may contain CRLF: Git must preserve those bytes too.
    (source / 'docs/local-run-examples/crlf-probe.txt').write_bytes(b'one\r\ntwo\r\n')
    git(source, 'init','-b','main')
    git(source, 'config','user.name','LF Fixture')
    git(source, 'config','user.email','fixture@example.invalid')
    git(source, 'add','.')
    git(source, 'commit','-m','Synthetic bundle byte fixture')
    checkout = tmp_path / 'crlf-checkout'
    # Later -c overrides helper's portable false: emulate a Windows Git checkout.
    git(source, '-c','core.autocrlf=true','worktree','add','--detach',str(checkout),'HEAD')
    examples = checkout / 'docs/local-run-examples'
    assert (examples / 'crlf-probe.txt').read_bytes() == b'one\r\ntwo\r\n'
    for name in ('passing','failing'):
        assert verify_bundle(examples / name, allowed_root=examples)['integrity_valid']
    git(source, 'worktree','remove','--force',str(checkout))
