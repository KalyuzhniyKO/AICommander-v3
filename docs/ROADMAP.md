# AICommander 3.0 — дорожная карта

[English](ROADMAP.en.md) · [Реализовано и осталось](IMPLEMENTATION.md) · [Исторический аудит](AUDIT_2026-10-15.md)

## Состояние `main` — 18.10.2026

- **Завершены #10–#13**, включая подтверждённые live-проверки OmniRoute и Hermes/streaming после [PR #41](https://github.com/KalyuzhniyKO/AICommander-v3/pull/41). Исторические записи «ещё не проверено» на страницах старого аудита относятся к 15 октября.
- **#14:** Hermes taskflow v2 планирует настоящими моделями и передаёт артефакты между ролями, но итоговая live-приёмка заблокирована квотами; **открыта**.
- **#15/#16:** [PR #52](https://github.com/KalyuzhniyKO/AICommander-v3/pull/52) объединяет *отдельные offline-библиотеки* worktree → pytest/Django QA → пакет ревью, без Codex CLI и без независимого Reviewer; обе **открыты**.
- **#17:** read-only проверка GitHub PR/CI, публикация draft PR ещё не реализована; **открыта**.
- **#18:** offline журнал бюджетов, не включённый в runtime; **открыта**.
- **#19:** [PR #54](https://github.com/KalyuzhniyKO/AICommander-v3/pull/54) добавил выравнивание кнопок, «светофор», диагностику и исправление RU/EN; [PR #56](https://github.com/KalyuzhniyKO/AICommander-v3/pull/56) — отдельный read-only просмотр QA-пакетов. Сквозной запуск AI-команды из панели **не готов**, #19 открыта.
- **#20:** прикладные проверки безопасности проведены, OS/network isolation ещё нет; **открыта**.
- **#21/#22:** пилот на тестовом складе и установка Zorin ещё не приняты; инструмент SQLite backup готов автономно, обе **открыты**.
- [PR #55](https://github.com/KalyuzhniyKO/AICommander-v3/pull/55) расширил CI до Python 3.14, включая macOS ARM64 и Chromium. Основная ветка — `main`, v2 — `v2-legacy`.

**Следующий интеграционный маршрут:** Hermes → безопасный исполнитель/Codex → QA → независимый Reviewer → draft PR/CI → решение владельца. Работа на production и автоматический merge не входят в пилот.

Таблица ниже сохраняет исходную очередность и **исторические** рекомендации моделей Codex; текущая доступность этих названий не подтверждена. Независимые offline-задачи можно выполнять параллельно, но реальный end-to-end требует проверенных зависимостей.

## План интеграционных этапов

| Шаг | Issue | Что сделать | Рекомендуемая модель Codex | Уровень | Почему |
|---|---|---|---|---|---|
| 00 | [#10](https://github.com/KalyuzhniyKO/AICommander-v3/issues/10) | Проверка основы и CI | GPT-6.1 Sol | **Medium** | Проверка кода, тестов и небольшой рефакторинг |
| 01 | [#11](https://github.com/KalyuzhniyKO/AICommander-v3/issues/11) | Проверка Hermes и OmniRouter (read-only) | GPT-6.1 Sol | **Medium** | Инвентаризация с точной фиксацией интерфейсов |
| 02 | [#12](https://github.com/KalyuzhniyKO/AICommander-v3/issues/12) | Анализ OSS и лицензий | GPT-6.1 Sol | **High** | Сравнение фреймворков и архитектурный ADR |
| 03 | [#13](https://github.com/KalyuzhniyKO/AICommander-v3/issues/13) | Адаптер OmniRouter | GPT-6.1 Sol | **High** | Контракт API, обработка ошибок, мок-тесты |
| 04 | [#14](https://github.com/KalyuzhniyKO/AICommander-v3/issues/14) | Директор и межролевой workflow | GPT-6 Astra | **High** | Сложная архитектура состояний и передач |
| 05 | [#15](https://github.com/KalyuzhniyKO/AICommander-v3/issues/15) | Изолированный Codex executor | GPT-6 Astra | **High** | Инструменты, песочница, права и выполнение кода |
| 06 | [#16](https://github.com/KalyuzhniyKO/AICommander-v3/issues/16) | QA и независимый Reviewer | GPT-6.1 Sol | **High** | Доказуемые тесты и анализ diff |
| 07 | [#17](https://github.com/KalyuzhniyKO/AICommander-v3/issues/17) | GitHub PR и CI | GPT-6.1 Sol | **Medium** | Практическая интеграция API и веток |
| 08 | [#18](https://github.com/KalyuzhniyKO/AICommander-v3/issues/18) | Free-first, лимиты и эскалация | GPT-6.1 Sol | **Medium** | Контроль бюджета, обработка rate limits |
| 09 | [#19](https://github.com/KalyuzhniyKO/AICommander-v3/issues/19) | Панель для MacBook | GPT-6.1 Sol | **Medium** | Web UI и защищённое управление |
| 10 | [#20](https://github.com/KalyuzhniyKO/AICommander-v3/issues/20) | Безопасность и восстановление | GPT-6 Astra | **High** | Security review, изоляция, отрицательные тесты |
| 11 | [#21](https://github.com/KalyuzhniyKO/AICommander-v3/issues/21) | Сквозной пилот warehouse_config | GPT-6 Astra | **High** | Интеграция нескольких систем, валидация |
| 12 | [#22](https://github.com/KalyuzhniyKO/AICommander-v3/issues/22) | Документация, развёртывание, бэкапы | GPT-6.1 Sol | **Medium** | Runbook и проверяемое восстановление |
| MIG | [#23](https://github.com/KalyuzhniyKO/AICommander-v3/issues/23) | Переименование репозитория — выполнено | — | — | `main` = v3, `v2-legacy` = резерв v2 |

## Очередность
Актуальный порядок приведён выше. Независимые этапы можно выполнять параллельно, но реальные интеграции — только после выполнения зависимостей и проверки. Создавать отдельный репозиторий v3 больше не нужно.

## Зависимости и критерии
- #10 проверяет фактический запуск и тесты foundation.
- #11 зафиксировал API/CLI Hermes/OmniRouter (закрыта).
- #12 выбирает OSS компоненты и указывает авторов/лицензии.
- #13 подключает OmniRouter только через проверенный контракт.
- #14 создаёт директора и структурированные передачи задач.
- #15 включает изменения реальных файлов в изолированном worktree.
- #16 доказывает качество реальными тестами и независимым ревью.
- #17 добавляет draft PR и CI, без auto-merge.
- #18 вводит бюджет и экономичную маршрутизацию.
- #19 делает панель для MacBook с авторизацией.
- #20 проверяет безопасность, секреты и восстановление.
- #21 проводит тестовый пилот на `warehouse_config` без production.
- #22 завершает документацию Zorin и восстановление.

**Модели — рекомендации, не гарантия доступности в интерфейсе Codex.** Если недоступна указанная модель, выбрать ближайшую доступную по назначению и уровню, указав замену в PR. Затраты на вызовы моделей AI-команды через OmniRouter — отдельный вопрос от выбора модели исполнителя **Codex для разработки проекта**.
