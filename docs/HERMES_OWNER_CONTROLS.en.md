# Hermes owner controls — #19

[Русский](HERMES_OWNER_CONTROLS.md)

The panel now manages existing Hermes v2 **planning** after runtime spending gates:
new run, optional Architect/step limit, start/resume, an explicit model step,
pause/cancel and saved-plan approval/rejection. No second Director or code executor.

Capabilities must declare `owner_controls_version=1` and
`runtime_budget_guarded=true`; history/operation reads must succeed. Older or
unavailable metadata never enables new actions. New model steps require configured
Hermes, not an assumed live quota. Offline saved-plan decisions and cancellation
remain available without SDK execution. Unknown capabilities preserve cancellation
of a known run. Pending/unknown operations or failed operation reads block dispatch.

Creation/start/resume/pause are lifecycle changes, not model calls. Next step is
explicit and passes the backend global/project/task budget gate before transport.
Default settings deny dispatch with a visible reason. Architect is explicitly
selected for a new plan and adds a step. Approving a plan establishes planning
acceptance only, not code/QA/independent review, spending authority or merge/deploy.
Cancellation is final and does not refund unknown or spent costs.

Existing Basic/CSRF/version/idempotency controls remain. A lost model-step response
replays the exact request/key and cannot cause another inference or reservation.
Conflicts/unknown outcomes refresh metadata and stop further model actions. Mock v1
is preserved.

Five new Chromium tests exercise actual local HTTP/SQLite/budget code with synthetic
SDK RPC and a verified-zero fixture policy: planning/Architect/pause/resume, offline
saved-plan approval, default deny/no RPC, lost response/one inference, missing
capabilities/cancel and unknown-operation barriers. Code/QA flags stay false.
This is not live Zorin, device or code-review acceptance. A public documentation
check does not establish gateway pricing/caps. No working spending policy or live
models were enabled. Full code/QA/reviewer/PR integration remains dependent work.
