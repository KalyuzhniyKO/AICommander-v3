# Примеры пакетов локального прогона

Реальные pytest/Django процессы на синтетических временных Git-проектах.
Это не Zorin, Codex CLI, Hermes или заключение независимого Reviewer.

- `passing/`: pytest/Django exit 0, `awaiting_review`, readiness False.
- `failing/`: настоящий pytest exit 1, `qa_failed`, readiness False.

В каждом каталоге четыре artifact files и manifest с проверяемыми hashes.
Fixture source/worktrees уже удалены тестами: verifier подтверждает только
целостность примера. UUID/commit hashes/durations меняются при перегенерации.
[Запуск и границы](../LOCAL_RUN.md).

Для byte hashes этого поддерева .gitattributes отключает EOL conversion:
исходные LF/CRLF bytes сохраняются, включая checkout с core.autocrlf=true.
Это отдельно проверяется настоящим Git.
