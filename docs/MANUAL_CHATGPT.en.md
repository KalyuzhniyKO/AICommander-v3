# Manual ChatGPT: latest_available / High

[Русский](MANUAL_CHATGPT.md)

Every task/project supports a manual ChatGPT handoff. The server data file sets
`chatgpt_web_model_policy=latest_available` and prefers High. Model numbers are
not coded as a chosen default: public Chat documentation provides a candidate,
while the owner confirms their actual visible picker. Work/Codex/API catalogs
are not treated as Chat availability.

The public-source check uses credential-free HTTPS GETs only to official docs
paths, at most four hops, five seconds per request and 1 MiB HTML. No auth,
cookies, ambient proxies, browser sessions or model APIs. HTML is not executed.
A 24-hour cache avoids redundant requests; force refresh is explicit. Unknown
format/network failure/staleness yields Unconfirmed, retaining historical data
and the last successful check. A slower old response cannot replace a newer check.

The panel distinguishes public announcement, plan eligibility, source/check dates
and owner-reported account availability. An announcement does not establish rollout
or access in a particular account.
[Official Chat overview](https://help.openai.com/en/articles/20001354-gpt-6-and-other-models-in-chatgpt).
High is a preference, not a programmatic picker change or a prompt instruction.
Unavailable High requires the owner to choose a supported level. Extra High/Pro
follow published plan restrictions and require explicit manual choice; Plus is not
presented as eligible for them. The account and subscription are never read.

Three steps: open ChatGPT through a plain link and manually confirm the newest
visible model/effort; review and sanitize a task-context draft before preparing text
for manual copying; choose its export and paste the manual answer back. Redaction
is best effort, not guaranteed PII removal. The owner transfers the text. The answer
remains a plain-text external note, never QA/reviewer, budget, code or merge authority.
No scraping, cookies/private endpoints, automated switching or answer extraction.
The link has no prompt, model/effort or session parameters. Paid API default-deny
remains intact; subscription quota and session monetary cost remain unknown.

Task/owner-bound history retains model/effort/plan self-report, actor/date, source
hash/status, context/answer hashes and idempotent request IDs. New announcements do
not rewrite old tasks. No selection is silently restored as a current session;
export needs a recent manual confirmation (24 hours). Task changes clear drafts.
Context and answers are bounded to 16 KiB UTF-8. Existing Basic/Host/CSRF protection
applies. Public fetch occurs outside the SQLite write lock; schema/version and
backup handling preserve manual history. This is not private-account synchronization
or proof that a user actually ran a model.

Unit/API/Chromium tests cover new/unavailable announcements, effort restrictions,
cache/stale/network failure, confirmation, task binding, idempotency, denied private
redirect/auth forwarding, no API spending, RU/EN/mobile and XSS. Browser tests operate
our local panel only. A real public-document GET on 2026-10-27 retrieved 98,454 bytes
and parsed model/plan metadata; no owner picker, subscription or inference was accessed.
Future model labels in screenshots/tests are synthetic, not real release claims.
