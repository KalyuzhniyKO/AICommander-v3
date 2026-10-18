"""One explicit synthetic example, in a new directory only; never a user project."""
import argparse
import json
import os
from pathlib import Path
import shutil
import threading

from ..execution import CommandSpec, ExecutionError, OfflineExecutor
from ..execution.executor import _no_links
from ..execution.process import clean_environment, run_command
from ..execution.redaction import Redactor
from ..qa import python_commands
from .workflow import LocalQaSession, LocalTask, TextEdit


def run_demo(root: Path, python: str, *, scenario: str = 'passing'):
    if scenario not in ('passing', 'failing'):
        raise ExecutionError('Unknown synthetic scenario')
    root = Path(root).absolute()
    _no_links(root)
    git = shutil.which('git')
    if not git:
        raise ExecutionError('Git is required')
    # Exclusive creation before writing anything: existing directories are data.
    root.mkdir(mode=0o700)
    source, evidence, private = root / 'source', root / 'evidence', root / 'bootstrap-env'
    for path in (source, evidence, private): path.mkdir(mode=0o700)
    files = {
        'inventory.py': 'def receive(stock, quantity):\n    return stock - quantity\n',
        'test_inventory.py': 'from inventory import receive\ndef test_receive():\n    assert receive(5, 3) == 8\n',
        '.gitignore': '__pycache__/\n.pytest_cache/\n',
        'qa_settings.py': "SECRET_KEY='synthetic-demo-only'\nINSTALLED_APPS=[]\nDATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}}\n",
    }
    for name, content in files.items(): (source / name).write_text(content, encoding='utf-8')
    for args in (('init','-b','main'), ('config','user.name','AICommander Synthetic Fixture'),
                 ('config','user.email','fixture@example.invalid'), ('add','.'),
                 ('commit','-m','Synthetic baseline')):
        spec = CommandSpec((str(Path(git).resolve()), '-c', 'core.hooksPath=' + os.devnull,
                            '-c', 'core.autocrlf=false', *args), 15, 65536)
        result = run_command('bootstrap','git',spec,source,clean_environment(private),threading.Event(),Redactor())
        if result.outcome != 'exited' or result.exit_code != 0 or result.output_truncated:
            raise ExecutionError('Synthetic repository bootstrap failed')
    executor = OfflineExecutor(allowed_root=root, repository=source, pool=root / 'worktrees',
                               commands=python_commands(python, django_settings='qa_settings'))
    session = LocalQaSession(executor, django=True, evidence_directory=evidence)
    body = '    return stock + quantity\n' if scenario == 'passing' else '    return stock + quantity + 1\n'
    task = LocalTask('receive-demo', 'receive(5, 3) должен возвращать 8.',
        'Только синтетический пример. Это не warehouse_config, не Codex CLI и не OS sandbox. '
        'Django settings пустого fixture не подтверждают проверки настоящего склада.',
        (TextEdit('inventory.py', 'def receive(stock, quantity):\n' + body),
         TextEdit('notes/change.txt', 'Синтетическое изменение для проверки передачи новых файлов.\n')))
    return session, session.run(task)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Одноразовый синтетический прогон executor → QA → review request')
    parser.add_argument('--root', required=True, help='Новый каталог внутри выделенного workspace; parent уже существует')
    parser.add_argument('--python', required=True, help='Одобренный прямой interpreter с pytest и Django')
    parser.add_argument('--scenario', choices=('passing','failing'), default='passing')
    args = parser.parse_args(argv)
    try:
        _, result = run_demo(Path(args.root), args.python, scenario=args.scenario)
        print(json.dumps({'scope':'synthetic_demo','state':result.state,'qa_passed':result.qa_passed,
            'bundle': str(result.bundle.relative_to(Path(args.root).absolute())),
            'workspace_id':result.workspace.id,'branch':result.workspace.branch,
            'manifest_sha256':result.manifest_sha256,'ready_for_pr':False,'ready_for_merge':False}, ensure_ascii=True))
        return 0 if result.state == 'awaiting_review' else 1
    except (ExecutionError, OSError, ValueError):
        print(json.dumps({'error':'synthetic_demo_failed','ready_for_pr':False,'ready_for_merge':False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
