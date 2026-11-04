# Preparation #18: request admission and reservation accounting

**Update 2026-10-27:** the ledger now also gates actual application runtime paths; see [runtime spending](RUNTIME_SPENDING.en.md). The offline scope below documents the original standalone implementation.

[Русский](BUDGETS.md) · [Issue #18](https://github.com/KalyuzhniyKO/AICommander-v3/issues/18)

`backend.app.budgets` is an offline library, with no model calls, routing loop,
SDK imports or service changes. **It is not wired into current legacy/v2 endpoints
or panel costs.** Full #18 stays open. There are no automatic application migrations,
environment flags or background tasks.

A trusted server-owned `Policy` specifies exact permitted role/model pairs,
request/input/output/total token limits, per-call timeout, account lifetime,
integer micro-USD budget and explicit paid permission (default false/budget zero).
Unknown pricing denies admission, even for an ID containing `/free`. Zero declared
rates do not prove actual free service. All example prices are synthetic; real
rates need separate verification. Input/output cost components round UP separately.
Float/bool/NaN/Infinity/negative/numeric-string values are rejected.
[Policy schema](contracts/budget-policy-v1.schema.json).

`BudgetLedger(path)` has no constructor/import side effect. Explicit `initialize()`
installs its own version-1 schema into a dedicated temporary/protected SQLite file;
existing taskflow migrations are unchanged. Connections close after each action.
Only short SQL/arithmetic transactions execute under the write lock.

1. `create_account` persists an immutable policy/lifetime. Identical replay resets
   nothing; a changed policy conflicts.
2. `reserve` atomically holds capacity after checking permissions, tokens, time,
   money, cooldown and count. Repeated operation IDs never authorize another send.
3. `claim` persists a one-time permission BEFORE external I/O; concurrent/restarted
   workers cannot obtain a second claim. The ledger does not send HTTP; future
   transport must enforce the physical call count.
4. `record` stores completed/quota/rejected/unknown, with idempotent identical
   outcomes and conflicting different outcomes. Completion here is accounting,
   not proof of a valid task artifact, code or QA.

Only unclaimed reservations can be released. Dispatched/unknown/crashed attempts
retain capacity, with no takeover, automatic retry or refund claim. Unknown fences new/queued permissions for that account and cannot
be replaced by a cheap success; verified reconciliation needs a separate operator
protocol. Both reserve/claim recheck deadline/cooldown/overrun. Backward server
clock movement fails closed. Admission deadlines do not replace actual transport
wall-clock/process enforcement.

429 or structured rate_limit/quota_exhausted sets account-scoped exponential
cooldown. A longer verified Retry-After is honored even above the backoff cap.
Late concurrent success cannot clear an active cooldown. There is no scheduler,
sleep, automatic model fallback or paid escalation. A later manual attempt needs
a new operation ID and remaining capacity. Provider-wide/API-key quotas remain
OmniRoute's responsibility; new accounts must not be used to evade them. HTTP200
alone is not proof of success; future adapters must inspect structured SDK errors.
Uncertain 5xx/timeouts must not be treated as confirmed free rejections.

Reports distinguish committed reservations, reserved/reported tokens, one-time
permissions, reported billing subtotal and complete reported cost (null when any
claim lacks billing). Missing cost is never inferred as zero from a tariff.
Trusted adapter usage/cost is not an independently verified invoice. Overruns are
recorded and fence further reservations/queued claims. Fees/cache/reasoning pricing
and hard provider spend caps require live integration.

No prompts, credentials or raw provider errors are accepted as ledger metadata.
Identifiers must be public metadata, never credentials; this is not a universal
secret detector. Static decision codes explain unknown pricing, paid approval, route permissions,
input/output/time/count/token/money limits, expiration, cooldown, replay and overrun.
Storage errors do not echo paths/SQL/provider exceptions.

```bash
python -m backend.app.budgets.demo
python -m pytest -q tests/test_budgets.py
```

The example uses a temporary file, synthetic time and 429/unknown outcomes, with
zero network calls. Its [published output](contracts/budget-offline.example.json)
is compared by tests. [QA](qa-issue-18-offline.json) covers concurrent reserve/claim,
reopen/replay, numeric boundaries, unknown billing, overruns, quota and schema rollback.

Remaining: verified live pricing/usage and error mapping for the existing Hermes
bridge, atomic coordination with StepOperations (two independent commits are not
atomic), provider caps, actual OmniRoute role/model configuration, billing
reconciliation and panel reporting. Full free-first routing, Codex integration
and manual ChatGPT handoff are not completed by this offline preparation.
