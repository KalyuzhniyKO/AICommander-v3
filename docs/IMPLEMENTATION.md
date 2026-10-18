# AICommander 3.0 — реализовано и не реализовано (18.10.2026)

[English](IMPLEMENTATION.en.md) · [Дорожная карта](ROADMAP.md) · [Исторический аудит 15 октября](AUDIT_2026-10-15.md)

Эта страница описывает **состояние `main` после PR #56**, а не целевую
архитектуру. Более ранние отчёты, в том числе аудит от 15 октября, сохраняются
как датированные свидетельства и не используются для описания сегодняшней
полноты функциональности.

## Фактическое состояние

| Компонент | Реализовано в коде | Граница реализации |
|---|---|---|
| FastAPI / SQLite | Legacy tasks/rounds; проекты, версии процессов, типизированные артефакты, миграции, idempotency/operation journal | Не весь workflow разработки |
| OmniRoute | Opt-in адаптер для ручных legacy-раундов и проверок моделей; авторизованный inventory/inference/streaming и один дочерний маршрут проверены live ([#13](qa-issue-13-live.json)) | Это не автоматический контроль всех маршрутов/цен |
| Hermes | [Taskflow v2](HERMES_TASKFLOW.md) подключён к установленному Hermes: реальные роли/артефакты **планирования**, optional Architect, Reviewer **плана**, owner decisions, операции/cancel/unknown | Последняя полная live-проверка #14 остановлена квотами; код, QA и PR не производятся |
| Mock taskflow v1 | Детерминированные Analyst → Architect → Developer, timeline, блокировка/возобновление и owner decision | `done` означает одобрение mock-плана, не выполненный код |
| Локальный executor + QA | [Одноразовые Git worktrees](EXECUTION.md), allowlist/timeout; [настоящие pytest и Django checks](QA_RUNNER.md), diff и hashes; [локальный прогон](LOCAL_RUN.md) worktree → QA → пакет для ревью ([PR #52](https://github.com/KalyuzhniyKO/AICommander-v3/pull/52)) | Только доверенные тестовые проекты, не Codex CLI и не OS sandbox; нет вызова из Hermes/UI |
| Панель MacBook | Авторизация; Mock и отдельная сохранённая история Hermes; RU/EN; выровненные группы кнопок; светофор из snapshot/operations; диагностика ([PR #54](https://github.com/KalyuzhniyKO/AICommander-v3/pull/54)) | В UI Hermes v2 — чтение/отмена, не новый запуск/approve; светофор Reviewer отражает ревью плана, не кода |
| Просмотр локальных QA-пакетов | [Отдельный read-only раздел](LOCAL_REVIEW_PANEL.md) ([PR #56](https://github.com/KalyuzhniyKO/AICommander-v3/pull/56)): сохранённые тесты, stdout, diff и проверка целостности | Включается явно; не связан с выбранной taskflow-задачей; ни подлинность, ни актуальность кода, ни readiness не подтверждаются |
| GitHub | [Read-only PR/CI reader](GITHUB_STATUS.md) для конкретного SHA | Автосоздания issue/branch/draft PR, reviewer-approval и merge нет |
| Бюджет | [Offline policy/ledger](BUDGETS.md) с резервами и блокировками | Не подключён к реальным запросам Hermes/OmniRoute, не ограничивает текущие расходы |
| Backup / безопасность | [Проверяемое SQLite backup/restore](SQLITE_BACKUPS.md), [защита API](SECURITY_BOUNDARY.md), [аудит зависимостей](DEPENDENCY_AUDIT.md) | Snapshot не зашифрован; нет полной OS/network isolation и проверки production-деплоя |
| CI | [PR #55](https://github.com/KalyuzhniyKO/AICommander-v3/pull/55): Python 3.11–3.14 Linux/Windows, Python 3.14 macOS ARM64 + Chromium; проверки браузера на временной SQLite | CI подтверждает тестируемый checkout, не живую работу модели/Zorin и не применение изменений |

## Что остаётся для работающей AI-команды

1. [#14](https://github.com/KalyuzhniyKO/AICommander-v3/issues/14): завершить live-приёмку Hermes v2 после восстановления квот, включая replay и ограничение шагов.
2. [#15–#16](https://github.com/KalyuzhniyKO/AICommander-v3/issues/15): подключить безопасный Codex CLI/исполнитель, настоящий taskflow QA и **независимый** Reviewer. Существующий локальный пакет — вход для review, не сам review.
3. [#17](https://github.com/KalyuzhniyKO/AICommander-v3/issues/17): создание draft PR, проверка CI и принятие решения владельцем (без автоматического merge).
4. [#18–#20](https://github.com/KalyuzhniyKO/AICommander-v3/issues/18): связать расходы с runtime и завершить защищённую панель и изоляцию.
5. [#21–#22](https://github.com/KalyuzhniyKO/AICommander-v3/issues/21): провести сквозной пилот на **отдельной тестовой копии** `warehouse_config`, затем проверить установку/восстановление на Zorin.

## Проверенные ограничения и правила

- `warehouse_config` — **тестовый пилот**, а не единственный проект, который должна обслуживать AICommander. Production-БД и рабочие секреты не используются.
- В `main` нет полноценного автономного цикла изменения кода по задаче владельца. Не принимать результат `done` в Mock/Hermes или `qa_passed` в сохранённом пакете за разрешение на merge.
- GitHub API/CI, свежие исходники, независимое ревью и согласие владельца должны проверяться отдельно.
- Изменения UI не выполняют inference; выбранная локализация переводит интерфейс, не тексты пользователя.
- [Старый foundation-аудит](AUDIT_2026-10-15.md) и [QA PR #30](QA_PANEL.md) остаются полезными *историческими* отчётами, а не текущей сводкой возможностей.
