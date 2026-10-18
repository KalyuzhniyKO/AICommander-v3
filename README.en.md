# AICommander 3.0 — Virtual AI Development Team

[**Русский / Russian version**](README.md)

**Status:** verified foundation and standalone workflow components exist, but the end-to-end Hermes → code executor → QA → independent Reviewer → draft PR pipeline is **not wired together**. Hermes v2 produces genuine *planning* artifacts; the offline worktree/QA workflow runs separately. The original v2 is preserved on [v2-legacy](https://github.com/KalyuzhniyKO/AICommander-v3/tree/v2-legacy).

## Verified status — 18 October 2026

- `main` contains the v3 implementation, with v2 preserved in `v2-legacy`. Issues #11–#13 closed after authorized OmniRoute/Hermes live verification. Hermes taskflow v2 passes typed planning artifacts; final live #14 acceptance still awaits provider quota recovery.
- [PR #52](https://github.com/KalyuzhniyKO/AICommander-v3/pull/52) merged: offline worktree → real pytest/Django checks → checksummed review bundle, **not** Codex CLI or HTTP/taskflow integration.
- [PR #54](https://github.com/KalyuzhniyKO/AICommander-v3/pull/54) merged: grouped and aligned process/owner controls, snapshot-derived stage indicators and diagnostics, corrected RU/EN language selector. A Hermes plan review is not code QA.
- [PR #55](https://github.com/KalyuzhniyKO/AICommander-v3/pull/55) merged: Python 3.11–3.14 on Linux/Windows CI and Python 3.14 on macOS ARM64 with Chromium.
- [PR #56](https://github.com/KalyuzhniyKO/AICommander-v3/pull/56) merged: separate optional read-only viewer for saved QA bundles/Git diffs; integrity checks do **not** attest authenticity, present source state, or readiness to merge.
- Still missing: Codex CLI wiring, a full OS sandbox, independent Reviewer, GitHub draft PR publishing, runtime budget enforcement, owner-accepted warehouse pilot and Zorin deployment. See [current implementation](docs/IMPLEMENTATION.en.md).

## Vision
The human owner enters tasks from a MacBook and approves the final changes. Existing Hermes and OmniRouter run on Zorin OS. AICommander should coordinate a team without re-implementing their existing responsibilities:

**Owner → Director (Hermes) → Analyst → Architect (if needed) → Developer (Codex for complex work) → real QA tests → independent Reviewer → GitHub draft PR → owner approval.**

The goal is real, test-backed software changes, not several independent textual responses.

## Retained from v2
Local FastAPI, minimal web UI, tasks and rounds, SQLite history, simple role prompts and keyword role routing, an OpenRouter-compatible transport with fallback and model availability checks, and credential-free test scaffolding.

Hermes taskflow v2 provides [live-tested planning artifacts](docs/HERMES_TASKFLOW.en.md), using the installed runtime. V1 retains mock runs; [panel preparation](docs/HERMES_PANEL.en.md) adds separate saved v2 history and cancellation, without model launch or approval. Final #14 live acceptance awaits provider quota recovery.

**Remaining integrations:** taskflow-triggered code execution/QA, independent Reviewer, Codex CLI, OS sandbox, GitHub draft PR generation and runtime budget policy. The authenticated mock-taskflow panel and independent offline worktree library are implemented. [Verified scope and tests](docs/QA_PANEL.md).

**Deliberately excluded:** legacy macOS integration bridges, old Director/Judge/Final Auditor migration pack, obsolete roadmaps and role wrappers, and the optional OpenAI API Premium Review. ChatGPT web is an optional **manual** expert handoff; ChatGPT subscription access does not imply API entitlements.

## Development principles
1. Reuse deployed Hermes and OmniRouter; no redundant directors or model routers.
2. Free/cheap models for routine roles; Codex for complex coding; ChatGPT web only by manual escalation.
3. QA executes actual commands and records exit status; reviewer examines actual Git diff.
4. Keep production warehouse databases, production secrets and files outside the approved workspace inaccessible.
5. Each Codex task gets its own feature branch and PR; **independent tasks may run in parallel**. No auto-merge or deployment.

## Documentation

| Topic | English | Russian |
|---|---|---|
| Product specification | [Spec](docs/PRODUCT_SPEC.en.md) | [ТЗ](docs/PRODUCT_SPEC.md) |
| Implementation status | [Status](docs/IMPLEMENTATION.en.md) | [Реализовано](docs/IMPLEMENTATION.md) |
| Codex stages | [Roadmap](docs/ROADMAP.en.md) | [Этапы](docs/ROADMAP.md) |
| Model and reasoning effort | [Selection](docs/MODEL_SELECTION.en.md) | [Выбор моделей](docs/MODEL_SELECTION.md) |
| Open-source reuse | [Review](docs/OPEN_SOURCE_REVIEW.en.md) | [Источники](docs/OPEN_SOURCE_REVIEW.md) |
| Codex rules | [AGENTS](AGENTS.en.md) | [Правила](AGENTS.md) |
| GitHub Issues | [English descriptions](docs/ISSUES_EN.md) | [Russian-first issues](https://github.com/KalyuzhniyKO/AICommander-v3/issues/24) |
| GitHub templates and workflow | [Workflow](docs/GITHUB_WORKFLOW.en.md) | [Процесс](docs/GITHUB_WORKFLOW.md) |
| OmniRoute opt-in transport and live smoke | [EN](docs/OMNIROUTE_ADAPTER.en.md) | [RU](docs/OMNIROUTE_ADAPTER.md) |
| Taskflow operation protocol (offline #14) | [EN](docs/OPERATIONS.en.md) | [RU](docs/OPERATIONS.md) |
| Local worktree → QA → review bundle | [EN](docs/LOCAL_RUN.en.md) | [RU](docs/LOCAL_RUN.md) |
| Read-only QA/Git diff viewer | [EN](docs/LOCAL_REVIEW_PANEL.en.md) | [RU](docs/LOCAL_REVIEW_PANEL.md) |

## Local test bootstrap
Python **3.11–3.14**; Linux/macOS (3.14 also covered by macOS ARM64 CI):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip>=26.2.1"
python -m pip install -r requirements-dev.txt
cp .env.example .env
cp config/models.example.json config/models.json
# Set PANEL_PASSWORD (at least 32 characters) in .env.
python -m compileall backend
python -m pytest -q
python -m backend.app.main
```
Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2.1"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
Copy-Item config/models.example.json config/models.json
.\.venv\Scripts\python.exe -m compileall backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m backend.app.main
```

Tests use temporary databases/configuration, ignore the local `.env`, and block live model HTTP calls. GitHub Actions runs compilation and tests on Python 3.11–3.14 on Linux and Windows, Chromium on Linux, and Python 3.14 with Chromium on macOS ARM64. `MAX_MODEL_RETRIES` is the total attempts per model, including the first; exhausted attempts advance to the next model. Tests require no API key.

Smoke endpoints: `GET /health`, `POST /tasks` with JSON `{"description":"Check documentation"}`, `GET /tasks/1`, `GET /models/status`. `POST /models/check` and round creation call models when a key is configured; replace placeholder IDs before using them.

Open `http://127.0.0.1:8000` locally. Model IDs are placeholders. Every route requires HTTP Basic; mutations also require `X-AICommander-Request: 1`. Set server-side `PANEL_PASSWORD` (at least 32 characters) before starting. Use [SSH tunnel access](docs/MACBOOK_PANEL.md); direct public/LAN hosting is unsupported.

## Repository status
The repository is AICommander-v3, with v3 on `main` and original v2 on `v2-legacy`. `v3-foundation` is historical; all new PRs target `main`. Repository rename #23 is complete.

[Stage #10](https://github.com/KalyuzhniyKO/AICommander-v3/issues/10) is complete. See the [parallel roadmap](docs/ROADMAP.en.md) and [Issue #24](https://github.com/KalyuzhniyKO/AICommander-v3/issues/24).

## Owner panel / taskflow (PR #30, #54, #56)

The target repository is AICommander-v3, branch main; v2 is preserved in v2-legacy. Merged PR #30 integrates the protected owner panel with the existing offline taskflow API: run history, typed artifacts, role stages, versioned/idempotent commands and owner decisions. Access requires server-side HTTP Basic credentials and an SSH tunnel to loopback. Approval accepts mock artifacts only; the separate executor, real model calls, QA and deployments are not invoked by this workflow. Stage indicators and process diagnostics derive from saved states, while a separate opt-in read-only panel displays local QA bundles unrelated to the selected task. [Russian runbook](docs/MACBOOK_PANEL.md), [QA](docs/QA_PANEL.md).

## Repository audit

[Audit updated after PR #30 and #37 (RU)](docs/AUDIT_2026-10-15.md) records code, CI and limited Zorin evidence. The main branch is v3; v2-legacy preserves v2. Subsequent [live QA #13](docs/qa-issue-13-live.json) verifies bounded inventory/inference routes, not a full development team.


## Offline budget preparation #18

[Admission/accounting library](docs/BUDGETS.en.md) validates role/model permissions,
request/token/time limits and declared micro-USD reservations in a separate SQLite
ledger. Unknown pricing and unapproved paid requests are denied. Unknown outcomes
retain capacity and fence subsequent account permissions. Run the [synthetic example](docs/contracts/budget-offline.example.json)
with `python -m backend.app.budgets.demo`, without network access.
**The library is not wired into runtime and does not enforce current app spending.**
Full #18 stays open for verified pricing/usage, atomic operation-journal integration
and live provider caps. No automatic inference retry/fallback was added.


## Offline backup/restore preparation #22

[Explicit SQLite snapshots](docs/SQLITE_BACKUPS.en.md) include committed WAL data,
verify integrity/checksums and restore into a new filename only. V1/v2 artifacts,
operations and budget reservations remain unchanged; unknown outcomes never retry.
Snapshots are private, unencrypted data and must stay out of Git. Full Zorin
installation/operational acceptance is not completed by this component.


## Offline QA (#16)

[QA runner](docs/QA_RUNNER.en.md) executes real pytest/Django checks in a trusted
disposable worktree and records exit codes, redacted logs and actual Git diff.
Failures or source changes block QA. Independent review and owner approval remain
required. No taskflow wiring or OS sandbox is claimed; full #16 remains open.


[Application security checks for #20](docs/SECURITY_BOUNDARY.en.md) cover JSON keys, new SQLite
text, legacy context, strict Host/duplicate headers and denial response headers.
Historical data is not rewritten; full #20 and OS/network isolation remain open.


[Dependency audit](docs/DEPENDENCY_AUDIT.en.md): installer updated locally;
a dedicated CI job checks installed Python packages and retains evidence.
This does not audit Zorin, every platform, or bundled browser components.


[Read-only GitHub PR/CI (#17)](docs/GITHUB_STATUS.en.md) reads exact-head
metadata and detects incomplete/stale checks. It never grants merge permission;
draft PR publishing and taskflow wiring remain pending.


## Local fixture workflow (#15/#16)

[Executor → QA → review request](docs/LOCAL_RUN.en.md) now has an explicit local
path: approved text edits in an owned worktree, literal staging, real pytest/Django
checks and one checksummed requirements/diff/QA bundle. A one-command synthetic
passing/failing demo is available. No Codex CLI/models or runtime HTTP/UI/taskflow
wiring; independent review/owner approval remain required and readiness False.

[Saved QA bundle viewer](docs/LOCAL_REVIEW_PANEL.en.md): optional read-only panel section for local fixture reports/diff, with integrity checks. Not current-source verification or independent review.
