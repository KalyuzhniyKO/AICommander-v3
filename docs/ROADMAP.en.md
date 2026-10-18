# AICommander 3.0 — dependency-aware roadmap

[Русская версия](ROADMAP.md) · [Current implementation](IMPLEMENTATION.en.md) · [Historical audit](AUDIT_2026-10-15.md)

## Current `main` status — 18 October 2026

- **#10–#13 closed:** authorized OmniRoute/Hermes live transport/inventory checks were completed after [PR #41](https://github.com/KalyuzhniyKO/AICommander-v3/pull/41).
- **#14 open:** Hermes taskflow v2 provides real planning and role handoffs. Final complete live acceptance still blocked by provider quota.
- **#15/#16 open:** [PR #52](https://github.com/KalyuzhniyKO/AICommander-v3/pull/52) merged a *standalone offline* trusted worktree → real pytest/Django checks → review-bundle path. No Codex CLI/taskflow wiring or independent Reviewer.
- **#17 open:** read-only GitHub exact-head CI status; no draft PR publication.
- **#18 open:** offline budget ledger, not enforced in runtime.
- **#19 open:** [PR #54](https://github.com/KalyuzhniyKO/AICommander-v3/pull/54) merged aligned controls, saved-state traffic lights and diagnostics plus RU/EN fix; [PR #56](https://github.com/KalyuzhniyKO/AICommander-v3/pull/56) merged an optional read-only saved QA/Git diff viewer, separate from taskflow. No end-to-end command from the panel.
- **#20 open:** app-layer security work complete in part; OS/network sandbox pending.
- **#21/#22 open:** isolated warehouse pilot, Zorin deployment and restore acceptance pending; a standalone SQLite backup module exists.
- [PR #55](https://github.com/KalyuzhniyKO/AICommander-v3/pull/55) extended CI to Python 3.14, including macOS ARM64 + Chromium. Default branch is `main`; v2 is preserved in `v2-legacy`.

**Next integration path:** Hermes → safe executor/Codex → QA → independent Reviewer → GitHub draft PR/CI → explicit owner decision. Do not claim production changes or automatic merge.

The stage table below preserves the **historical** task/model recommendations; specific old model names and access are not guaranteed. Independent offline work can be parallelized; real integration must honor dependencies.

## Historical parent-task plan

| Stage | GitHub issue | Scope | Suggested Codex model | Reasoning effort |
|---|---|---|---|---|
| 00 | [#10](https://github.com/KalyuzhniyKO/AICommander-v3/issues/10) | Проверка основы и CI | GPT-6.1 Sol | Medium |
| 01 | [#11](https://github.com/KalyuzhniyKO/AICommander-v3/issues/11) | Проверка Hermes и OmniRouter (read-only) | GPT-6.1 Sol | Medium |
| 02 | [#12](https://github.com/KalyuzhniyKO/AICommander-v3/issues/12) | Анализ OSS и лицензий | GPT-6.1 Sol | High |
| 03 | [#13](https://github.com/KalyuzhniyKO/AICommander-v3/issues/13) | Адаптер OmniRouter | GPT-6.1 Sol | High |
| 04 | [#14](https://github.com/KalyuzhniyKO/AICommander-v3/issues/14) | Директор и межролевой workflow | GPT-6 Astra | High |
| 05 | [#15](https://github.com/KalyuzhniyKO/AICommander-v3/issues/15) | Изолированный Codex executor | GPT-6 Astra | High |
| 06 | [#16](https://github.com/KalyuzhniyKO/AICommander-v3/issues/16) | QA и независимый Reviewer | GPT-6.1 Sol | High |
| 07 | [#17](https://github.com/KalyuzhniyKO/AICommander-v3/issues/17) | GitHub PR и CI | GPT-6.1 Sol | Medium |
| 08 | [#18](https://github.com/KalyuzhniyKO/AICommander-v3/issues/18) | Free-first, лимиты и эскалация | GPT-6.1 Sol | Medium |
| 09 | [#19](https://github.com/KalyuzhniyKO/AICommander-v3/issues/19) | Панель для MacBook | GPT-6.1 Sol | Medium |
| 10 | [#20](https://github.com/KalyuzhniyKO/AICommander-v3/issues/20) | Безопасность и восстановление | GPT-6 Astra | High |
| 11 | [#21](https://github.com/KalyuzhniyKO/AICommander-v3/issues/21) | Сквозной пилот warehouse_config | GPT-6 Astra | High |
| 12 | [#22](https://github.com/KalyuzhniyKO/AICommander-v3/issues/22) | Документация, развёртывание, бэкапы | GPT-6.1 Sol | Medium |
| MIG | [#23](https://github.com/KalyuzhniyKO/AICommander-v3/issues/23) | Repository rename — completed | — | — |

Independent tasks may run in parallel, but real integration stages must respect dependency checks and human merge/deploy approval. The repository rename (#23) is complete; there is no remaining standalone-repo migration.

## Authorized live follow-up (#13)

[PR #41](https://github.com/KalyuzhniyKO/AICommander-v3/pull/41) supersedes earlier
inventory deferral: bounded authorized inventory/text inference, Hermes streaming
and one delegated child route are verified. The app's manual legacy round used
OmniRoute with temporary SQLite. [Evidence](qa-issue-13-live.json). The conservative
no-new-framework ADR is accepted. PR #41 has merged and #11–#13 are closed; #14 still owns real Director/external effects in taskflow. Reboot
recovery, arbitrary tools and all provider/fallback routes are not claimed.


Offline preparation for #16 (2026-10-17): [QA runner](QA_RUNNER.en.md) executes
real pytest/Django checks against trusted disposable fixtures and binds reports
to code/diff evidence. Independent Reviewer, OS sandbox and runtime integration
remain pending; full #16 stays open.
