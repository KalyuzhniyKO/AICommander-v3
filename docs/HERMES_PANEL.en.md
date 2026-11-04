# Panel preparation #19: stored Hermes history

**Update 2026-10-27:** [budget-guarded owner planning controls](HERMES_OWNER_CONTROLS.en.md) extend this original read-only preparation. They do not execute code or QA.

[Русский](HERMES_PANEL.md) · [Issue #19](https://github.com/KalyuzhniyKO/AICommander-v3/issues/19)

The existing panel now has **History mode → Hermes · stored planning**. Mock is the
default. Each mode reads its own contract for the selected task; histories never
mix, even when a task contains both types. Failed v2 reads do not fall back to mock.

The Hermes view shows saved runs, roles, events, typed artifacts, planning review,
provenance (model/revision/operation/input hash) and operation metadata. Running,
result_ready, unknown, succeeded, failed and discarded states are distinct.
Operation-read failures are shown as errors, not empty history.

The only v2 mutation offered by this preparation is **cancel**. New runs, steps,
resume and approval are unavailable; the common UI mutation handler rejects them.
This is a frontend limit, not a change to server API permissions. Existing auth,
Host/CSRF, version and idempotency checks remain. Project/task edits do not call
models. Backend schemas/routes are unchanged.

A lost cancellation response can replay exactly the same key/body. Mode/run/task
switching is locked until it resolves. HTTP409 rereads state for a fresh owner
decision. Unknown operations never automatically retry. Cancellation does not
prove a refund, worker termination or resolution of unknown; a trusted operator
uses the existing [recovery protocol](HERMES_TASKFLOW.en.md).

Refresh is explicit. There is no polling, inventory or inference call. Locale and
viewport changes do not mutate data or translate user-provided content. A v2 done
run means approved **planning artifacts**, not code execution, verified tests,
a Git diff or a cost report. Provider availability/quotas are not checked.
The existing mock workflow remains separate and functional.

[QA](qa-issue-19-panel.json) uses real uvicorn/SQLite/Chromium with a **fixture
Director**, not live Hermes. Tests cover mixed task histories, four roles,
provenance/XSS, operation states, cancellation/replay, failed GETs, prohibited model
commands, RU/EN and mobile layouts. Existing mock browser tests also run.

Screenshots are **synthetic test data** and viewport emulation, not live Zorin,
Safari or actual-device acceptance:

| Language | Desktop 1440×1050 | Mobile 390×844 |
|---|---|---|
| RU | [PNG](ui-preview/hermes-fixtures/hermes-ru-desktop.png) | [PNG](ui-preview/hermes-fixtures/hermes-ru-mobile.png) |
| EN | [PNG](ui-preview/hermes-fixtures/hermes-en-desktop.png) | [PNG](ui-preview/hermes-fixtures/hermes-en-mobile.png) |

Full #19 stays open for Hermes/Codex/QA/GitHub integration and owner-device tests.
This preparation does not close #14/#15/#16/#18/#28. No SSH, Zorin, model, production
DB or persistent deployment was used.

[Unknown-state fixture screenshot](ui-preview/hermes-fixtures/hermes-unknown-en-desktop.png).
