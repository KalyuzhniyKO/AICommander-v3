# Local owner-panel visual acceptance

[Full Russian notes and four PNGs](UI_PREVIEW.md)

> **Historical #28 captures:** these files predate [PR #54](https://github.com/KalyuzhniyKO/AICommander-v3/pull/54) (aligned actions, real-state indicators/diagnostics, corrected RU/EN selector) and [PR #56](https://github.com/KalyuzhniyKO/AICommander-v3/pull/56) (separate saved QA viewer). They are evidence of that earlier viewport test, **not screenshots of current `main`**. See the [panel runbook](MACBOOK_PANEL.md) and `tests/ui` for the present feature scope.

Real Chromium screenshots show RU/EN at 1440×1050 and 390×844 viewports. They are
full-page captures of a local uvicorn/temporary SQLite mock run, not generated
mockups. No Zorin, model, real Director or executor calls occurred. User-authored
Russian fixture text remains unchanged when UI language switches to English.

All four captures were visually inspected. Automated checks verify no horizontal
overflow or clipped action buttons, appropriate approve/step availability,
preserved artifacts/version and zero mutations during locale/viewport changes.
Other browser cases cover XSS, cancellation, rejection/resume and request replay.

Reproduce: `RUN_UI_TESTS=1 PANEL_VISUAL_DIR=docs/ui-preview python -m pytest tests/ui -q`.
Without PANEL_VISUAL_DIR the CI runs behavior checks without writing screenshots.
[QA and image hashes](qa-issue-28-visual.json). This is Chromium viewport emulation,
not physical owner-device/Safari acceptance. #28 and parent #19 remain open for
those remaining criteria. No fabricated diff/QA examples or backend/frontend/API
changes were added; only the test and verification artifacts.
