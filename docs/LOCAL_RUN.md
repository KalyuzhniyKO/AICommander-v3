# Общий локальный прогон executor → QA → запрос ревью

[English](LOCAL_RUN.en.md)

Подготовка #15/#16 без Zorin: `LocalQaSession` соединяет уже существующие
`OfflineExecutor` и `QaRunner`. На доверенном одноразовом проекте применяется
заранее одобренный текстовый план, выполняются настоящие проверки и сохраняется
единый пакет. Это фиксированная последовательность без AI-планирования,
выбора ролей/моделей или нового Director/router.

**Изменения берутся из статического плана владельца. Codex CLI, Hermes и
независимый Reviewer этим компонентом не запускаются.** HTTP, панель, SQLite
приложения, taskflow v1/v2, бюджет и GitHub не подключены и не изменены.
Полные #15/#16 и сквозная приёмка остаются открытыми.

## Воспроизводимый пример

Нужны Git и Python 3.11–3.13 с pytest/Django. Интерпретатор должен удовлетворять
[ограничениям executor](QA_RUNNER.md): абсолютный путь, без symlink/reparse,
на Windows — разрешённый прямой native interpreter без Store broker.

Для **новой**, ранее не существующей `.venv-local-run` на Linux/macOS:

```bash
python3 -m venv --copies .venv-local-run
.venv-local-run/bin/python -m pip install --upgrade "pip>=26.2.1"
.venv-local-run/bin/python -m pip install -r requirements-dev.txt
mkdir -p .aicommander-local-runs
.venv-local-run/bin/python -m backend.app.localrun.demo --root "$PWD/.aicommander-local-runs/passing-1" --python "$PWD/.venv-local-run/bin/python" --scenario passing
.venv-local-run/bin/python -m backend.app.localrun.demo --root "$PWD/.aicommander-local-runs/failing-1" --python "$PWD/.venv-local-run/bin/python" --scenario failing
```

Существующее Python-окружение не пересоздавать поверх старого; можно использовать
уже подготовленный совместимый interpreter. `--copies` нужен для POSIX venv,
поскольку executor отказывается от symlink executables. Для нового Windows venv:

```powershell
py -3.13 -m venv --copies .venv-local-run
.\.venv-local-run\Scripts\python.exe -m pip install --upgrade "pip>=26.2.1"
.\.venv-local-run\Scripts\python.exe -m pip install -r requirements-dev.txt
New-Item -ItemType Directory -Force .aicommander-local-runs
.\.venv-local-run\Scripts\python.exe -m backend.app.localrun.demo --root (Join-Path $PWD ".aicommander-local-runs\passing-1") --python (Join-Path $PWD ".venv-local-run\Scripts\python.exe") --scenario passing
```

Каждый demo root **должен отсутствовать**, его parent уже существует. Demo
создаёт fixture repository с вымышленной Git identity, делает initial baseline
commit и затем отдельный worktree. Исправление функции `receive(5, 3)` должно
дать 8; failing-сценарий намеренно даёт 9. В staged diff попадает также новый
файл. Изменения в worktree не коммитятся, source/main после bootstrap остаётся
неизменным. Никаких рабочих данных склада или настоящего warehouse_config нет.
Django checks выполняются с пустыми fixture apps и SQLite в памяти: это
проверка передачи/запуска команд, не приёмка Django-склада.

Код возврата demo/run: 0 — `awaiting_review`, 1 — завершённый отрицательный
результат, 2 — ошибка входных данных/исполнения/экспорта. Даже 0 не разрешает PR.
Повтор demo с тем же root отклоняется до записи. После ошибки частичные данные
сохраняются; для нового прогона выбрать другое имя, не удалять старое автоматически.

## Работа с одобренным fixture-проектом

[Пример плана](contracts/local-task-v1.example.json) содержит только четыре поля:
`task_id`, `requirements`, `risks`, `edits` (path/content). Unknown fields запрещены.
Сначала владелец проверяет source, settings, тесты, dependencies и план. Source —
одноразовый primary checkout с локальной `.git` directory, не worktree проекта
владельца, не production. Repository, pool и evidence не должны пересекаться.

```python
from pathlib import Path
from backend.app.execution import OfflineExecutor
from backend.app.qa import python_commands
from backend.app.localrun import LocalQaSession, LocalTask, TextEdit

executor = OfflineExecutor(
    allowed_root=Path('/explicit/fixture-root'),
    repository=Path('/explicit/fixture-root/source'),
    pool=Path('/explicit/fixture-root/worktrees'),
    commands=python_commands('/approved/direct/python', django_settings='qa_settings'),
)
session = LocalQaSession(executor, django=True,
                         evidence_directory=Path('/explicit/fixture-root/evidence'))
result = session.run(LocalTask(
    'fixture-task', 'receive(5, 3) возвращает 8.', 'Только синтетический fixture.',
    (TextEdit('inventory.py', 'def receive(stock, quantity):\n    return stock + quantity\n'),),
))
# Путь к пакету: result.bundle; данные остаются доступны для проверки.
# session.is_current(result) сверяет пакет, текущие файлы и план команд.
```

CLI для уже подготовленного source и существующей отдельной evidence directory:

```bash
python -m backend.app.localrun run --root /explicit/fixture-root --repository /explicit/fixture-root/source --pool /explicit/fixture-root/worktrees --evidence /explicit/fixture-root/evidence --plan /explicit/fixture-root/plan.json --python /approved/direct/python --django-settings qa_settings
```

`--django-settings` опускается только для явно выбранного Python-only fixture.
Команды не задаются текстом модели/HTTP/shell argument. План: 1–32 текстовых edits,
до 1 MiB content суммарно, requirements/risks до 16 KiB каждый. Пути проходят
общую portable policy executor; case/Unicode/separator aliases отклоняются как
дубли. Нет delete/rename/binary edits. Staging выполняется только для одобренных
файлов с Git literal pathspecs: wildcard не захватывает другие файлы. Ignored
новые файлы не добавляются принудительно. Нет add-all, auto-commit или публикации.

## Пакет и состояния

В новом приватном `evidence/<run_id>/` создаются ровно пять файлов:

| Файл | Содержимое |
|---|---|
| `requirements.md` | Отредактированные требования и ограничения владельца |
| `changes.diff` | Реальный отредактированный Git diff, включая staged новые файлы |
| `qa-report.json` | Неизменённый отчёт QaRunner: exit codes/stdout/stderr/evidence hashes |
| `review-request.md` | Запрос проверки требований, security и покрытия; вердикта нет |
| `manifest.json` | Идентичности task/workspace/QA run, состояния и hashes всех файлов |

Manifest записывается последним, после повторной проверки исходных данных и
плана команд. Перед возвратом результата пакет повторно проверяется на целостность.
Тексты/логи/diff используют существующий best-effort redactor.
`edit_plan_sha256` хеширует одобренные edits, исходное содержимое плана целиком
не экспортируется. Пути к interpreter/source и raw argv в пакет не включаются.
`run_id` локального процесса, `workspace_id` и `qa_run_id` различаются намеренно.

| Состояние | Условие |
|---|---|
| `awaiting_review` | QA пройден, source/план актуальны, есть реальные изменения |
| `qa_failed` | QA завершён отрицательно при актуальном source |
| `stale` | Код/план изменились или evidence невозможно подтвердить |
| `cancelled` | Запрошена отмена; успешно завершённые до неё проверки остаются записаны |
| `no_changes` | Тесты успешны, но нет diff относительно baseline |

Отмена до setup не создаёт worktree; при отмене после начала сохраняются
диагностические данные. Обычная ошибка тестов не становится review success.
Если ошибка произошла до QA/завершения экспорта, остаётся worktree и, возможно,
неполная папка без валидного manifest; CLI возвращает 2. Не повторять/возобновлять
такой процесс автоматически. Cleanup только явно через исходный executor session
после инспекции, либо вручную владельцем по проверенным Git metadata.

На POSIX bundle directory — 0700, files — 0600; Windows наследует ACL выделенного
каталога. Нет overwrite и автоматического удаления. Это не транзакционный архив:
при остановке/сбое питания часть данных может отсутствовать, manifest/file checks
должны это обнаружить. Не добавлять заметки/посторонние файлы внутрь пакета;
аннотации и review verdict хранить отдельно. Пакеты приватные, не публикуются.

## Целостность, актуальность и review

```bash
python -m backend.app.localrun verify --root /explicit/fixture-root/evidence --bundle /explicit/fixture-root/evidence/RUN_ID
```

`verify_bundle()` проверяет только явно выбранный каталог внутри заданного root,
без рекурсивного поиска. Проверяются fixed members, regular file/no link policy,
размеры (8 MiB/file, 12 MiB total, manifest 16 KiB), SHA-256, собственный hash QA,
identity bindings и equality QA diff/changes.diff. Unknown manifest fields/versions и неизвестные QA versions,
duplicate JSON keys, лишние/недостающие файлы и inconsistent ready flags запрещены.

**Это целостность, не подлинность или разрешение.** Внешний автор может пересчитать
hashes; verifier намеренно возвращает `authenticated=False`,
`current_source_verified=False`, `ready_for_pr=False`, `ready_for_merge=False`.
Код возврата verify 0 означает только корректный пакет, даже если QA failed.

`session.is_current(result)` принимает только результат своей живой session,
проверяет pinned manifest, текущий source/evidence и approved command-plan hash.
True возможен и у актуального qa_failed: это свежесть данных, не успешный QA.
При отличии файлов/статуса/плана от сохранённого evidence, подмене пакета или
удалении worktree положительного подтверждения нет. Возврат точно прежних байтов
может восстановить совпадение hashes; история всех промежуточных правок не ведётся.
Busy/unavailable также не дают положительного подтверждения. После рестарта
live binding отсутствует; внешнему JSON нельзя присвоить доверенную идентичность.

Во всех состояниях `independent_review=required`, `owner_approval=required`,
`developer_agent_executed=False`, readiness flags False. Требуется отдельный
доверенный Reviewer по точному diff/report/source/command hashes, затем владелец.
`review-request.md` не является `review.md` с заключением. Состояния/флаги Mock/
Hermes taskflow не продвигаются и остаются прежними.

## Ограничения и доказательства

Наследуются [ограничения QA](QA_RUNNER.md): worktree — не OS/network sandbox,
settings/conftest/код должны быть заранее проверены, production credentials/БД
не допускаются. Игнорируемые outputs не входят в evidence; hostile races или
изменение/возврат файлов между snapshots не исключены. Редактор не DLP, может
маскировать полезный текст и не обнаружить неизвестные/закодированные секреты.
Нет automatic retry/resume/journal/recovery или exactly-once внешних эффектов.

[Успешный пакет](local-run-examples/passing/manifest.json) и
[падающий пакет](local-run-examples/failing/manifest.json) получены реальными
процессами на синтетических проектах, не Zorin/моделями. Для файлов примеров зафиксирован LF в .gitattributes; checkout с core.autocrlf=true
проверяется настоящим Git. Fixture worktrees после
тестов удалены явно; примеры не подтверждают актуальность live source.
[QA summary](qa-local-run.json) отличает локальные/CI проверки от полной приёмки.

Перегенерация обоих пакетов (UUID, commit hashes и durations изменятся):

```bash
LOCAL_RUN_EXAMPLES=docs/local-run-examples python -m pytest -q tests/test_localrun.py::test_real_passing_chain_and_stale_source_plan_bundle_detection tests/test_localrun.py::test_real_failing_chain_retains_exit_code_diff_and_review_block
```
