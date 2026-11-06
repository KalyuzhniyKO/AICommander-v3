# GitHub PR/CI — read-only часть #17

[English](GITHUB_STATUS.en.md)

18.10.2026: добавлен `PublicGitHubStatus`, явная библиотека чтения публичного
репозитория GitHub. Нет подключения к HTTP/UI/taskflow, создания Issue/branch/
commit/PR, публикации комментариев, approval, merge или автоматического polling.
Полная #17 остаётся открытой; независимый Reviewer и #15/#16 ещё нужны.

## Использование

```python
from backend.app.github_status import PublicGitHubStatus

reader = PublicGitHubStatus(
    'KalyuzhniyKO/AICommander-v3',
    expected_checks=(
        'Python 3.11 / ubuntu-latest', 'Python 3.11 / windows-latest',
        'Python 3.12 / ubuntu-latest', 'Python 3.12 / windows-latest',
        'Python 3.13 / ubuntu-latest', 'Python 3.13 / windows-latest',
        'Python 3.14 / ubuntu-latest', 'Python 3.14 / windows-latest',
        'Chromium owner panel', 'Python 3.14 / macOS 15 ARM64 + Chromium',
        'Installed Python dependency audit',
    ),
    expected_app_id=15368,  # GitHub Actions, checked against this repo's API data
)
snapshot = reader.snapshot(50)
```

Пример обновлён после [PR #55](https://github.com/KalyuzhniyKO/AICommander-v3/pull/55) под текущую матрицу CI. Проверять имена jobs и App ID непосредственно перед использованием: изменения workflow делают прежний список недостаточным.

Repository, точные expected check names и trusted App ID задаёт владелец из
проверенного workflow. Эти значения нельзя брать из текста модели/ответа API.
Список не заменяет GitHub required checks/rulesets. URL/число PR валидируются;
не допускаются произвольные endpoint/base URL, path traversal или query injection.

Для одного snapshot выполняются максимум четыре GET к фиксированному
`https://api.github.com/repos/<owner>/<repo>/…`:

1. Metadata PR: проверяются number, base repository и head SHA.
2. Check runs для **этого SHA**, `filter=all`, первая страница до 100.
3. Combined legacy commit statuses для того же SHA, до 100.
4. Metadata PR повторно: head, state, draft, merged и base ref должны остаться
   прежними. Изменение даёт `stale`, а не успешную проверку.

Ссылки на PR/diff конструируются из доверенного repository и проверенного number.
URLs/redirects/annotations/body из ответа не используются для новых запросов.
Публичное чтение работает без токена: GH_TOKEN/GITHUB_TOKEN, `.env`, git config,
keychain, netrc/cookies не загружаются. Ambient HTTP proxies отключены, redirect
не выполняется. Приватные репозитории/enterprise/auth здесь не поддерживаются.
Ограничения: до 1 MiB на ответ, timeout 10 s на запрос (owner может задать 0–30 s,
исключая 0), без retry, фоновых вызовов или обхода rate limit. Это socket timeout,
не жёсткий общий wall-clock deadline; slow responses/OS stalls отдельно не изолированы.

Контракты REST: [PR](https://docs.github.com/en/rest/pulls/pulls),
[Check runs](https://docs.github.com/en/rest/checks/runs),
[Commit statuses](https://docs.github.com/en/rest/commits/statuses).
Используется документированная API version `2026-03-10`; новых dependencies и
копирования upstream-кода нет.

## Значение результата

`all_expected_checks_passed=True` означает только: получены полные страницы,
найдены все expected names у trusted App ID, все возвращённые checks этого App
завершены success, нет checks неизвестного App и legacy statuses не содержат
pending/failure/error. Это conservative snapshot: старый failed rerun в `filter=all`
тоже блокирует green, даже если новый rerun уже успешен. Skipped/neutral не
считаются success; неизвестные статусы/типы, неверный SHA/payload, duplicate JSON
properties, oversized response, HTTP/network ошибки дают unavailable/не green.

При наличии следующей страницы/неполного total_count `complete=False`; библиотека
не следует Link URL и не притворяется, что первая страница является полным CI.
Отсутствие обязательного check не равно успеху. Пустой legacy status list допустим
только при выполненных expected check runs, пустой весь CI — нет.

**`ready_for_merge` всегда False.** Draft/merged/closed PR может иметь успешные
CI checks: это не разрешение публиковать/сливать. Не проверены branch protection,
rulesets, approvals, разрешения владельца, актуальность локального кода, merge
conflicts или независимость review. Между GET и следующим действием GitHub может
измениться; будущая публикация должна заново связать точный SHA, доверенные QA/
review artifacts и owner approval. Этот reader не является security controller.
Названия checks/statuses — недоверенные строки; UI должен показывать их как текст.

## Проверки и границы

49 offline-тестов с injected HTTP fixtures: exact SHA/GET-only/no auth, pending/
failed/skipped/neutral, foreign App, missing jobs, pagination, head/base/draft race,
legacy failure, HTTP 301/302/401/403/404/429/500, body limit, duplicate/invalid JSON,
неверные типы/configuration, network failure и отсутствие retry/raw error echo.
Реальная сеть в pytest не используется.

Отдельная live-проба выполняется только вручную на публичном PR этого репозитория,
без credentials, Zorin и моделей. [QA evidence](qa-issue-17-status.json) отличает
fixture tests от public GitHub observations. Это не сквозной Issue → worktree →
Coder → QA → Reviewer → draft PR; эта часть требует зависимостей полного этапа.
