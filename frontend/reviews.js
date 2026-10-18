/* Saved trusted-fixture evidence only; never executes or approves a task. */
(() => {
  const byId = id => document.getElementById(id);
  const txt = (ru, en) => panelLocale.language === 'en' ? en : ru;
  let index = null, bundle = null, state = 'loading', generation = 0;
  let controller;
  const states = {
    awaiting_review: ['Ожидает независимого ревью', 'Independent review required'],
    qa_failed: ['QA завершился ошибкой', 'QA failed'],
    cancelled: ['Отменён', 'Cancelled'], stale: ['Устарел', 'Stale'],
    no_changes: ['Нет изменений', 'No changes']
  };
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  function render() {
    byId('reviewsHeading').textContent = txt('Сохранённые пакеты QA', 'Saved QA bundles');
    byId('reviewsNotice').textContent = txt(
      'Отдельные локальные прогоны на тестовых проектах. Не связаны с выбранной задачей или историей Mock/Hermes. Просмотр ничего не запускает.',
      'Separate local runs on fixture projects. Not attached to the selected task or Mock/Hermes history. Viewing does not execute anything.');
    byId('refreshReviews').textContent = txt('Обновить пакеты', 'Refresh bundles');
    byId('reviewSelectLabel').textContent = txt('Пакет прогона', 'Run bundle');
    const messages = {
      loading: txt('Загрузка…', 'Loading…'),
      disabled: txt('Просмотр выключен. Владелец может указать папку LOCAL_REVIEW_ROOT в настройках сервера.', 'Viewer disabled. The owner can configure LOCAL_REVIEW_ROOT on the server.'),
      empty: txt('Сохранённых пакетов нет.', 'No saved bundles.'),
      select: txt('Выберите пакет. Его целостность будет проверена перед показом.', 'Choose a bundle. Its integrity will be checked before display.'),
      failed: txt('Пакет недоступен или проверка целостности не пройдена. Предыдущий результат скрыт.', 'Bundle unavailable or integrity check failed. Previous result hidden.'),
      index_failed: txt('Список пакетов недоступен. Проверьте папку и лимит 100 записей на сервере.', 'Bundle index unavailable. Check the directory and the 100-entry limit on the server.'),
      ready: txt('Целостность пакета проверена.', 'Bundle integrity verified.')
    };
    byId('reviewStatus').textContent = messages[state];
    byId('reviewStatus').className = state.endsWith('failed') ? 'error' : 'hint';
    byId('reviewSelect').disabled = !index?.runs.length || state === 'loading';
    const detail = byId('reviewDetail');
    detail.replaceChildren();
    if (!bundle) return;
    const m = bundle.manifest, qa = bundle.qa_report;
    detail.append(node('h3', `${m.task_id} · ${txt(...states[m.state])}`));
    detail.append(node('p', txt(
      'Источник: одобренные статические изменения; агент-программист не запускался. Хеши не подтверждают автора. Актуальность исходников сейчас не проверена. Независимое ревью и решение владельца обязательны. Готовность к PR и слиянию не подтверждена.',
      'Source: approved static edits; no developer agent ran. Hashes do not authenticate the author. Current source freshness is not verified. Independent review and owner approval are required. PR and merge readiness are not established.'), 'notice warning'));
    detail.append(node('p', `${txt('Прогон', 'Run')}: ${m.run_id}\nQA: ${m.qa_run_id}\n${txt('Ветка', 'Branch')}: ${m.branch}\nBase: ${m.base_commit}\nHead: ${m.head}\nManifest SHA-256: ${bundle.manifest_sha256}`, 'review-provenance'));
    detail.append(node('h3', txt('Тесты по сохранённому отчёту', 'Tests from the saved report')));
    detail.append(node('p', m.qa_passed ? txt('QA: успешно в сохранённом прогоне', 'QA: passed in the saved run') : txt('QA: не пройден', 'QA: not passed'), m.qa_passed ? 'review-pass' : 'error'));
    if (qa.blockers.length) detail.append(node('pre', `${txt('Блокеры', 'Blockers')}: ${qa.blockers.join(', ')}`));
    for (const check of qa.checks) {
      const item = node('details');
      item.append(node('summary', `${check.command_id} · ${check.passed ? txt('успешно', 'passed') : txt('ошибка / не выполнен', 'failed / not run')} · exit ${check.exit_code ?? '—'}`));
      item.append(node('p', `${txt('Исход', 'Outcome')}: ${check.outcome ?? '—'} · ${txt('Секунды', 'Seconds')}: ${check.duration_seconds ?? '—'} · ${txt('Вывод обрезан', 'Output truncated')}: ${check.output_truncated === true ? txt('да', 'yes') : check.output_truncated === false ? txt('нет', 'no') : '—'}`));
      for (const key of ['stdout', 'stderr']) item.append(node('pre', `${key}\n${check[key] ?? ''}`));
      detail.append(item);
    }
    for (const [name, ru, en] of [
      ['requirements.md', 'Требования и риски', 'Requirements and risks'],
      ['changes.diff', 'Изменения · Git diff', 'Changes · Git diff'],
      ['review-request.md', 'Запрос ревью · это не заключение', 'Review request · not a verdict'],
      ['qa-report.json', 'Полный отчёт QA', 'Full QA report']
    ]) {
      const item = node('details');
      item.append(node('summary', txt(ru, en)), node('pre', bundle.artifacts[name]));
      detail.append(item);
    }
  }
  async function get(path, signal) {
    const response = await fetch(path, {signal, cache: 'no-store'});
    if (!response.ok) throw new Error('Unavailable');
    return response.json();
  }
  function start() {
    controller?.abort(); controller = new AbortController();
    bundle = null; state = 'loading'; render();
    return {id: ++generation, signal: controller.signal};
  }
  async function refresh() {
    const {id, signal} = start();
    index = null;
    byId('reviewSelect').replaceChildren();
    try {
      const result = await get('/local-reviews', signal);
      if (id !== generation) return;
      index = result;
      byId('reviewSelect').append(new Option(txt('Выберите пакет', 'Choose a bundle'), ''));
      for (const run of index.runs) byId('reviewSelect').append(new Option(run.run_id, run.run_id));
      state = !index.enabled ? 'disabled' : !index.runs.length ? 'empty' : 'select';
    } catch (error) { if (id === generation) state = 'index_failed'; }
    if (id === generation) render();
  }
  async function select() {
    const selected = byId('reviewSelect').value;
    const {id, signal} = start();
    if (!selected) { state = 'select'; render(); return; }
    try {
      const result = await get(`/local-reviews/${encodeURIComponent(selected)}`, signal);
      if (id !== generation) return;
      bundle = result; state = 'ready';
    } catch (error) { if (id === generation) state = 'failed'; }
    if (id === generation) render();
  }
  byId('refreshReviews').addEventListener('click', refresh);
  byId('reviewSelect').addEventListener('change', select);
  document.addEventListener('panel-language-change', () => {
    const placeholder = byId('reviewSelect').querySelector('option[value=""]');
    if (placeholder) placeholder.textContent = txt('Выберите пакет', 'Choose a bundle');
    render();
  });
  refresh();
})();
