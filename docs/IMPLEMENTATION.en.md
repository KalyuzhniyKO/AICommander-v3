# AICommander 3.0 — current implementation (18 October 2026)

[Русская версия](IMPLEMENTATION.md) · [Roadmap](ROADMAP.en.md) · [15 October historical audit](AUDIT_2026-10-15.md)

This document describes `main` **after PR #56**, distinguishing tested building blocks
from the still-unimplemented end-to-end development pipeline. Dated earlier QA
reports remain valid for their original commits, not for every subsequent change.

## Verified components and boundaries

| Component | Present in code | Not yet provided |
|---|---|---|
| FastAPI / SQLite | Authenticated legacy tasks/rounds, projects, versioned taskflow, typed artifacts, migrations, idempotency and operation journal | Complete development automation |
| OmniRoute | Opt-in transport for legacy rounds and probes. Authorized inventory, inference, streaming and one delegated child route were live-tested ([evidence](qa-issue-13-live.json)) | Full route coverage or live budget enforcement |
| Hermes taskflow v2 | Installed Hermes runtime for real **planning** artifacts, typed handoffs, optional Architect, plan Reviewer, owner decisions and safe cancellation/unknown handling ([details](HERMES_TASKFLOW.en.md)) | Final full live acceptance #14 blocked by provider quota; no coding/QA/PR |
| Mock taskflow v1 | Deterministic Analyst → Architect → Developer, snapshots, pause/resume and owner approval | `done` is **mock plan approval**, not working code |
| Local executor / QA | Disposable trusted worktrees, allowlisted commands, actual pytest/Django checks, diff/hashes and a local review bundle ([PR #52](https://github.com/KalyuzhniyKO/AICommander-v3/pull/52); [runbook](LOCAL_RUN.en.md)) | Codex CLI, OS sandbox, Hermes/UI runtime wiring and independent review |
| MacBook owner panel | Authenticated mock and saved Hermes history, RU/EN, aligned controls, stage/status lights and diagnostics from saved data ([PR #54](https://github.com/KalyuzhniyKO/AICommander-v3/pull/54)) | UI cannot launch new Hermes v2 inference/approval; plan review is not code review |
| Saved QA bundle viewer | Optional **separate read-only** panel section for saved QA evidence and Git diffs with integrity checks ([PR #56](https://github.com/KalyuzhniyKO/AICommander-v3/pull/56); [docs](LOCAL_REVIEW_PANEL.en.md)) | No proof of provenance/current source, no taskflow binding or merge readiness |
| GitHub | Exact-head read-only PR/CI status library ([docs](GITHUB_STATUS.en.md)) | Issue/branch/draft PR publishing, automatic review/merge |
| Budget | Offline admission/reservations ledger ([docs](BUDGETS.en.md)) | Connected real provider calls/usage/cost |
| Recovery/security | SQLite backup and new-file restore, protected API and dependency audit ([backup](SQLITE_BACKUPS.en.md); [security](SECURITY_BOUNDARY.en.md)) | Encrypted backups, full OS/network isolation and deployed Zorin acceptance |
| CI | [PR #55](https://github.com/KalyuzhniyKO/AICommander-v3/pull/55): Python 3.11–3.14 Linux/Windows; Python 3.14 macOS ARM64 plus Chromium; ephemeral databases | Does not establish live Zorin/provider integration |

## Outstanding integration steps

- **#14:** repeat full Hermes v2 live acceptance after provider quota recovery.
- **#15/#16:** integrate Codex CLI, safe execution, taskflow QA and an **independent Reviewer**. A review-request file is not a review decision.
- **#17:** publish draft GitHub PR with checked evidence and fresh CI, require explicit owner decision, never auto-merge.
- **#18–#20:** enforce real budgets, complete UI integration and OS/network isolation.
- **#21/#22:** conduct end-to-end pilot on an **isolated test copy** of `warehouse_config`; validate Zorin installation/restore. AICommander itself is intended for arbitrary future projects, not only warehouse_config.

## Interpretation rules

- `done` for Mock/Hermes, and even `qa_passed` on a saved local bundle, **never authorize merging**.
- Passing Chromium/pytest on ephemeral fixtures does not verify provider quota, current deployed Zorin services, production data or physical Safari devices.
- The language selector changes interface labels only; user text and server artifacts remain as authored.
- Preserve historic audit artifacts as dated evidence; do not project their historical missing/present claims onto the current `main`.
