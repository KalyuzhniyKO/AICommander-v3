# Process cleanup after killpg denial

[Русский](PROCESS_CLEANUP.md)

macOS ARM64 CI hit PermissionError in killpg while testing an oversized Git diff. Cleanup exited before wait(), and a later test reported an unreaped Popen ResourceWarning.

A process-group OSError now records failed containment while still attempting to kill and reap the direct child and close its pipes. Cleanup then raises a sanitized ExecutionError. Unconfirmed tree cleanup never becomes a successful command result. Time/output limits and mandatory macOS CI remain enabled.

Regression tests use real short-lived and sleeping Python children with an injected killpg denial, requiring both reaping and a containment failure. The session execution environment disconnected before local validation. Acceptance requires CI, including test_process_cleanup.py, the existing oversized evidence test and resource tests without ResourceWarning. No live Zorin or models are involved.
