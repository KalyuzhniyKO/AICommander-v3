"""Explicitly configured, read-only saved QA bundles; no taskflow attachment."""
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException

from ..execution import ExecutionError
from ..execution.executor import _no_links
from .workflow import read_bundle

MAX_ENTRIES = 100
RUN_ID = re.compile(r'[0-9a-f]{32}')


def create_review_router(settings):
    router = APIRouter(prefix='/local-reviews')

    def root():
        configured = settings().local_review_root
        if not configured:
            raise HTTPException(404, 'Local review storage is disabled')
        path = Path(configured)
        try:
            if not path.is_absolute():
                raise ExecutionError('Absolute review root required')
            _no_links(path)
            if not path.is_dir():
                raise ExecutionError('Review root unavailable')
        except (ExecutionError, OSError, ValueError):
            raise HTTPException(503, 'Local review storage unavailable') from None
        return path

    @router.get('')
    def list_reviews():
        if not settings().local_review_root:
            return {'enabled': False, 'runs': []}
        directory = root()
        runs = []
        try:
            # Count ALL direct entries; never traverse recursively or infer paths.
            for count, entry in enumerate(directory.iterdir(), start=1):
                if count > MAX_ENTRIES:
                    raise ExecutionError('Review index limit exceeded')
                if RUN_ID.fullmatch(entry.name):
                    _no_links(entry)
                    if entry.is_dir():
                        runs.append({'run_id': entry.name})
        except (ExecutionError, OSError, ValueError):
            raise HTTPException(503, 'Local review index unavailable') from None
        return {'enabled': True, 'runs': sorted(runs, key=lambda item: item['run_id'])}

    @router.get('/{run_id}')
    def get_review(run_id: str):
        if not RUN_ID.fullmatch(run_id):
            raise HTTPException(404, 'Local review not found')
        directory = root()
        try:
            bundle = read_bundle(directory / run_id, allowed_root=directory)
            if bundle['manifest']['run_id'] != run_id:
                raise ExecutionError('Review identity mismatch')
        except (ExecutionError, OSError, ValueError):
            # Incomplete, absent and tampered bundles never expose unverified text.
            raise HTTPException(409, 'Local review unavailable or integrity check failed') from None
        return bundle

    return router
