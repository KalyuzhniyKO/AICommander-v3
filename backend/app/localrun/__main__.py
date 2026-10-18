"""Explicit trusted workspace CLI. No .env, credential discovery or HTTP startup."""
import argparse
import json
from pathlib import Path

from ..execution import ExecutionError, OfflineExecutor
from ..execution.executor import _within
from ..qa import python_commands
from .workflow import LocalQaSession, LocalTask, _decode, _read, verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description='Локальный fixture-прогон и пакет для ревью')
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run', help='Изменения доверенного fixture → настоящий QA → запрос ревью')
    for name in ('root', 'repository', 'pool', 'evidence', 'plan', 'python'):
        run.add_argument('--' + name, required=True)
    run.add_argument('--django-settings', default=None)
    check = sub.add_parser('verify', help='Проверить целостность пакета, не разрешение на PR')
    check.add_argument('--root', required=True)
    check.add_argument('--bundle', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'verify':
            value = verify_bundle(Path(args.bundle), allowed_root=Path(args.root))
            print(json.dumps(value, ensure_ascii=True))
            return 0
        root = Path(args.root).absolute()
        plan = Path(args.plan).absolute()
        _within(plan, root)
        task = LocalTask.from_dict(_decode(_read(plan, 8 * 1024 * 1024)))
        executor = OfflineExecutor(allowed_root=root, repository=Path(args.repository),
            pool=Path(args.pool), commands=python_commands(args.python, django_settings=args.django_settings))
        session = LocalQaSession(executor, django=args.django_settings is not None,
                                 evidence_directory=Path(args.evidence))
        result = session.run(task)
        print(json.dumps({'run_id': result.run_id, 'workspace_id': result.workspace.id,
            'branch': result.workspace.branch, 'bundle': str(result.bundle.relative_to(root)),
            'state': result.state, 'qa_passed': result.qa_passed,
            'manifest_sha256': result.manifest_sha256, 'ready_for_pr': False, 'ready_for_merge': False}, ensure_ascii=True))
        return 0 if result.state == 'awaiting_review' else 1
    except (ExecutionError, OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({'error': 'local_workflow_failed', 'ready_for_pr': False, 'ready_for_merge': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
