# Локальные снимки панели: RU/EN и mobile/desktop

[English](UI_PREVIEW.en.md) · [#28](https://github.com/KalyuzhniyKO/AICommander-v3/issues/28)

> **Исторические скриншоты #28:** эти PNG сняты **до** объединения [PR #54](https://github.com/KalyuzhniyKO/AICommander-v3/pull/54) (новые группы кнопок, «светофор», диагностика и исправленный языковой селектор) и [PR #56](https://github.com/KalyuzhniyKO/AICommander-v3/pull/56) (отдельный просмотр QA-пакетов). Они свидетельствуют о старом viewport-тесте, **не** показывают текущий интерфейс. Для текущих UI-функций см. [инструкцию](MACBOOK_PANEL.md) и `tests/ui`.

В настоящем Chromium создана отдельная тестовая задача и три сохранённых
mock-артефакта до awaiting_owner. Снимки взяты с работающего локального uvicorn и
временной SQLite, а не нарисованы вручную. Настоящие Zorin/Hermes/OmniRoute,
кодовый executor и модели не вызывались. Содержимое задачи искусственное.

| Режим | Viewport | Снимок |
|---|---|---|
| Русский desktop | 1440 × 1050 | [PNG](ui-preview/panel-ru-desktop.png) |
| English desktop | 1440 × 1050 | [PNG](ui-preview/panel-en-desktop.png) |
| Русский mobile | 390 × 844 | [PNG](ui-preview/panel-ru-mobile.png) |
| English mobile | 390 × 844 | [PNG](ui-preview/panel-en-mobile.png) |

PNG — full-page: высота файла больше viewport. Пользовательский текст задачи
остаётся русским и при English UI; переключатель переводит интерфейс, не данные.

Четыре снимка просмотрены визуально. Проверены перенос кнопок, компоновка ролей,
читаемость пометок Mock Director и отсутствие горизонтального скролла/выхода
кнопок за экран. Автотест также проверяет доступность approve и недоступность
step после трёх артефактов, неизменность версии/содержимого и отсутствие POST
при переключении языка/viewport. Остальные Chromium-тесты проверяют XSS,
отмену, отказ/продолжение, stale version и потерю ответа/idempotent replay.

Воспроизведение после установки requirements-ui.txt и Chromium:

```bash
RUN_UI_TESTS=1 PANEL_VISUAL_DIR=docs/ui-preview python -m pytest tests/ui -q
```

В CI новый тест проверяет поведение без записи PNG, если PANEL_VISUAL_DIR не задан.
[QA с реальным stdout и hash каждого PNG](qa-issue-28-visual.json).

Это эмуляция размеров в Chromium на macOS, не приёмка на iPhone/Safari или всех
устройствах владельца. #28 остаётся открытой для просмотра владельцем; #19 не
закрывается. Дополнительные вымышленные diff/QA-примеры не добавлены: панель
честно показывает, что mock-процесс их не производил. Backend/API/БД/frontend
в этом PR не меняются, только тест и материалы проверки.
