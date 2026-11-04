# Совместимость Hermes bridge и ограничения процессов

[English](HERMES_BRIDGE_HARDENING.en.md)

При запуске установленного Hermes bridge отключает lazy installs через
`HERMES_DISABLE_LAZY_INSTALLS=1`. SDK не должен перезапускать Python после чтения
stdin: иначе теряется входная конфигурация. Audit hook запрещает shell, exec,
fork и произвольные subprocess. Для служебных запросов SDK разрешены только
конкретные бинарники `git`/`uname`, найденные до импорта SDK, с фиксированными
аргументами `git rev-parse` и `uname -p/-m/-s`. Подмена PATH, иной бинарник и
относительный executable отклоняются. Toolsets моделей остаются пустыми.

`HermesPreflightError` передаёт только allowlist phase/error_type при явно
подтверждённом отсутствии dispatch. Неизвестный исход после dispatch сохраняет
прежний запрет автоматического повтора. Runtime-бюджет, caps payload/tokens,
учёт usage и обработка quota/Retry-After сохраняются.

Это ограничения доверенного Python SDK, не OS sandbox. Live-приёмка актуальной
установки Zorin остаётся отдельным этапом #14; здесь модели и SSH не запускаются.
Старая незакоммиченная утилита live_acceptance не включена: сначала требуется
адаптировать её к обязательному runtime-контролю расходов из PR #62.

Регрессия проверяет допустимые metadata-команды, запрет subprocess/exec и Git
mutation, подмену executable/PATH, безопасные preflight diagnostics и прежние
spending caps/usage. Реальный установленный SDK эти fixtures не заменяют.
