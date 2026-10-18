import math
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import threading
from types import MappingProxyType
from contextlib import contextmanager
import uuid

from .models import CommandSpec, ExecutionError, Snapshot, Workspace
from .process import clean_environment, run_command
from .redaction import Redactor


def _no_links(path: Path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ExecutionError("Symlinks and reparse points are forbidden")


def _within(path: Path, root: Path):
    _no_links(root)
    _no_links(path)
    if not path.resolve().is_relative_to(root.resolve()) or path.resolve() == root.resolve():
        raise ExecutionError("Path must be strictly inside the allowed root")


def _tree_no_links(root: Path):
    _no_links(root)
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            entry = Path(directory) / name
            _no_links(entry)
            if entry.is_file() and entry.stat().st_nlink > 1:
                raise ExecutionError("Hard-linked files are forbidden in fixture directories")


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def relative_parts(relative_path: str):
    """The same portable path policy for controlled edits and literal staging."""
    if not isinstance(relative_path, str) or "\0" in relative_path:
        raise ExecutionError("Invalid workspace-relative path")
    relative = PureWindowsPath(relative_path)
    if (not relative.parts or relative.is_absolute() or relative.drive or relative.root
            or any(p.lower() in {"..", ".git", "git~1"} or ":" in p or p.rstrip(" .") != p
                   or p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
                   for p in relative.parts)):
        raise ExecutionError("Invalid workspace-relative path")
    return relative.parts


class OfflineExecutor:
    """Session-scoped manager for disposable, trusted repositories only.

    Policy is owner configuration. There is no HTTP route or model-facing shell.
    This is NOT an OS sandbox: approved code still runs with the user's OS rights.
    """

    def __init__(self, *, allowed_root: Path, repository: Path, pool: Path,
                 commands: dict[str, CommandSpec], secrets=()):
        self.root = Path(allowed_root).absolute()
        self.repository = Path(repository).absolute()
        self.pool = Path(pool).absolute()
        if not self.root.is_dir():
            raise ExecutionError("Allowed root must already exist")
        _within(self.repository, self.root)
        _within(self.pool, self.root)
        self.repository = self.repository.resolve()
        self.pool = self.pool.resolve()
        if (self.pool.is_relative_to(self.repository) or self.repository.is_relative_to(self.pool)):
            raise ExecutionError("Repository and worktree pool must not overlap")
        if not (self.repository / ".git").is_dir():
            raise ExecutionError("Use a disposable primary checkout with a local .git directory")
        _no_links(self.repository / ".git")
        self.pool.mkdir(parents=True, exist_ok=True)
        self._pool_identity = _identity(self.pool)
        self._repo_identity = _identity(self.repository)
        self._lock = threading.Lock()
        self._workspaces = {}
        self.redact = Redactor(secrets)
        self.git = shutil.which("git")
        if not self.git:
            raise ExecutionError("Git executable not found")
        self.git = str(Path(self.git).resolve())
        self.commands = {}
        for name, spec in commands.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                raise ExecutionError("Invalid command identifier")
            if (not isinstance(spec, CommandSpec) or not isinstance(spec.argv, tuple)
                    or not spec.argv or any(not isinstance(a, str) or "\0" in a for a in spec.argv)
                    or not Path(spec.argv[0]).is_absolute() or not Path(spec.argv[0]).is_file()
                    or Path(spec.argv[0]).suffix.lower() in {".bat", ".cmd", ".ps1", ".sh"}
                    or Path(spec.argv[0]).stem.lower() in {"cmd", "powershell", "pwsh", "sh", "bash", "zsh", "dash"}
                    or not isinstance(spec.timeout_seconds, (int, float))
                    or not math.isfinite(spec.timeout_seconds) or not 0 < spec.timeout_seconds <= 300
                    or not isinstance(spec.max_output_bytes, int) or not 256 <= spec.max_output_bytes <= 1048576):
                raise ExecutionError("Invalid approved command specification")
            executable = Path(spec.argv[0])
            _no_links(executable)
            if os.name == "nt":
                # Store app aliases/brokered venv launchers can escape Job inheritance.
                # Require a native executable (for stdlib fixtures: sys.base_prefix/python.exe).
                alias = "microsoft/windowsapps"
                config = executable.parent.parent / "pyvenv.cfg"
                if (alias in str(executable).replace("\\", "/").lower()
                        or executable.stem.lower() in {"py", "pyw"}
                        or config.is_file() and alias in config.read_text(encoding="utf-8").replace("\\", "/").lower()):
                    raise ExecutionError("Brokered Python launcher is unsupported; approve a direct interpreter")
            self.commands[name] = spec
        self.commands = MappingProxyType(self.commands)
        self._validate_repository()

    @contextmanager
    def _exclusive(self):
        if not self._lock.acquire(blocking=False):
            raise ExecutionError("Executor is busy; cancel active command before cleanup")
        try:
            self._validate_roots()
            yield
        finally:
            self._lock.release()

    def _validate_roots(self):
        _within(self.repository, self.root)
        _within(self.pool, self.root)
        if _identity(self.pool) != self._pool_identity or _identity(self.repository) != self._repo_identity:
            raise ExecutionError("Repository or pool directory was replaced")

    def _git(self, cwd, *args, accepted=(0,)):
        argv = (self.git, "-c", "core.hooksPath=" + os.devnull,
                "-c", "core.fsmonitor=false", "-c", "core.attributesFile=" + os.devnull,
                "-c", "core.autocrlf=false", "-c", "submodule.recurse=false", *args)
        result = run_command("git", "git", CommandSpec(argv, 15, 1048576),
                             cwd, clean_environment(self.pool), threading.Event(), lambda s: s)
        if result.outcome != "exited" or result.exit_code not in accepted or result.output_truncated:
            raise ExecutionError("Git operation failed or exceeded its output/time limit")
        return result.stdout

    def _validate_repository(self):
        # Reject local includes, checkout filters and alternate object stores before Git checkout.
        # Global/system config and attribute files are disabled by the child environment.
        _tree_no_links(self.repository)
        for name in ("config", "config.worktree"):
            config = self.repository / ".git" / name
            if config.exists() and re.search(r"(?im)^\s*\[\s*(?:include|includeIf|filter)\b", config.read_text(encoding="utf-8")):
                raise ExecutionError("Repository includes and checkout filters are forbidden")
        alternates = self.repository / ".git" / "objects" / "info" / "alternates"
        if alternates.exists():
            raise ExecutionError("External object stores are forbidden")
        for argument in ("--show-toplevel", "--absolute-git-dir", "--git-common-dir"):
            value = self._git(self.repository, "rev-parse", argument).strip()
            actual = (self.repository / value).resolve()
            expected = self.repository if argument == "--show-toplevel" else self.repository / ".git"
            if actual != expected.resolve():
                raise ExecutionError("Git repository points outside its primary checkout")

    def create(self, task_id: str) -> Workspace:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", task_id):
            raise ExecutionError("Invalid task identifier")
        with self._exclusive():
            self._validate_repository()
            base = self._git(self.repository, "rev-parse", "HEAD").strip()
            tree = self._git(self.repository, "ls-tree", "-r", base)
            if any(line.startswith(("120000 ", "160000 ")) for line in tree.splitlines()):
                raise ExecutionError("Symlinks and submodules are forbidden in the fixture")
            key = uuid.uuid4().hex
            branch = f"codex/offline-{task_id}-{key}"
            path = self.pool / key
            private = self.pool / (key + "-env")
            _within(path, self.pool)
            private.mkdir()
            try:
                self._git(self.repository, "worktree", "add", "-b", branch, str(path), base)
            except ExecutionError:
                # Never recursively delete a partially created checkout on an ambiguous failure.
                # Any residual worktree/ref is retained for manual inspection.
                private.rmdir()
                raise
            workspace = Workspace(key, task_id, branch, str(path), base)
            self._workspaces[key] = (workspace, _identity(path), private, _identity(private))
            return workspace

    def _owned(self, key):
        if key not in self._workspaces:
            raise ExecutionError("Unknown workspace; cleanup only accepts this manager's IDs")
        workspace, identity, private, private_identity = self._workspaces[key]
        path = Path(workspace.path)
        _within(path, self.pool)
        _within(private, self.pool)
        if _identity(path) != identity or _identity(private) != private_identity:
            raise ExecutionError("Owned directory was replaced")
        _tree_no_links(path)
        _tree_no_links(private)
        metadata = path / ".git"
        if not metadata.is_file():
            raise ExecutionError("Worktree Git metadata is missing")
        pointer = metadata.read_text(encoding="utf-8").strip()
        expected = self.repository / ".git" / "worktrees" / key
        if pointer != "gitdir: " + str(expected).replace("\\", "/") and pointer != "gitdir: " + str(expected):
            raise ExecutionError("Worktree Git metadata was replaced")
        _tree_no_links(expected)
        if (expected / "commondir").read_text(encoding="utf-8").strip() != "../..":
            raise ExecutionError("Worktree common directory was replaced")
        if self._git(path, "symbolic-ref", "--quiet", "HEAD").strip() != "refs/heads/" + workspace.branch:
            raise ExecutionError("Workspace is no longer on its owned branch")
        return workspace, path, private

    def write_text(self, key: str, relative_path: str, content: str):
        """Controlled fixture edits; does not accept absolute paths or Git metadata."""
        with self._exclusive():
            _, path, _ = self._owned(key)
            target = path.joinpath(*relative_parts(relative_path))
            _within(target, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def stage(self, key: str, relative_paths: tuple[str, ...]):
        """Stage exactly owner-selected regular files, never a Git glob or add-all."""
        if (not isinstance(relative_paths, tuple) or not 1 <= len(relative_paths) <= 32):
            raise ExecutionError("Approve between 1 and 32 paths")
        parts = [relative_parts(name) for name in relative_paths]
        with self._exclusive():
            _, path, _ = self._owned(key)
            for components in parts:
                target = path.joinpath(*components)
                _within(target, path)
                if not target.is_file():
                    raise ExecutionError("Staging requires regular files")
            self._git(path, "--literal-pathspecs", "add", "--",
                      *("/".join(p) for p in parts))

    def snapshot(self, key: str) -> Snapshot:
        with self._exclusive():
            workspace, path, _ = self._owned(key)
            head = self._git(path, "rev-parse", "HEAD").strip()
            status = self._git(path, "status", "--porcelain=v1", "--untracked-files=all")
            diff = self._git(path, "diff", "--no-ext-diff", "--no-textconv", "--no-color", workspace.base_commit, "--")
            return Snapshot(workspace.id, workspace.base_commit, head, self.redact(status), self.redact(diff))

    def review_evidence(self, key: str):
        """Bound tracked bytes to Git evidence; untracked source cannot be reviewed.

        Ignored, untracked outputs are excluded. Only trusted fixture code is
        supported: this is not a defense against code racing reads or changing
        and restoring files while a test runs. Limits fail closed.
        """
        with self._exclusive():
            workspace, path, _ = self._owned(key)
            head = self._git(path, "rev-parse", "HEAD").strip()
            status = self._git(path, "status", "--porcelain=v1", "--untracked-files=all")
            diff = self._git(path, "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                             workspace.base_commit, "--")
            flags = self._git(path, "ls-files", "-v", "-z").split("\0")
            if any(entry and (entry[0].islower() or entry[0] == "S") for entry in flags):
                raise ExecutionError("QA forbids assume-unchanged and skip-worktree index entries")
            names = self._git(path, "ls-files", "-z").split("\0")
            if len(names) > 4097:
                raise ExecutionError("QA evidence has too many files")
            files = []
            total = 0
            for name in sorted(set(n for n in names if n)):
                target = path / name
                _within(target, path)
                if not target.exists():
                    files.append((name, None))
                    continue
                size = target.stat().st_size
                total += size
                if not target.is_file() or total > 8 * 1024 * 1024:
                    raise ExecutionError("QA evidence exceeds its file limit")
                with target.open("rb") as stream:
                    data = stream.read(8 * 1024 * 1024 + 1)
                if len(data) != size:
                    raise ExecutionError("QA evidence changed while reading")
                files.append((name, hashlib.sha256(data).hexdigest()))
            payload = [workspace.id, workspace.base_commit, head, status, diff, files]
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()
            snapshot = Snapshot(workspace.id, workspace.base_commit, head,
                                self.redact(status), self.redact(diff))
            return snapshot, digest, not any(line.startswith("?? ") for line in status.splitlines())

    def run(self, key: str, command_id: str, *, cancel: threading.Event | None = None):
        with self._exclusive():
            if command_id not in self.commands:
                raise ExecutionError("Command is not in the approved allowlist")
            workspace, path, private = self._owned(key)
            return run_command(workspace.id, command_id, self.commands[command_id], path,
                               clean_environment(private), cancel or threading.Event(), self.redact)

    def cleanup(self, key: str, *, discard_changes: bool = False):
        with self._exclusive():
            workspace, path, private = self._owned(key)
            head = self._git(path, "rev-parse", "HEAD").strip()
            # Include ignored files: test outputs are still data and need explicit discard.
            dirty = self._git(path, "status", "--porcelain=v1", "--untracked-files=all", "--ignored")
            if (dirty or head != workspace.base_commit or any(private.iterdir())) and not discard_changes:
                raise ExecutionError("Workspace contains changes; explicit discard is required")
            args = ["worktree", "remove"]
            if discard_changes:
                args.append("--force")
            self._git(self.repository, *args, str(path))
            # Compare-and-delete only the unique branch we own. Never prune other worktrees.
            self._git(self.repository, "update-ref", "-d", "refs/heads/" + workspace.branch, head)
            _within(private, self.pool)
            _tree_no_links(private)
            shutil.rmtree(private)
            del self._workspaces[key]
