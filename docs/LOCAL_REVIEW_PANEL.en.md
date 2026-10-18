# Saved QA bundle viewer — preparation for #19

[Русский](LOCAL_REVIEW_PANEL.md)

A separate read-only panel section displays the [local workflow](LOCAL_RUN.en.md)
requirements/risks, Git diff, saved pytest/Django results, exit codes, stdout/stderr,
truncation, blockers and review request. It does not attach bundles to the selected
Mock/Hermes task, mutate SQLite, execute commands/models or approve/publish anything.
Full #15/#16/#19 remain open.

The viewer is disabled by default. Configure `LOCAL_REVIEW_ROOT` in the server's
`.env` as an absolute dedicated evidence directory inside the approved workspace,
for example `/absolute/workspace/AICommander-v3/.aicommander-local-runs/passing-1/evidence`
after the documented demo. Restart the app normally. Existing HTTP Basic and
loopback/SSH tunnel restrictions apply. No directory creation or automatic discovery.
Windows accepts an absolute native path. Relative/missing directories and symbolic
links/reparse points are rejected. Do not point it at a home directory or source tree.

`GET /local-reviews` returns `{enabled, runs:[{run_id}]}` from direct lowercase
32-hex child directories. IDs are unverified until opened. Enumeration is bounded
to 100 total entries, including unrelated entries; overflow fails with 503, no
partial results or recursion. To combine runs, the owner may manually copy complete
five-file bundles byte-for-byte into a dedicated directory under their original IDs,
without overwriting conflicts. Committed passing/failing examples are not auto-loaded.

`GET /local-reviews/{run_id}` accepts an ID only. The existing bounded verifier
checks all five files, hashes, lengths, manifest/QA/diff bindings and state. Manifest
is at most 16 KiB; each artifact 8 MiB; total 12 MiB. Only regular files, no symlinks,
reparse points or hard links. The directory ID must match the manifest run ID.
The reader returns the exact bytes it verified in one read, decoded as UTF-8,
as `artifacts`, plus `qa_report`, manifest and manifest hash. This is not filesystem
race protection against hostile processes or an OS sandbox; the owner controls the
storage directory and permissions.

Successful detail always says `integrity_valid=true`, `authenticated=false`,
`current_source_verified=false`, `ready_for_pr=false`, `ready_for_merge=false`.
Integrity is internal consistency, not author authentication, command trust,
current source verification or review approval. Passing means passed in the saved
run. Independent review and owner approval remain required. A review request is
not a verdict. `source_current_at_export` is historical; the panel does not call
`session.is_current()`.

Unavailable/incomplete/tampered/non-UTF-8 bundles return 409 without unverified text
or filesystem paths. Invalid IDs/disabled detail return 404; unavailable storage/index
returns 503. Existing response redaction masks configured literals/patterns without
changing files. The manifest hash belongs to verified on-disk bytes, not the serialized
or masked HTTP response. No mutation routes exist.

The UI clears old results on selection/refresh/errors, discards late responses after
refresh, never substitutes fixtures on failure, and renders plain text without HTML
or Markdown execution. RU/EN and a 390px viewport are covered. The selected task's
approval controls do not operate on bundles.

API tests use byte-preserved published synthetic bundles. Chromium uses real local
HTTP and temporary SQLite to test passing/failing output, language/mobile, corrupted
and unavailable storage, late responses, disabled state and checksum-valid XSS text.
LOCAL_RUN regression also executes real pytest/Django fixture subprocesses. Run:

```bash
python -m pytest -q tests/test_review_api.py tests/test_localrun.py
RUN_UI_TESTS=1 python -m pytest -q tests/ui/test_review_panel.py
RUN_UI_TESTS=1 python -m pytest -q
```

Locally inspected PNGs show passing RU, failing EN/mobile and integrity failure.
They are synthetic Chromium evidence, not Zorin/Safari or owner device acceptance.
No new dependencies, database migrations, background jobs, SSH, deployment, live
models, runtime GitHub wiring or independent reviewer. Full Hermes → Codex → QA
→ reviewer → PR/CI integration remains subsequent work.
