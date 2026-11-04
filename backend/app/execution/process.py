"""Bounded capture, minimal environment and process-tree lifetime management."""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from .models import CommandResult, ExecutionError


def clean_environment(private_dir: Path) -> dict[str, str]:
    # Do not inherit PATH, credentials, proxies, PYTHONPATH, Git options or .env.
    env = {"HOME": str(private_dir), "USERPROFILE": str(private_dir),
           "TMP": str(private_dir), "TEMP": str(private_dir), "TMPDIR": str(private_dir),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
    if os.name == "nt":
        env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
        env["SystemDrive"] = Path(env["SystemRoot"]).drive
        env["LOCALAPPDATA"] = str(private_dir)
        env["APPDATA"] = str(private_dir)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_ALLOW_PROTOCOL="", GIT_ATTR_NOSYSTEM="1")
    return env


class _WindowsJob:
    def __init__(self):
        from ctypes import wintypes

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.name = "Local\\AICommander-" + uuid.uuid4().hex
        self.handle = self.api.CreateJobObjectW(None, self.name)
        if not self.handle:
            raise ExecutionError("Cannot create process job")
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ExecutionError("Cannot configure process job")

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


class _Capture:
    def __init__(self, stream, limit, overflow):
        self.stream, self.limit, self.overflow = stream, limit, overflow
        self.data = bytearray()
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            while chunk := self.stream.read(4096):
                remaining = self.limit - len(self.data)
                self.data.extend(chunk[:max(0, remaining)])
                if len(chunk) > remaining:
                    self.overflow.set()
        finally:
            self.stream.close()


def run_command(workspace_id, command_id, spec, cwd, env, cancel, redact):
    start = time.monotonic()
    if cancel.is_set():
        return CommandResult(workspace_id, command_id, "cancelled", None, 0, "", "", False)
    process = None
    job = None
    captures = []
    overflow = threading.Event()
    outcome = "exited"
    try:
        argv = spec.argv
        if os.name == "nt":
            job = _WindowsJob()
            # Bypass a venv launcher that could spawn a child before job assignment.
            argv = (str(Path(sys.base_prefix) / "python.exe"), "-I", "-S",
                    str(Path(__file__).with_name("_worker.py")), job.name)
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, shell=False,
            stdin=subprocess.PIPE if job else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if job:
            process.stdin.write((json.dumps(spec.argv) + "\n").encode("utf-8"))
            process.stdin.close()
        # Extra retained bytes protect literal redaction across the display boundary.
        captures = [_Capture(stream, spec.max_output_bytes + 16384, overflow)
                    for stream in (process.stdout, process.stderr)]
        while process.poll() is None:
            if cancel.is_set():
                outcome = "cancelled"
                break
            if time.monotonic() - start >= spec.timeout_seconds:
                outcome = "timed_out"
                break
            if overflow.is_set():
                outcome = "output_limit"
                break
            cancel.wait(0.02)
    except (OSError, ValueError):
        raise ExecutionError("Test process could not be started") from None
    finally:
        cleanup_failed = False
        # Also reap background descendants when the direct process exits normally.
        if job:
            job.close()
        elif process:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                # Group cleanup may be denied on macOS. Still reap our direct child,
                # but never report successful containment for an unconfirmed tree.
                cleanup_failed = True
        if process:
            try:
                if process.poll() is None:
                    process.kill()
            except ProcessLookupError:
                pass
            except OSError:
                cleanup_failed = True
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                cleanup_failed = True
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            for capture in captures:
                capture.thread.join(timeout=5)
            if any(capture.thread.is_alive() for capture in captures):
                raise ExecutionError("Process pipes remain open; command containment failed")
            if not captures:
                process.stdout.close()
                process.stderr.close()
        if cleanup_failed:
            raise ExecutionError("Process tree cleanup failed; command containment unconfirmed") from None
    truncated = overflow.is_set() or any(len(c.data) > spec.max_output_bytes for c in captures)
    if overflow.is_set() and outcome == "exited":
        outcome = "output_limit"
    output = [redact(c.data.decode("utf-8", errors="replace")).encode("utf-8")[:spec.max_output_bytes]
              .decode("utf-8", errors="ignore") for c in captures]
    return CommandResult(workspace_id, command_id, outcome, process.returncode,
                         round(time.monotonic() - start, 3), *output, truncated)
