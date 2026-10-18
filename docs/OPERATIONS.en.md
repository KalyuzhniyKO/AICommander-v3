# Taskflow durable operations (offline #14 preparation)

**#14 update, 16 October 2026:** [Hermes planning v2](HERMES_TASKFLOW.en.md)
uses the installed runtime and the same journal/lifecycle. Mock-only statements
below describe preserved v1 and its offline implementation. Real-result import
through resolve_unknown is rejected; only explicit failed-attempt resolution
is supported after confirming worker shutdown.

[Full Russian protocol](OPERATIONS.md)

**Scope of the protocol below: preserved mock taskflow v1.** Its steps use
MockDirector, `simulated=true` and `mock_artifacts_only` with no Hermes/
OmniRoute or shell calls. **Separate Hermes taskflow v2 already exists** using
the same durable-operation lifecycle; see [v2 contract](HERMES_TASKFLOW.en.md).

Step now reserves a durable operation in a short transaction, computes outside any
SQLite transaction, durably saves a typed/redacted result, and atomically commits
artifact/version/event/idempotency response with operation completion. A cached
pure Director is used only to apply the saved result, never to replay external work.
Input/result sizes are capped at 1 MiB; arbitrary exception text is not persisted.

A partial unique index permits one running/result_ready/unknown operation per run.
Unrelated writes and cancellation can proceed during computation. In-flight repeats
return 409 operation_pending; completed repeats return the saved response. A
result_ready replay finishes the DB commit without recalculation after SQL failure.
Cancellation/version fencing discards a late result and never adds its artifact.
Cancellation is not proof of no dispatch and does not undo a future provider charge.

An actual worker-process crash leaves running. No automatic retry, lease expiry,
PID guessing or exactly-once external-effect guarantee is provided. Only after
independently confirming worker termination may a trusted backend operator call
mark_unknown(id). This fences late workers. For **v1 only**, resolve_unknown(id, result=typed_data)
imports independently checked evidence; omitting result records a failed attempt
and consumes one step. Bad evidence is rejected. These recovery mutations have no
HTTP API and cannot be invoked by a model or grant owner approval.

Protected GET /taskflow/runs/{id}/operations exposes metadata only, not prompts,
results or worker/key hashes. Additive transactional migration 2 preserves v1 and
legacy data. Downgrade is unsafe while operations remain active because old code
cannot enforce the new guard; stop workers and resolve them first.

[QA evidence](qa-issue-14-offline.json): real SQLite concurrency, a killed Python
worker, duplicate requests, SQL rollback, cancellation/fencing, recovery, redaction,
metadata API and migration rollback. **18 October update:** actual Hermes transport, real typed *planning*
artifacts and bounded live evidence now exist in
[Hermes v2](HERMES_TASKFLOW.en.md). #14 remains open until final live
acceptance after quota recovery, including replay/max_steps. These original
offline fixtures alone do not prove Hermes v2 live behavior. This protocol
is not an OS sandbox or an independent code Reviewer.
