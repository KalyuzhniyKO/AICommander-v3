# Hermes bridge compatibility and process restrictions

[Русский](HERMES_BRIDGE_HARDENING.md)

The bridge sets `HERMES_DISABLE_LAZY_INSTALLS=1` so the installed SDK does not
relaunch Python after consuming stdin. An audit hook denies shell, exec, fork
and arbitrary subprocess launches. SDK metadata queries allow only pinned
`git`/`uname` binaries resolved before SDK import, with fixed `git rev-parse`
and `uname -p/-m/-s` arguments. PATH/executable substitution and relative
executables are rejected. Model toolsets remain empty.

`HermesPreflightError` exposes allowlisted phase/error_type fields only when
the bridge explicitly confirms no dispatch. Uncertain dispatched operations
retain the existing no-retry semantics. Runtime spending reservations, request
payload/token caps, usage accounting and quota/Retry-After handling are retained.

These are restrictions on a trusted Python SDK, not an OS sandbox. Live Zorin
acceptance remains a separate #14 gate; no models or SSH are used here. The old
uncommitted live_acceptance CLI is excluded until adapted to the mandatory
runtime spending guard introduced by PR #62.

Regression tests cover metadata queries, subprocess/exec/Git mutation denial,
executable/PATH substitution, safe preflight diagnostics and existing spending
caps/usage. Fixtures do not establish compatibility with the live installed SDK.
