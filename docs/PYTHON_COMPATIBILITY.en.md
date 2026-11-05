# Python and macOS compatibility policy — AICommander 3.0

[Русская версия](PYTHON_COMPATIBILITY.md) · [Product specification](PRODUCT_SPEC.en.md) · [GitHub Issue #58](https://github.com/KalyuzhniyKO/AICommander-v3/issues/58)

## Mandatory principles

1. **Current Python versions.** A claimed supported minor version must pass real CI dependency installation, `compileall`, `pip check`, and the applicable test suite. New stable releases receive an independent compatibility evaluation; neither support nor incompatibility may be asserted without evidence.
2. **Cross-platform testing.** The backend is tested on Ubuntu and Windows, plus mandatory **macOS ARM64** backend and real Chromium/Playwright UI jobs. An Apple Silicon runner tests the architecture, **not** the exact MacBook Air M4 hardware or Safari/WebKit.
3. **Fix causes rather than hiding failures.** Repair project code and add regression tests; investigate and update third-party dependencies as appropriate. Do not camouflage regressions with `continue-on-error`, unjustified skips, platform omissions, or arbitrary Python downgrades.
4. **Dependency bounds require evidence.** Pins/locks for reproducibility, security constraints, upstream-supported ranges, and demonstrable incompatibilities are acceptable. A temporary upper bound requires a package/version scope, repro or upstream reference, cause, workaround or remediation, responsible owner, and review date. A pin is not a substitute for compatibility work.
5. **Safe CI.** Actions must not access live model credentials, production databases, home servers, or the owner's real tasks. Browser tests use temporary databases and mocked providers.

## Verified configuration as of 2026-10-18

| Environment | CI coverage |
|---|---|
| Ubuntu / Python 3.11, 3.12, 3.13, 3.14 | Required `pip check`, `compileall`, and `pytest` |
| Windows / Python 3.11, 3.12, 3.13, 3.14 | The same required checks |
| macOS 15 ARM64 / Python 3.14 | `pytest`, `pip check`, `compileall`, and real Chromium/Playwright UI tests |
| Ubuntu / Python 3.13 | Separate Chromium UI tests and dependency audit |
| Python 3.15 | **Not yet tested in CI; support is not claimed** |

Source of configuration: [`foundation.yml`](../.github/workflows/foundation.yml); these jobs succeeded in [PR #56 CI](https://github.com/KalyuzhniyKO/AICommander-v3/actions/runs/39107082409). Subsequent changes require new evidence.

## New Python versions vs. optional Django QA fixtures

- [Python 3.15.0](https://www.python.org/downloads/release/python-3150/) was released on **2026-10-09**. [Issue #58](https://github.com/KalyuzhniyKO/AICommander-v3/issues/58) tracks first assessing core runtime installation separately from `requirements-dev.txt`, an offline API import/startup check, then dev/pytest/browser coverage.
- `requirements-dev.txt` specifies `Django>=5.2.8,<5.3` **for Django QA test fixtures only**, not application runtime. [Django 5.2](https://docs.djangoproject.com/en/5.2/releases/5.2/) supports Python 3.14 as of 5.2.8; the currently published Django compatibility documentation does **not** claim Python 3.15 support. This is a test dependency risk, not evidence that the FastAPI runtime is incompatible.
- Independently verify and justify `Django<5.3`, or replace it with a version-aware fixture test strategy. Do not remove an upper bound blindly, but also do not use it indefinitely to avoid evaluating new interpreters.
- On every new stable Python minor release: open a compatibility issue; exercise a separate clean core environment; classify project bugs versus upstream blockers; fix or document concrete temporary blockers; expand mandatory CI and acceptance criteria when demonstrated. Experimental probing may be clearly marked separately; a probe is not proof of complete support.

## Definition of done

- Every declared Python/OS combination passes current required CI, with real job names, commit SHA, logs and failures available in PR.
- ARM64 macOS backend and Chromium tests remain mandatory, not simulated on Linux.
- New Python evaluation has reproducible test/dependency evidence.
- No concealed failures, arbitrary version caps, unexplained platform skips or downgrades; any temporary exception has proof and a remediation plan.
- Separate branch/PR; merge/deploy only after explicit owner authorization.
