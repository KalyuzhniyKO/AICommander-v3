# Budget-aware Hermes acceptance preparation

[Русский](HERMES_BUDGET_ACCEPTANCE.md)

The explicit live_acceptance CLI checks synthetic three/four-role planning chains,
owner approval, credential-free replay in a separate process, and max_steps=1.
At most eight bridge calls; no retries or fallback. Code execution and QA remain false.
The prior uncommitted live-readiness harness was preserved.

RuntimeSpend shares the Taskflow SQLite connection, including after reopening it.
Global/project ceilings and committed costs survive between chains. Unknown cost
stays null; unknown operations stop the harness and retain financial reservations.

Required options: --allow-inference, --workspace (new absolute directory), and
--spend-policy (current verified owner policy). Credentials/config arrive through
stdin JSON, never argv. No working .env is loaded. Fixtures cannot establish live
pricing. Paid routes additionally require an explicitly authorized total
--approved-micro-usd ceiling, default zero; existing scope approval APIs are used.

Private workspace/DB permissions are 0700/0600. Evidence survives success/failure.
Do not create another DB to bypass unresolved unknown operations. The harness never
resumes or retries them. Temporary bridge profiles are still removed. The operator
must supply/revoke a separate restricted key; this CLI does not manage keys.

Read-only inventory on 4 November 2026: SSH works; Linux 7.0.0-42-generic,
system Python 3.12.3, no reboot-required marker. Clean Hermes checkout
bf0203a794a07aac3ff764972ab9087baa4e4f4e, selected PM Python 3.14.9.
OmniRoute package 3.8.51; user service loaded/active/running/enabled.
Codex absent from SSH PATH and ~/.local/bin, /usr/local/bin, /usr/bin checks;
other installations or subscription status are not established. No updates performed.

The owner has no additional spending budget. No inference was run. A current verified
free-route policy is unavailable, so #14 remains blocked and #15–#17/pilot were not
started. Offline synthetic tests do not prove current SDK/provider live acceptance.

Local validation: 569 backend passed (2 Windows-only skips), separately 37 Chromium passed; compileall, pip check and diff check exit 0. Initial run lacked Playwright and skipped 37 UI tests; the subsequent dedicated run executed all of them.
