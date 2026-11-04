"""A denied POSIX group kill must not bypass reaping or become successful QA."""
import os
import subprocess
import sys
import threading

import pytest

from backend.app.execution import CommandSpec, ExecutionError
from backend.app.execution import process as process_module


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
@pytest.mark.parametrize("program", ["print('fixture completed')", "import time; time.sleep(30)"])
def test_denied_group_cleanup_reaps_direct_child_and_fails_closed(tmp_path, monkeypatch, program):
    children = []
    popen = subprocess.Popen

    def launch(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def denied(*args):
        raise PermissionError("synthetic-private-cleanup-detail")

    monkeypatch.setattr(process_module.subprocess, "Popen", launch)
    monkeypatch.setattr(process_module.os, "killpg", denied)
    spec = CommandSpec((sys.executable, "-I", "-c", program), 1, 1024)
    with pytest.raises(ExecutionError, match="containment unconfirmed") as failure:
        process_module.run_command("fixture", "check", spec, tmp_path,
            process_module.clean_environment(tmp_path), threading.Event(), lambda value: value)

    assert "synthetic-private" not in str(failure.value)
    assert len(children) == 1 and children[0].returncode is not None
    assert children[0].stdout.closed and children[0].stderr.closed
