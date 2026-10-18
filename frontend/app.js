let mode = 'mock', operations = [], operationError = false;
const isHermes = () => mode === 'hermes';
const flowRoot = () => isHermes() ? '/taskflow/v2' : '/taskflow';
let task = null, tasks = [], projects = [], runs = [], run = null, busy = false, pending = null;
const el = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {planned:'Запланирован', running:'Готов к следующему шагу', failed:'Ошибка', blocked:'Заблокирован', awaiting_owner:'Ожидает решения', done:'План согласован', cancelled:'Отменён', pending:'Ожидает', ready:'Готов', completed:'Завершён', analyst:'Аналитик', architect:'Архитектор', developer:'Разработчик', create:'Создание', start:'Запуск', step:'Шаг', block:'Пауза', resume:'Продолжение', fail:'Ошибка', cancel:'Отмена', owner_approve:'Подтверждение', owner_reject:'Отклонение',
  reviewer:'Ревьюер плана', review:'Ревью плана', requirements:'Требования', design:'Проект', plan:'План'};
const enLabels = {planned:'Planned', running:'Ready for next step', failed:'Failed', blocked:'Blocked',
 awaiting_owner:'Awaiting owner decision', done:'Mock plan approved', cancelled:'Cancelled',
 pending:'Pending', ready:'Ready', completed:'Completed', analyst:'Analyst', architect:'Architect',
 developer:'Developer', create:'Created', start:'Started', step:'Step', block:'Paused',
 resume:'Resumed', fail:'Failed', cancel:'Cancelled', owner_approve:'Approved', owner_reject:'Rejected',
 reviewer:'Plan reviewer', review:'Plan review', requirements:'Requirements', design:'Design', plan:'Plan'};
const label = key => isHermes() && key === 'running' ? (panelLocale.language === 'en' ? 'Active process' : 'Процесс активен') : isHermes() && key === 'done' ? (panelLocale.language === 'en' ? 'Planning artifacts approved' : 'План согласован') : panelLocale.language === 'en' ? (enLabels[key] || labels[key] || key) : (labels[key] || key);
const tr = value => panelLocale.tr(value);
let cachedModels = null;
async function api(path, method = 'GET', payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const options = {method, signal:controller.signal, credentials:'same-origin', headers:{'Content-Type':'application/json','X-AICommander-Request':'1'}};
    if (payload !== undefined) options.body = JSON.stringify(payload);
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail;
      const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'Некорректный запрос');
      error.httpStatus = response.status;
      throw error;
    }
    return data;
  } finally { clearTimeout(timeout); }
}
function status(text) { el('status').textContent = tr(text); }
function controls() {
  const locked = busy || Boolean(pending), state = run?.status;
  el('flowMode').disabled = locked;
  el('processActionsHeading').parentElement.classList.toggle('hermes-actions', isHermes());
  el('projectSelect').disabled = locked;
  el('maxSteps').disabled = locked || isHermes();
  el('maxSteps').parentElement.hidden = isHermes();
  el('runSelect').parentElement.parentElement.classList.toggle('hermes-settings', isHermes());
  for (const id of ['newRun','runTask','stepTask','pauseTask','approveTask','rejectTask']) el(id).hidden = isHermes();
  el('runSelect').disabled = locked;
  el('newRun').disabled = locked || isHermes() || !task;
  el('runTask').disabled = locked || isHermes() || !['planned','blocked','failed'].includes(state) || (['blocked','failed'].includes(state) && run.steps_done >= run.max_steps && run.stages.some(s => s.status !== 'completed'));
  el('runTask').textContent = state === 'planned' ? tr('Запустить процесс') : tr('Продолжить / Повторить');
  el('stepTask').disabled = locked || isHermes() || state !== 'running';
  el('pauseTask').disabled = locked || isHermes() || state !== 'running';
  el('stopTask').disabled = locked || !['planned','running','blocked','failed','awaiting_owner'].includes(state);
  el('approveTask').disabled = locked || isHermes() || state !== 'awaiting_owner';
  el('rejectTask').disabled = locked || isHermes() || state !== 'awaiting_owner';
  el('retryRequest').hidden = !pending;
  el('retryRequest').disabled = busy || !pending;
  document.querySelectorAll('button[type="submit"], [data-task]').forEach(button => button.disabled = locked);
}
async function action(fn) {
  if (busy) return;
  busy = true; controls();
  try { await fn(); status('Сохранено'); }
  catch (error) { status(error.message); }
  finally { busy = false; controls(); }
}
function renderTasks() {
  el('taskList').innerHTML = tasks.filter(t => String(t.project_id) === el('projectSelect').value).map(t =>
    `<button class="task-choice ${task?.id === t.id ? 'selected' : ''}" data-task="${t.id}">${esc(t.title)}<small>${esc(isHermes() ? (panelLocale.language === 'en' ? 'Open Hermes history' : 'Открыть историю Hermes') : t.flow_status ? label(t.flow_status) : tr('Нет taskflow'))}</small></button>`).join('') || `<p>${esc(tr('Пока нет задач.'))}</p>`;
  el('taskList').querySelectorAll('[data-task]').forEach(button => { button.disabled = busy || Boolean(pending); button.onclick = () => action(() => selectTask(Number(button.dataset.task))); });
}
// The thread is a *view* of persisted taskflow snapshots. No invented LLM chat,
 // shell execution, model call, or QA report is generated here.
function renderDirector() {
  const thread = el('directorMessages');
  if (!thread) return;
  const en = panelLocale.language === 'en';
  const bubble = (kind, heading, content) =>
    `<article class="chat-bubble ${kind}"><strong>${esc(heading)}</strong><p>${esc(content)}</p></article>`;
  if (!task) {
    thread.innerHTML = bubble('mock', isHermes() ? 'Hermes' : 'Mock Director',
      isHermes() ? (en ? 'Select a task to view stored Hermes planning.' : 'Выбери задачу для просмотра сохранённого планирования Hermes.') : en ? 'Select a task to view its saved mock workflow.' : 'Выбери задачу, чтобы увидеть сохранённый mock-процесс.');
    return;
  }
  const messages = [bubble('owner', en ? 'Owner · saved requirement' : 'Владелец · сохранённое требование', task.description)];
  if (!run) {
    messages.push(bubble('mock', isHermes() ? 'Hermes' : 'Mock Director',
      isHermes() ? (en ? 'No stored Hermes runs for this task. Model launch is unavailable in this panel.' : 'У задачи нет сохранённых процессов Hermes. Запуск моделей из панели недоступен.') : en ? 'No run selected. Create a run; this does not contact Hermes or execute code.'
         : 'Процесс не выбран. Создай процесс; это не вызывает Hermes и не исполняет код.'));
  } else {
    const state = isHermes()
      ? (en ? `Saved planning status: ${runLabel()}. Steps ${run.steps_done}/${run.max_steps}. Code and QA were not executed.` : `Сохранённое планирование: ${runLabel()}. Шаги ${run.steps_done}/${run.max_steps}. Код и QA не выполнялись.`)
      : en
      ? `Saved status: ${label(run.status)}. Steps ${run.steps_done}/${run.max_steps}. This is only a mock process; no real coding or QA occurred.`
      : `Сохранённый статус: ${label(run.status)}. Шаги ${run.steps_done}/${run.max_steps}. Это только mock-процесс, реальное программирование и QA не выполнялись.`;
    messages.push(bubble('mock', isHermes() ? (en ? 'Hermes · saved state' : 'Hermes · сохранённое состояние') : en ? 'Mock Director · saved state' : 'Mock Director · сохранённое состояние', state));
    for (const artifact of run.artifacts || []) {
      const description = en
        ? `Stored typed artifact: ${artifact.type}. Open the artifact panel below for the actual content. Not a verified code change.`
        : `Сохранён типизированный артефакт: ${artifact.type}. Фактическое содержимое ниже в блоке артефактов. Это не подтверждённое изменение кода.`;
      messages.push(bubble('artifact', `${label(artifact.role)} · #${artifact.artifact_id}`, description));
    }
  }
  thread.innerHTML = messages.join('');
}

function runLabel(snapshot = run) {
  if (isHermes() && snapshot?.status === 'running') return panelLocale.language === 'en' ? 'Active process' : 'Процесс активен';
  if (isHermes() && snapshot?.status === 'done') return panelLocale.language === 'en' ? 'Planning artifacts approved' : 'План согласован';
  return label(snapshot?.status);
}
function renderMode() {
  // Restore translated static Mock copy before applying Hermes-only copy.
  panelLocale.apply();
  const en = panelLocale.language === 'en';
  el('modeHint').textContent = isHermes()
    ? (en ? 'Stored Hermes history: view and cancel only. Starting models and approving plans are unavailable here.' : 'История Hermes: доступны просмотр и отмена. Запуск моделей и согласование планов здесь недоступны.') : '';
  el('operationPanel').hidden = !isHermes();
  if (!isHermes()) return;
  document.querySelector('.notice.warning').textContent = en
    ? 'Hermes · stored planning artifacts. Code, task tests and deployment were not executed. Viewing history does not call models.'
    : 'Hermes · сохранённые артефакты планирования. Код, тесты задач и deploy не выполнялись. Просмотр истории не вызывает модели.';
  el('actionHint').textContent = en ? 'Cancellation is final. An unknown operation is never automatically restarted.' : 'Отмена окончательная. Операция с неизвестным исходом не перезапускается автоматически.';
  el('directorHeading').textContent = en ? 'Hermes · saved activity' : 'Hermes · сохранённый ход работы';
  el('directorDisclaimer').textContent = en ? 'Only stored events and artifacts are shown. A planning review is not a code or test review.' : 'Показаны только сохранённые события и артефакты. Ревью плана не является проверкой кода или тестов.';
  el('directorMessages').setAttribute('aria-label', en ? 'Hermes process history' : 'История процесса Hermes');
  document.querySelector('label[for="commentText"]').textContent = en ? 'Cancellation comment' : 'Комментарий к отмене';
  el('artifactsHeading').textContent = en ? 'Planning artifacts · Hermes' : 'Артефакты планирования · Hermes';
}
function renderOperations() {
  const en = panelLocale.language === 'en';
  const names = en
    ? {running:'In progress',result_ready:'Result saved, awaiting commit',unknown:'Outcome unknown',succeeded:'Committed',failed:'Failed',discarded:'Discarded'}
    : {running:'Выполняется',result_ready:'Результат сохранён, ожидает фиксации',unknown:'Исход неизвестен',succeeded:'Зафиксирована',failed:'Ошибка',discarded:'Отброшена'};
  el('operations').innerHTML = operations.map(op => `<article class="operation"><strong>${esc(label(op.role))} · ${esc(names[op.state] || op.state)}</strong><p>${esc(op.id)}</p><p>${esc(op.cancel_requested ? (en ? 'Cancellation requested' : 'Отмена запрошена') : '')}</p><small>${esc(op.updated_at)}</small></article>`).join('') || `<p>${esc(operationError ? (en ? 'Could not read operations.' : 'Не удалось прочитать операции.') : en ? 'No stored operations.' : 'Сохранённых операций нет.')}</p>`;
  el('operationHint').textContent = operationError ? (en ? 'Operation status unavailable. Refresh before taking further action.' : 'Статус операций недоступен. Обновите данные перед дальнейшими действиями.') : (operations.some(op => op.state === 'unknown')
    ? (en ? 'The call may have been sent. Automatic retry is blocked. After confirming the worker stopped, an operator must resolve the outcome. Cancellation does not resolve an unknown outcome.' : 'Вызов мог быть отправлен. Автоматический повтор запрещён. После подтверждения остановки worker оператор должен разрешить исход. Отмена не разрешает неизвестный исход.')
    : en ? 'Refresh to read the latest state. Viewing and switching languages never restart operations.' : 'Обновите данные для актуального состояния. Просмотр и смена языка не перезапускают операции.');
}
// Traffic lights are derived exclusively from the selected run snapshot and saved
// operation metadata. A completed Hermes Reviewer means plan review, never code QA.
function renderProgress() {
  const en = panelLocale.language === 'en';
  const stateNames = en
    ? {pending:'Not started',active:'In progress',completed:'Completed',error:'Blocked / failed',
        waiting:'Awaiting owner',unknown:'Outcome unknown'}
    : {pending:'Не начато',active:'Выполняется',completed:'Завершено',error:'Ошибка / блокировка',
        waiting:'Ожидает владельца',unknown:'Исход неизвестен'};
  const items = [];
  const add = (name, state) => {
    const safe = Object.hasOwn(stateNames, state) ? state : 'pending';
    items.push(`<li class="stage-indicator" data-state="${safe}"><span>${esc(name)}<br><small>${esc(stateNames[safe])}</small></span></li>`);
  };
  if (!task) {
    add(en ? 'Task' : 'Задача', 'pending');
  } else {
    add(en ? 'Task created' : 'Задача создана', 'completed');
    if (run) {
      for (const stage of run.stages || []) {
        const unknownOperation = isHermes() && operations.some(op => op.role === stage.role && op.state === 'unknown');
        const stageStatus = unknownOperation ? 'unknown'
          : stage.status === 'completed' ? 'completed'
          : ['failed','blocked','cancelled'].includes(stage.status) ? 'error'
          : stage.status === 'ready' && run.status === 'running' ? 'active' : 'pending';
        add(label(stage.role), stageStatus);
      }
      const ownerState = run.status === 'awaiting_owner' ? 'waiting'
        : run.status === 'done' ? 'completed'
        : run.owner_decision === 'rejected' ? 'error' : 'pending';
      add(en ? 'Owner decision' : 'Решение владельца', ownerState);
      add(en ? 'Planning outcome' : 'Итог планирования',
        run.status === 'done' ? 'completed' : ['failed','cancelled'].includes(run.status) ? 'error' : 'pending');
    }
  }
  el('stageProgress').innerHTML = items.join('');

  const unknown = isHermes() && operations.some(op => op.state === 'unknown');
  const opUnavailable = isHermes() && operationError;
  el('stageSummary').textContent = !task ? (en ? 'Select a task.' : 'Выбери задачу.')
    : !run ? (en ? 'No run selected; execution status unavailable.' : 'Процесс не выбран; данных о выполнении нет.')
    : unknown ? (en ? 'Operation outcome unknown — do not retry automatically.' : 'Исход операции неизвестен — автоматический повтор запрещён.')
    : opUnavailable ? (en ? 'Operation state unavailable; no completion is assumed.' : 'Состояние операций недоступно; завершение не подтверждено.')
    : `${isHermes() ? 'Hermes · ' : 'Mock Director · '}${runLabel()}. ${en ? 'Steps' : 'Шаги'} ${run.steps_done}/${run.max_steps}. ${en ? 'Planning only; code and QA not verified.' : 'Только планирование; код и QA не проверены.'}`;

  const entries = [
    [en ? 'Source' : 'Источник', isHermes() ? 'Hermes · planning' : 'Mock Director'],
    [en ? 'Task ID' : 'ID задачи', task?.id],
    [en ? 'Run ID' : 'ID процесса', run?.run_id],
    [en ? 'Run status' : 'Статус процесса', run ? runLabel() : null],
    [en ? 'Step counter' : 'Шаги', run ? `${run.steps_done}/${run.max_steps}` : null],
    [en ? 'Current role' : 'Текущая роль', run?.stages?.find(stage => stage.status === 'ready')?.role
      ? label(run.stages.find(stage => stage.status === 'ready').role) : null],
    [en ? 'Last saved action' : 'Последнее событие',
      run?.timeline?.length ? label(run.timeline[run.timeline.length - 1].action) : null],
    [en ? 'Operation ID' : 'ID операции', isHermes() && !operationError && operations.length ? operations[operations.length - 1].id : null],
    [en ? 'Operation state' : 'Состояние операции', isHermes() ?
      operationError ? (en ? 'Unavailable' : 'Недоступно') :
      operations.length ? ({running:en?'In progress':'Выполняется',
        result_ready:en?'Result saved':'Результат сохранён',
        unknown:en?'Unknown outcome':'Исход неизвестен',
        succeeded:en?'Committed':'Зафиксировано',
        failed:en?'Failed':'Ошибка',
        discarded:en?'Discarded':'Отброшено'}[operations[operations.length - 1].state] || (en?'Unknown':'Неизвестно')) : null : null],
    [en ? 'Code execution / QA' : 'Исполнение кода / QA', en ? 'Not verified' : 'Не проверено']
  ];
  const fallback = en ? 'No data' : 'Нет данных';
  el('processDiagnostics').innerHTML = entries.map(([name, value]) =>
    `<div><dt>${esc(name)}</dt><dd>${esc(value === null || value === undefined ? fallback : value)}</dd></div>`).join('');
}

function renderDetail() {
  renderMode();
  el('taskTitle').textContent = task?.title || tr('Выбери задачу');
  el('taskMeta').textContent = run ? (panelLocale.language === 'en' ? `Task #${task.id} · Run #${run.run_id} · ${runLabel()} · Version ${run.version} · Steps ${run.steps_done}/${run.max_steps}` : `Задача #${task.id} · Процесс #${run.run_id} · ${runLabel()} · Версия ${run.version} · Шаги ${run.steps_done}/${run.max_steps}`) : tr('Процесс не выбран');
  el('description').textContent = task?.description || '';
  el('runSelect').innerHTML = runs.map(r => `<option value="${r.run_id}">#${r.run_id} · ${esc(runLabel(r))}</option>`).join('');
  if (run) el('runSelect').value = run.run_id;
  el('flowReason').textContent = run?.reason || '';
  el('roleBoard').innerHTML = (run?.stages || []).map(s => `<article class="role"><strong>${esc(label(s.role))}</strong><p>${esc(label(s.status))}</p></article>`).join('');
  el('artifacts').innerHTML = (run?.artifacts || []).map(a => `<details><summary>${esc(label(a.role))} · ${esc(a.type)} · #${a.artifact_id} · ${isHermes() ? 'Hermes · '+esc(panelLocale.language === 'en' ? 'planning only' : 'только планирование') : 'mock'}</summary><p>${esc(tr('Входы:'))} ${esc(a.input_artifact_ids.join(', ') || tr('нет'))} · ${esc(a.created_at)}</p>${isHermes() && a.provenance ? `<p class="provenance">${esc(a.provenance.origin)} · ${esc(a.provenance.model_id)}<br>SDK: ${esc(a.provenance.sdk_commit)}<br>Operation: ${esc(a.provenance.operation_id)}<br>Input SHA-256: ${esc(a.provenance.input_sha256)}</p>` : ''}<pre>${esc(JSON.stringify(a.content,null,2))}</pre></details>`).join('') || `<p>${esc(tr('Артефактов пока нет.'))}</p>`;
  el('decisions').innerHTML = (run?.timeline || []).map(e => `<p>v${e.version} · ${esc(label(e.action) || e.action)} → ${esc(label(e.to_status))} · ${esc(e.created_at)}<br>${esc(e.reason)}</p>`).join('') || `<p>${esc(tr('Событий пока нет.'))}</p>`;
  el('rounds').innerHTML = (task?.rounds || []).map(r => `<article class="round-card"><h4>${esc(panelLocale.language === 'en' ? 'Legacy round' : 'Legacy раунд')} ${r.round_number}</h4>` + r.role_outputs.map(o => `<details><summary>${esc(o.role)} · ${esc(o.status)}</summary><pre>${esc(o.output)}</pre></details>`).join('') + '</article>').join('') || `<p>${esc(tr('Legacy-раундов нет.'))}</p>`;
  controls(); renderTasks(); renderDirector(); renderOperations(); renderProgress(); renderCachedModels();
}
async function selectTask(id, selectedRun) {
  const [selectedTask, selectedRuns] = await Promise.all([api('/tasks/' + id), api(`${flowRoot()}/tasks/${id}/runs`)]);
  task = selectedTask; runs = selectedRuns;
  run = runs.find(r => r.run_id === selectedRun) || runs[0] || null;
  operations = []; operationError = false;
  if (isHermes() && run) {
    try { operations = await api(`${flowRoot()}/runs/${run.run_id}/operations`); }
    catch (error) { operationError = true; }
  }
  renderDetail();
}
async function refreshTasks() {
  tasks = await api('/tasks');
  if (task) await selectTask(task.id, run?.run_id);
  else renderTasks();
}
function renderCachedModels() {
  if (!cachedModels) return;
  el('cost').textContent = isHermes() ? (panelLocale.language === 'en' ? 'Stored planning artifacts are not a cost report. Actual model costs are unavailable.' : 'Артефакты планирования не являются отчётом о затратах. Фактическая стоимость вызовов недоступна.') : tr('Taskflow работает с Mock Director без вызовов моделей. Учёт затрат реальных моделей ожидает #18.');
  el('models').innerHTML = (isHermes() ? [] : cachedModels.models).map(m => `<p>${esc(m.role)} · ${esc(m.model_id)} · ${esc(m.status)}</p>`).join('') || `<p>${esc(isHermes() ? (panelLocale.language === 'en' ? 'Provider availability and quotas are not checked in this view.' : 'Доступность провайдеров и квоты в этом режиме не проверяются.') : tr('В legacy-раундах модели ещё не вызывались.'))}</p>`;
}
async function refreshModels() {
  cachedModels = await api('/panel/status');
  renderCachedModels();
}
function renderProjects(selected) {
  el('projectSelect').innerHTML = projects.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  if (selected) el('projectSelect').value = selected;
}
async function sendPending() {
  const request = pending;
  let response;
  try { response = await api(request.path,'POST',request.body); }
  catch (error) {
    if (error.httpStatus && error.httpStatus < 500) {
      pending = null;
      // Конфликт версии требует нового снимка и нового решения пользователя.
      if (error.httpStatus === 409) await selectTask(request.taskId, request.runId);
    } else {
      throw new Error(tr('Результат запроса неизвестен. Повторите тот же запрос кнопкой «Повторить запрос».'));
    }
    throw error;
  }
  pending = null;
  tasks = await api('/tasks');
  // Replay может вернуть старую версию: всегда перечитываем актуальный снимок.
  await selectTask(request.taskId, response.run_id);
  el('projectSelect').value = task.project_id;
  renderTasks();
}
function mutate(path, body) {
  if (pending) return;
  if (isHermes() && !(path === `${flowRoot()}/runs/${run?.run_id}/commands` && body.action === 'cancel')) throw new Error('Hermes: view and cancel only');
  pending = {path, body:{...body,idempotency_key:crypto.randomUUID()},taskId:task.id,runId:run?.run_id};
  return sendPending();
}
el('projectForm').onsubmit = event => { event.preventDefault(); action(async () => {
  const project = await api('/projects','POST',{name:el('projectName').value});
  projects.push(project); renderProjects(project.id); el('projectName').value = ''; task = run = null; runs = []; operations = []; operationError = false; renderDetail();
}); };
el('flowMode').onchange = () => action(async () => {
  mode = el('flowMode').value; el('commentText').value = ''; run = null; runs = []; operations = []; operationError = false; renderDetail();
  if (task) await selectTask(task.id);
});
el('projectSelect').onchange = () => { task = run = null; runs = []; operations = []; operationError = false; renderDetail(); };
el('runSelect').onchange = () => action(() => selectTask(task.id,Number(el('runSelect').value)));
el('taskForm').onsubmit = event => { event.preventDefault(); action(async () => {
  task = await api('/tasks','POST',{description:el('taskText').value,project_id:Number(el('projectSelect').value)});
  el('taskText').value = ''; run = null; runs = []; await refreshTasks();
}); };
el('newRun').onclick = () => action(() => mutate(`${flowRoot()}/tasks/${task.id}/runs`, {max_steps:Number(el('maxSteps').value)}));
function command(actionName) {
  return mutate(`${flowRoot()}/runs/${run.run_id}/commands`, {action:actionName,expected_version:run.version,reason:el('commentText').value});
}
el('runTask').onclick = () => action(() => command(run.status === 'planned' ? 'start' : 'resume'));
el('stepTask').onclick = () => action(() => command('step'));
el('pauseTask').onclick = () => action(() => command('block'));
el('stopTask').onclick = () => action(() => command('cancel'));
for (const [id, decision] of [['approveTask','approve'],['rejectTask','reject']]) {
  el(id).onclick = () => action(() => mutate(`${flowRoot()}/runs/${run.run_id}/owner-decision`,{decision,expected_version:run.version,reason:el('commentText').value}));
}
el('retryRequest').onclick = () => action(sendPending);
el('refreshTasks').onclick = () => action(refreshTasks);
el('refreshModels').onclick = () => action(refreshModels);
async function init() {
  try { projects = await api('/projects'); renderProjects(); await refreshTasks(); await refreshModels(); status('Подключено'); }
  catch (error) { status(error.message); }
  controls(); renderDirector();
}
el('languageSelect').onchange = () => panelLocale.setLanguage(el('languageSelect').value);
document.addEventListener('panel-language-change', () => {
  renderDetail();
});
panelLocale.apply();
init();
