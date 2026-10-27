# Runtime spending control — #18 / panel #19

[Русский](RUNTIME_SPENDING.md)

The existing ledger now gates legacy rounds, role reruns, manual model checks and
Hermes StepOperations. Empty `SPEND_POLICY_FILE` denies model dispatch even when
keys and Hermes are configured. Mock, local classification and saved/status reads
remain deterministic. No new Director or model-routing framework.

Configure an absolute owner-controlled JSON policy without secrets. The
[template](contracts/runtime-spend-policy.example.json) is deliberately expired,
unpriced, unbound to an account and unverified; copying it enables nothing.
See the [schema](contracts/runtime-spend-policy.schema.json). The operator verifies
actual endpoint/model, role capability, account fingerprint, enforced provider caps
and every billing category. Model names and subscriptions do not prove zero cost.
No live credentials or working Zorin policy were read/generated for this change.

Evidence expires within 24 hours. Invalid/duplicate/nonfinite/non-UTF-8 JSON,
links/reparse/hard links and files over 128 KiB fail closed. The browser cannot
supply pricing or file paths. Existing Basic Auth, loopback and SSH tunnel apply.

Reserve the full maximum input/output bounds plus fixed fee in integer micro-USD,
rounding up, not an average prompt estimate. The verified input limit must include
SDK overhead; payload bytes are separately capped. Actual expected token counts
without a trustworthy tokenizer remain unknown. For paid OpenRouter, a separate
upstream tariff supplies wire price caps; the total tariff includes additional
fees. Provider fallback is disabled and text-only parameters are enforced.
OmniRoute needs verified gateway caps, including internal routes/accounts.
[OpenRouter contract](https://openrouter.ai/docs/guides/routing/provider-selection).

Response cost is interpreted only with a verified unit: USD, or credits with an
explicit conversion. Otherwise it stays null and the upper reservation remains.
Safe counters/decimal amounts and the applicable policy snapshot are retained;
raw prompts/bodies/argv/keys are not financial records. Provider usage accounting
does not prove a cash payment or subscription balance.
[Usage documentation](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

Server ceilings alone grant no spending. Every new global/project/task scope has
zero budget and `allow_paid=false`. Paid tasks need approval for the exact route
in all three scopes; fixed-prompt model checks use the global scope. Another key,
project or operation ID cannot reset the global limit. Legacy free-first only
reorders existing configured references by verified zero-price eligibility.
Hermes keeps its explicit configured route.

The owner selects scope/route, enters a total limit including prior commitments,
and explicitly confirms. Approval starts no model. Server ceilings, budget revision
and the displayed policy hash are checked; lost responses replay the same approval
ID/body. The actor comes from authenticated Basic Auth. Audit records retain
before/after, actor, time, route, scope and authorization window. Old commitments,
tokens, requests and unresolved holds are never reset by policy/time-window renewal.
Updated server metadata revokes old paid permissions. Verified free routes can
continue without a positive money allowance but still obey quota/time/unknown gates.

All scope reservations and StepOperation use one connection/commit via savepoints.
Claim happens durably once before transport; no HTTP/SDK call holds the write lock.
The saved model result and financial outcome commit together. Hermes checks actual
HTTP model/context/schema/payload/output bounds and rejects tools, multiple samples
and fallback lists. Existing one-child/one-inference containment remains. SDK time
is bounded; bounded JSON/SSE metering never turns absent usage into zero.

Timeout/unknown holds the maximum reservation and blocks new dispatch across
projects/keys. Restart revokes unclaimed permissions and marks dispatched work
unknown; no replay. Quota honors cooldown/Retry-After; overrun blocks subsequent
requests. Approval cannot clear unknown/overrun. Verified reconciliation is separate
work; there is no unsafe reset button.

`GET /spending/status` and `GET /spending/tasks/{id}?mode=mock|hermes&run_id=...`
show the selected route/reason, estimates, scopes, reservations, known cost, remaining
budget, counters, dates and bounded history. `POST /spending/approvals` is owner-only,
CSRF-protected, explicitly confirmed and idempotent. Unknown financial schema
versions are rejected before other app DDL. Backups include finance/unresolved data.

Actual local HTTP/SQLite/runtime/Chromium tests use synthetic upstream responses and
prices. Coverage includes default deny, concurrent last slot, transaction rollback,
restart, account changes, stale prices, fixed fees, quota/unknown/overrun, free-first,
unit handling, lost responses, RU/EN/mobile and XSS. This is not live invoice evidence.
Live gateway pricing/caps, Codex CLI/subscription dispatch, full code/QA/reviewer/PR
acceptance and OS isolation remain separate. Manual ChatGPT latest_available/High
is a separate supported workflow without session automation or hidden inference.
