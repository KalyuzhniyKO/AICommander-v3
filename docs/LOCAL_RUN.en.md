# Local executor → QA → review request workflow

[Русский](LOCAL_RUN.md)

`LocalQaSession` connects the existing OfflineExecutor and QaRunner in a fixed,
explicit sequence against trusted disposable repositories. Owner-approved text
edits are applied in a new worktree, staged with literal Git pathspecs, tested by
real pytest/Django checks and exported as a private review-request bundle.
No model/Director planning, Codex CLI, independent Reviewer, HTTP/UI/taskflow,
budget, database migration or GitHub publishing is added. Full #15/#16 remain open.

`python -m backend.app.localrun.demo --root NEW_DIRECTORY --python ABSOLUTE_DIRECT_INTERPRETER
--scenario passing` creates an entirely synthetic repository and baseline commit,
then tests a small receive() edit in a separate worktree. `failing` deliberately
fails the real pytest assertion. Existing roots are never reused/overwritten.
The parent directory must already exist; the interpreter must have pytest/Django
and satisfy executor's direct-executable policy. Use a fresh POSIX venv created
with --copies; do not overwrite an existing environment. See the Russian guide
for complete Linux/macOS and PowerShell commands.

A general `python -m backend.app.localrun run` CLI requires explicit root,
repository, pool, evidence, plan and interpreter paths, plus optional trusted test
Django settings. Plan fields are task_id/requirements/risks/edits only. No arbitrary
shell callback, delete/rename/binary edits, add-all, forced ignored files, automatic
commit/push or cleanup. Limits: 1–32 edits, 1 MiB total contents, 16 KiB each for
requirements/risks. Case/Unicode/separator path aliases are rejected.

The bundle contains exactly requirements.md, changes.diff, qa-report.json,
review-request.md and manifest.json. The manifest is written last, after checking
source/command-plan freshness, and binds identities, source hashes and exact file
checksums. POSIX directories/files use 0700/0600; Windows inherits parent ACLs.
The finished bundle is integrity-checked before returning a result.
Partial writes remain for inspection; this is not a transactional/power-loss-proof
archive. No overwrite, automatic retry/resume or recovery journal.

States: awaiting_review, qa_failed, stale, cancelled, no_changes. Exit 0 from run/demo
means awaiting review, never PR permission; exit 1 is a completed negative outcome,
exit 2 an input/execution/export error. A failed export retains its worktree and
possibly a partial bundle. Demo's baseline commit is synthetic only; intended
worktree changes are never committed by the workflow.

`verify` checks only integrity within an explicit root: fixed members, no links,
bounded regular files, hashes, report hash and identity/diff binding. Limits are
8 MiB/file, 12 MiB total, 16 KiB manifest. Unknown manifest fields/versions, unknown QA versions, duplicate JSON
properties and inconsistent readiness are rejected. Verification exit 0 can apply
to a failed QA bundle. Checksums can be recomputed by outsiders: authentication,
current source verification and readiness are always False in verifier output.

`session.is_current(result)` accepts only its own live result identity and pins
the manifest, source evidence and current approved command plan. It does not turn
failed QA into success and does not provide authority after restart. Even a fresh
passing result requires trusted independent review and owner approval. Every
manifest keeps both required and ready_for_pr/merge False; developer_agent_executed
is False. review-request.md contains a request, not a Reviewer verdict.

Existing trusted-code/OS-rights/ignored-output/race/redaction limitations apply.
Worktrees are not an OS/network sandbox. Never pass production code/settings/data
or credentials. Artifacts are private, not automatically published. Taskflow v1/v2
claims and states remain unchanged.

[Passing](local-run-examples/passing/manifest.json) and
[failing](local-run-examples/failing/manifest.json) examples came from real local
synthetic fixture processes. Their worktrees were explicitly removed afterwards;
they do not prove current source or live Zorin/model acceptance.
[Validation summary](qa-local-run.json).
