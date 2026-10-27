# AICommander 3.0 — виртуальная команда ИИ-разработчиков

[**English version / Английская версия**](README.en.md)

**Статус:** подготовлена минимальная основа (foundation MVP). Полноценная интеграция Hermes, OmniRouter, Codex и QA **ещё не реализована**. Версия 2 сохранена в [v2-legacy](https://github.com/KalyuzhniyKO/AICommander-v3/tree/v2-legacy).

## Текущий статус — 15 октября 2026

- `main` репозитория AICommander-v3 содержит foundation, offline taskflow (PR #31) и отдельную библиотеку worktree executor (PR #33).
- В объединённом PR #30 панель подключена к taskflow: версии, роли, артефакты, согласование/отказ, отмена и продолжение. Все маршруты защищены авторизацией и CSRF. [Проверки и границы](docs/QA_PANEL.md).
- Адаптер OmniRoute подключается явно к ручным legacy-раундам и проверкам моделей; [live smoke](docs/OMNIROUTE_ADAPTER.md) подтвердил текстовый маршрут, streaming и делегирование установленного Hermes. Taskflow v2 использует установленный Hermes для реальных артефактов планирования; [контракт и live QA](docs/HERMES_TASKFLOW.md). V1 сохраняет Mock Director; [подготовка панели](docs/HERMES_PANEL.md) добавляет отдельный просмотр/отмену сохранённых Hermes v2-процессов. Код и QA ещё не выполняются. Итоговая live-приёмка #14 ожидает восстановления квот провайдеров.

## Что мы создаём

Ты ставишь задачу **с MacBook** и принимаешь результат. На домашнем **Zorin OS** уже установлены Hermes и OmniRouter. AICommander управляет прозрачным процессом разработки, **не дублируя** их собственные функции.

**Цепочка работы:** владелец → директор (Hermes) → аналитик → архитектор (если нужен) → программист (Codex для сложных работ) → автоматические тесты/QA → независимый ревьюер → GitHub Pull Request → твоё подтверждение.

Цель — настоящие изменения в Git-ветке и проверенные тесты, а не пять текстовых советов от разных моделей.

## Что уже перенесено из v2

- локальный FastAPI и минимальный веб-интерфейс;
- задачи, история раундов, результаты ролей в SQLite;
- базовые подсказки ролям и первоначальное распределение по ключевым словам;
- клиент совместимого с OpenRouter API, проверка моделей и резервные модели;
- базовые тесты без реальных API-ключей.

**Пока отсутствует:** сквозной исполнитель кода и QA, исполнение Codex CLI, работа с реальными файлами, настоящая песочница, создание PR внутри приложения, сквозная интеграция панели с executor и учёт бюджета. Проверки foundation подтверждены в [отчёте этапа 00](docs/IMPLEMENTATION.md), панели — в [отчёте #19](docs/QA_PANEL.md); это не проверка интеграции с Zorin.

**Не переносим из v2:** архивные мосты для macOS, Director/Judge/Final Auditor migration pack, старые планы, пустые обёртки ролей, ненужную интеграцию с отдельным OpenAI API Premium Review. ChatGPT в браузере используется **вручную** для сложных экспертных задач.

## Принципы работы

1. Используем уже настроенные Hermes и OmniRouter, не пишем их аналоги.
2. Бесплатные или дешёвые модели — обычные этапы; сложная реализация — Codex; ChatGPT Web — ручная экспертиза.
3. QA доказывает результат реальными тестами, а ревьюер проверяет Git diff.
4. Production, реальные базы склада и секреты агентам недоступны.
5. Каждая задача Codex — отдельная feature-ветка и PR; **независимые задачи можно выполнять параллельно**. Merge и деплой — только с одобрения владельца.

## Документация

| Документ | Русский | English |
|---|---|---|
| Техническое задание | [PRODUCT_SPEC](docs/PRODUCT_SPEC.md) | [Product spec](docs/PRODUCT_SPEC.en.md) |
| Реализовано и не реализовано | [IMPLEMENTATION](docs/IMPLEMENTATION.md) | [Implementation](docs/IMPLEMENTATION.en.md) |
| Очерёдность работы Codex | [ROADMAP](docs/ROADMAP.md) | [Roadmap](docs/ROADMAP.en.md) |
| Выбор модели и уровня | [MODEL_SELECTION](docs/MODEL_SELECTION.md) | [Model selection](docs/MODEL_SELECTION.en.md) |
| Использование Open Source | [OPEN_SOURCE_REVIEW](docs/OPEN_SOURCE_REVIEW.md) | [Open-source review](docs/OPEN_SOURCE_REVIEW.en.md) |
| Решение по оркестрации (принято после live QA) | [ADR-001](docs/ADR-001-ORCHESTRATION.md) | [ADR-001 (RU)](docs/ADR-001-ORCHESTRATION.md) |
| Инструкции Codex | [AGENTS.md](AGENTS.md) | [Codex instructions](AGENTS.en.md) |
| Английские версии GitHub Issues | Основные описания — в Issues | [Issues EN](docs/ISSUES_EN.md) |
| Шаблоны GitHub и порядок работы | [WORKFLOW](docs/GITHUB_WORKFLOW.md) | [Workflow](docs/GITHUB_WORKFLOW.en.md) |
| Адаптер OmniRoute: opt-in и live smoke | [RU](docs/OMNIROUTE_ADAPTER.md) | [EN](docs/OMNIROUTE_ADAPTER.en.md) |
| SQLite backup/restore (подготовка #22) | [RU](docs/SQLITE_BACKUPS.md) | [EN](docs/SQLITE_BACKUPS.en.md) |
| Лимиты и бюджет (offline-подготовка #18) | [RU](docs/BUDGETS.md) | [EN](docs/BUDGETS.en.md) |
| Панель: история Hermes (подготовка #19) | [RU](docs/HERMES_PANEL.md) | [EN](docs/HERMES_PANEL.en.md) |
| Hermes Director: реальное планирование | [RU](docs/HERMES_TASKFLOW.md) | [EN](docs/HERMES_TASKFLOW.en.md) |
| Журнал операций taskflow (offline-часть #14) | [RU](docs/OPERATIONS.md) | [EN](docs/OPERATIONS.en.md) |

## Локальный запуск базовой версии

Python **3.11–3.14**. Версия Python 3.14 проверяется в CI на Linux/Windows и на macOS ARM64 (включая Chromium). Для MacBook Air M4 с установленным Python 3.14 используйте `python3 -m venv .venv` — установка 3.13 отдельно не требуется. Команды для Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip>=26.2.1"
python -m pip install -r requirements-dev.txt
cp .env.example .env
cp config/models.example.json config/models.json
# Укажите PANEL_PASSWORD (минимум 32 символа) в .env.
python -m compileall backend
python -m pytest -q
python -m backend.app.main
```

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2.1"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
Copy-Item config/models.example.json config/models.json
.\.venv\Scripts\python.exe -m compileall backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m backend.app.main
```

Тесты используют временные БД и конфигурации, не читают локальный `.env` и блокируют реальные HTTP-вызовы моделей. GitHub Actions выполняет compileall и pytest на Python 3.11–3.14 в Linux и Windows; на macOS ARM64 с Python 3.14 дополнительно проверяются Chromium UI и offline backend. Совместимость проверяется с последним patch-релизом 3.14 в GitHub runner, а не каждой минорной или patch-версией локальной системы. `MAX_MODEL_RETRIES` задаёт общее число попыток для каждой модели, включая первую; после их исчерпания используется следующая модель. Проверки не требуют API-ключа.

Все API требуют HTTP Basic; изменяющие запросы также требуют `X-AICommander-Request: 1`. После авторизации: `GET /health`, `POST /tasks` с JSON `{"description":"Проверить документацию"}`, `GET /tasks/1`, `GET /models/status`. `POST /models/check` и создание раунда вызывают модели, если ключ настроен; примеры ID нужно заменить перед такими запросами.

Открыть: `http://127.0.0.1:8000`. В конфигурации моделей указаны **заглушки**, их нужно заменить действительными ID. Панель требует браузерную авторизацию и серверный PANEL_PASSWORD. Для MacBook используйте только [SSH tunnel](docs/MACBOOK_PANEL.md); публичное размещение и прямой доступ из LAN не поддерживаются.

## Где находится v3

Основная ветка AICommander-v3 — `main`; v2 сохранена в `v2-legacy`. PR #30 объединяет интерфейс с уже реализованными offline taskflow и отдельной библиотекой executor, не вызывая внешний executor из mock-процесса.

## Панель владельца (#30)

Защищённая панель подключена к `/taskflow/*`: процессы, этапы, типизированные артефакты, timeline, approve/reject/cancel и возобновление. V1 — Mock Director без вызовов моделей и исполнения кода. В режиме истории Hermes доступны чтение сохранённых v2-артефактов и отмена, без запуска моделей и согласования. Legacy-раунды доступны отдельно через прежний API и показываются как история. [Запуск](docs/MACBOOK_PANEL.md), [проверки](docs/QA_PANEL.md).

## Аудит репозитория

[Аудит после PR #30 и #37](docs/AUDIT_2026-10-15.md) фиксирует состояние кода, CI и ограниченные подтверждения Zorin. Последующее [live QA #13](docs/qa-issue-13-live.json) подтвердило inventory и ограниченные Hermes → OmniRoute маршруты; это не сквозная команда разработки.


## Offline-подготовка бюджета #18

[Библиотека допуска запросов](docs/BUDGETS.md) проверяет разрешённые роли/модели,
лимиты запросов/токенов/времени и объявленный денежный резерв в отдельной SQLite.
Неизвестная цена и платный маршрут без разрешения блокируются; unknown удерживает
резерв и блокирует дальнейшие разрешения account. [Синтетический пример](docs/contracts/budget-offline.example.json)
воспроизводится командой `python -m backend.app.budgets.demo` без сети.
**В runtime библиотека пока не подключена и реальные расходы приложения не ограничивает.**
Полная #18 остаётся открытой для проверенных тарифов/usage и интеграции с журналом
операций Hermes/OmniRoute; новые автоматические retries/fallback не добавлены.


## Offline-подготовка backup/restore #22

[Явный SQLite snapshot-инструмент](docs/SQLITE_BACKUPS.md) создаёт проверяемый
архив, включает WAL и восстанавливает данные только в новый файл. Сохраняются
артефакты v1/v2, операции и бюджетные резервы; неизвестные исходы не повторяются.
Снимки приватные, не зашифрованы и не должны попадать в Git. Полная установка
и эксплуатационная приёмка Zorin этим компонентом не завершены.


## Offline QA (#16)

[QA runner](docs/QA_RUNNER.md) запускает настоящий pytest и Django checks в
одноразовом доверенном worktree, сохраняет exit codes, отредактированные логи и
реальный Git diff. Ошибки и изменение кода блокируют QA; независимое review и
решение владельца остаются обязательными. В taskflow не подключён, OS sandbox
не заменяет; полная #16 остаётся открытой.


[Проверка прикладной защиты #20](docs/SECURITY_BOUNDARY.md) закрывает утечки в JSON keys,
новых текстах SQLite и legacy context, усиливает Host/duplicate headers и
защитные заголовки отказов. Исторические данные не переписываются; OS/network
isolation и полная #20 остаются открытыми.


[Аудит зависимостей](docs/DEPENDENCY_AUDIT.md): локальный установщик обновлён,
CI отдельно проверяет установленные Python-пакеты и сохраняет отчёт. Это не
аудит Zorin, всех платформ или полной поставки браузера.


[Read-only GitHub PR/CI (#17)](docs/GITHUB_STATUS.md) получает статус для
точного commit, обнаруживает неполные/устаревшие проверки и не даёт разрешения
на merge. Публикация draft PR и подключение к taskflow ещё не реализованы.


## Общий локальный прогон (#15/#16)

[Executor → QA → запрос ревью](docs/LOCAL_RUN.md) соединены в явную локальную
цепочку: одобренные текстовые edits в отдельном worktree, literal staging,
настоящие pytest/Django checks и единый пакет требований/diff/QA с hashes.
Одной командой можно запустить новый синтетический passing/failing demo.
Codex CLI/модели не вызываются, независимое review и решение владельца обязательны;
readiness False. В HTTP/UI/taskflow не подключено, полные #15/#16 не закрыты.

[Просмотр пакетов QA в панели](docs/LOCAL_REVIEW_PANEL.md): отдельный read-only раздел для сохранённых локальных отчётов и diff с проверкой целостности. Это не проверка актуальности исходников или независимое ревью.

[Контроль расходов в runtime](docs/RUNTIME_SPENDING.md): платный API запрещён по умолчанию, scoped бюджеты/резервы и подтверждения владельца подключены к текущим путям inference. Это не live-приёмка тарифов/caps.

[Ручной ChatGPT](docs/MANUAL_CHATGPT.md): latest_available / High, кеш публичных сведений и подтверждённый владельцем export/import; без автоматизации аккаунта и вызовов API моделей.
