/* Spending approval grants a bounded permission; it never starts inference. */
(() => {
  const el = id => document.getElementById(id);
  const t = (ru,en) => panelLocale.language === 'en' ? en : ru;
  const node = (tag,text,cls) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; };
  const money = n => Number.isSafeInteger(n) ? `$${BigInt(n)/1000000n}.${String(BigInt(n)%1000000n).padStart(6,'0')}` : t('Неизвестно','Unknown');
  const date = n => Number.isSafeInteger(n) ? new Date(n).toLocaleString(panelLocale.language === 'en'?'en-GB':'ru-RU') : t('Неизвестно','Unknown');
  const names = {global:['Общий','Global'],project:['Проект','Project'],task:['Задача','Task']};
  const reasons = {
    pricing_policy_unavailable:['Нет проверенной политики тарифов','Verified pricing policy unavailable'],
    pricing_unknown:['Стоимость неизвестна','Cost unknown'],account_policy_mismatch:['Политика не соответствует текущему API-аккаунту','Policy does not match the current API account'],hermes_unavailable:['Hermes не настроен на сервере','Hermes is not configured on the server'],director_config_changed:['Маршрут Hermes изменился; нужен новый процесс','Hermes route changed; create a new run'], provider_caps_unknown:['Ограничения провайдера не подтверждены','Provider caps unverified'],
    paid_approval_required:['Требуется подтверждение владельца','Owner approval required'],
    verified_free_route:['Выбран проверенный бесплатный маршрут','Verified free route selected'],
    paid_within_approved_budget:['Маршрут разрешён в утверждённом бюджете','Route within approved budget'],
    money_budget:['Лимит денег исчерпан','Money budget exhausted'], token_budget:['Лимит токенов исчерпан','Token budget exhausted'],
    request_limit:['Лимит запросов исчерпан','Request limit exhausted'], provider_cooldown:['Квота / временная блокировка провайдера','Provider quota / cooldown'],
    outcome_unknown:['Исход отправки неизвестен; резерв удерживается','Dispatch outcome unknown; reservation retained'],
    server_policy_changed:['Тарифы изменились; перечитайте оценку','Pricing changed; refresh the estimate'],
    stale_budget_revision:['Бюджет изменился; перечитайте состояние','Budget changed; refresh its state'],
    server_ceiling_exceeded:['Сумма превышает разрешённый сервером предел','Amount exceeds the server ceiling'],
    usage_overrun:['Фактические данные превысили разрешённые границы','Reported usage exceeded approved bounds'],
    route_not_allowed:['Маршрут не разрешён политикой','Route not allowed by policy'],
    deadline_exhausted:['Период бюджета завершён','Budget period ended'], pricing_evidence_expiring:['Подтверждение тарифов скоро истечёт','Pricing evidence expires too soon']
  };
  const reason = code => reasons[code] ? t(...reasons[code]) : code;
  let runId=window.panelCurrentRunId??null, mode=window.panelExecutionMode??'mock';
  let taskId=window.panelCurrentTaskId ?? null, stamp='', data=null, epoch=0, controller, loading=false;
  let pending=null, sending=false, message='';
  function render() {
    for (const [id,ru,en] of [
      ['spendHeading','Бюджет и расходы','Budget and spending'],['refreshSpending','Обновить расходы','Refresh spending'],
      ['spendEstimatesHeading','Оценка до отправки','Estimate before dispatch'],['spendHistoryHeading','Выбор маршрутов и история разрешений','Routing and approval history'],
      ['spendScopeLabel','Лимит для','Budget scope'],['spendRouteLabel','Маршрут для этого разрешения','Route for this approval'],
      ['spendAmountLabel','Общий лимит USD, включая ранее учтённое','Total USD limit, including prior commitments'],
      ['spendConfirmText','Разрешаю этот платный маршрут в указанном лимите','I approve this paid route within this limit'],
      ['approveBudget','Подтвердить лимит','Approve budget'],['revokeBudget','Запретить платный API','Disable paid API'],
      ['retryBudget','Повторить то же подтверждение','Retry the same approval']
    ]) el(id).textContent=t(ru,en);
    el('spendNotice').textContent=t('Платный API по умолчанию запрещён, бюджет — 0. Подтверждение лимита не запускает модели. Отсутствие данных об оплате не означает нулевой расход.','Paid API is disabled by default with a zero budget. Approving a limit does not start models. Missing billing does not mean zero cost.');
    el('spendQuota').textContent=t('Остаток подписки ChatGPT/Codex: неизвестно. Денежная стоимость подписки, локальных вычислений и ручной экспертизы: неизвестно; это отдельные источники учёта. Денежный платёж/квитанция: неизвестно.','ChatGPT/Codex subscription quota: unknown. Subscription, local compute and manual expertise monetary costs: unknown; these are separate sources from API cost accounting. A cash payment/receipt is unknown.');
    el('spendContext').textContent=taskId===null?t('Общий лимит · задача не выбрана','Global budget · no task selected'):t(`Бюджеты для задачи #${taskId}`,`Budgets for task #${taskId}`);
    let state=!data?'':data.accounts.some(a=>a.states.unknown)?reason('outcome_unknown'):data.accounts.some(a=>a.overrun)?reason('usage_overrun'):data.accounts.some(a=>a.cooldown_until_ms>Date.now())?reason('provider_cooldown'):data.accounts.some(a=>a.remaining_requests===0||a.remaining_tokens===0)?t('Квота запросов или токенов исчерпана','Request or token quota exhausted'):!data.policy_available?t('Стоимость неизвестна: политика тарифов не подтверждена. Отправка моделей заблокирована.','Cost unknown: pricing policy unverified. Model dispatch blocked.'):data.accounts.some(a=>a.limits.allow_paid&&a.remaining_micro_usd===0)?t('Платный лимит исчерпан; требуется подтверждение.','Paid budget exhausted; approval required.'):data.accounts.every(a=>a.limits.allow_paid)?t('В утверждённом бюджете. Резерв и подтверждённая стоимость показаны отдельно.','Within approved budget. Reservations and confirmed costs are separate.'):data.estimates.some(q=>q.upper_bound_micro_usd===0&&q.accuracy==='owner_verified_upper_bound')?t('Доступен проверенный бесплатный маршрут. Платный API требует подтверждения.','A verified free route is available. Paid API requires approval.'):t('Требуется подтверждение: платный API запрещён.','Approval required: paid API disabled.');
    const messages={approval_input:t('Укажите сумму, маршрут и подтвердите разрешение.','Enter an amount, choose a route and confirm approval.'),approval_saved:t('Лимит записан. Подтверждение не запускает модели.','Budget recorded. Approval does not start models.'),approval_recorded:t('Подтверждение записано. Модели не запускались.','Approval recorded. No models were started.'),approval_unconfirmed:t('Ответ не подтверждён. Обновите данные или повторите тот же запрос.','Response unconfirmed. Refresh or retry the same request.'),approval_rejected:t('Подтверждение отклонено.','Approval rejected.')};
    const notice=messages[message]||reason(message);
    el('spendStatus').textContent=loading?t('Загрузка…','Loading…'):!data?t('Расходы недоступны. Предыдущее состояние скрыто.','Spending unavailable. Previous state hidden.'):(notice?notice+'\n':'')+state;
    el('spendAccounts').replaceChildren(); el('spendEstimates').replaceChildren(); el('spendHistory').replaceChildren();
    for(const option of el('spendScope').options) {option.textContent=t(...names[option.value]);option.disabled=taskId===null&&option.value!=='global';}
    el('spendApprovalForm').hidden=!data?.policy_available;
    const placeholder=el('spendRoute').querySelector('option[value=""]');if(placeholder)placeholder.textContent=t('Выберите маршрут','Choose a route');
    el('retryBudget').hidden=!pending;
    el('retryBudget').disabled=sending||loading||pending?.task_id!==taskId;
    for(const id of ['spendScope','spendRoute','spendAmount','spendConfirm','approveBudget','revokeBudget'])el(id).disabled=sending||loading||Boolean(pending)||!data?.policy_available;
    if(!data)return;
    for(const account of data.accounts) {
      const card=node('section',undefined,'spend-account');
      card.append(node('h3',t(...names[account.scope])),node('p',`${t('Лимит','Budget')}: ${money(account.limits.max_micro_usd)} · ${t('Остаток','Remaining')}: ${money(account.remaining_micro_usd)}`));
      card.append(node('p',`${t('Учтено вместе с резервами','Committed including reservations')}: ${money(account.budget_committed_micro_usd)}`));
      card.append(node('p',`${t('Подтверждённая стоимость API','Confirmed API cost')}: ${money(account.reported_cost_micro_usd)}`));
      if(!account.reported_cost_complete&&account.known_cost_subtotal_micro_usd)card.append(node('p',`${t('Известная часть, не общий итог','Known subtotal, not total')}: ${money(account.known_cost_subtotal_micro_usd)}`));
      card.append(node('p',`${t('Попытки отправки','Dispatch attempts')}: ${account.dispatches_claimed} · ${t('Учтено токенов с резервом','Committed tokens incl. reservation')}: ${account.tokens_committed}`));
      card.append(node('p',`${t('Подтверждённые токены: вход / выход','Reported tokens: input / output')}: ${account.reported_input_tokens??t('Неизвестно','Unknown')} / ${account.reported_output_tokens??t('Неизвестно','Unknown')}`));
      card.append(node('p',`${t('Окно разрешения','Authorization window')}: ${date(account.created_ms)} → ${date(account.deadline_ms)}`));
      if(account.states.unknown)card.append(node('p',reason('outcome_unknown'),'error'));
      if(account.overrun)card.append(node('p',reason('usage_overrun'),'error'));
      if(account.cooldown_until_ms>Date.now())card.append(node('p',`${reason('provider_cooldown')} · ${date(account.cooldown_until_ms)}`,'error'));
      if(account.policy_current===false)card.append(node('p',reason('server_policy_changed'),'error'));
      el('spendAccounts').append(card);
    }
    const selected=data.selected_route;
    if(selected)el('spendEstimates').append(node('p',!selected.dispatch_required?t('Текущий маршрут: локальная логика. Модели не вызываются.','Current route: local logic. No model dispatch.'):`${t('Текущий маршрут','Current route')}: ${selected.provider}/${selected.model_id||t('не выбран','not configured')} · ${selected.role} · ${money(selected.upper_bound_micro_usd)}${selected.block_code?' · '+reason(selected.block_code):''}`,'spend-selection'));
    for(const quote of data.estimates) {
      const accurate=quote.accuracy==='owner_verified_upper_bound';
      const p=node('p',`${quote.provider}/${quote.model_id} · API\n${t('Верхняя оценка','Upper bound')}: ${money(quote.upper_bound_micro_usd)} · ${accurate?t('проверенная граница, не счёт','verified bound, not an invoice'):t('точность неизвестна','accuracy unknown')}\n${t('Максимум токенов: вход / выход','Maximum tokens: input / output')}: ${quote.maximum_input_tokens} / ${quote.maximum_output_tokens}\n${t('Ожидаемое фактическое число токенов','Expected actual tokens')}: ${t('Неизвестно','Unknown')}`,'spend-quote');
      el('spendEstimates').append(p);
    }
    if(!data.estimates.length)el('spendEstimates').append(node('p',reason('pricing_unknown')));
    for(const event of data.events)el('spendHistory').append(node('p',`${date(event.created_ms)} · ${event.category} · ${event.provider}/${event.model_id}\n${reason(event.code)}`));
    for(const approval of data.approvals)el('spendHistory').append(node('p',`${date(approval.created_ms)} · ${approval.actor} · ${approval.scope_id}\n${money(approval.before.limits.max_micro_usd)} → ${money(approval.after.limits.max_micro_usd)} · ${date(approval.period_start_ms)} → ${date(approval.period_end_ms)}`));
    for(const evidence of data.usage_evidence)el('spendHistory').append(node('p',`${date(evidence.updated_ms)} · ${evidence.provider}/${evidence.model_id}\n${t('Данные ответа API','API response metadata')}: ${evidence.reported.usage.prompt_tokens} / ${evidence.reported.usage.completion_tokens} · ${evidence.reported.usage.cost??t('Неизвестно','Unknown')} ${evidence.reported.reported_cost_unit??t('единицы неизвестны','unit unknown')}`));
    if(data.events_limited||data.approvals_limited||data.usage_evidence_limited)el('spendHistory').append(node('p',t('Показаны последние 100 записей каждого вида.','Showing the latest 100 entries of each kind.')));
    if(!data.events.length&&!data.approvals.length)el('spendHistory').append(node('p',t('Записей пока нет.','No records yet.')));
    const scope=data.accounts.find(a=>a.scope===el('spendScope').value);
    el('spendCeiling').textContent=`${t('Предел сервера','Server ceiling')}: ${money(data.server_ceilings_micro_usd[el('spendScope').value])} · ${t('Версия бюджета','Budget revision')}: ${scope?.revision??0}`;
  }
  async function refresh() {
    const id=++epoch, selected=taskId;
    controller?.abort();controller=new AbortController();
    loading=true;data=null;render();
    try {
      const response=await fetch(selected===null?'/spending/status':`/spending/tasks/${selected}?mode=${mode}${runId===null?'':'&run_id='+runId}`,{signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw new Error('Unavailable');
      const result=await response.json();
      if(id!==epoch)return;
      if(result.task_id!==selected)throw new Error('Context mismatch');
      data=result;
      if(pending&&data.approvals.some(a=>a.id===pending.approval_id)) {pending=null;message='approval_recorded';}
      const previous=el('spendRoute').value;
      el('spendRoute').replaceChildren(new Option(t('Выберите маршрут','Choose a route'),''));
      for(const q of data.estimates.filter(q=>q.accuracy==='owner_verified_upper_bound'&&q.upper_bound_micro_usd>0))el('spendRoute').append(new Option(`${q.provider}/${q.model_id}`,`${q.provider}/${q.model_id}`));
      if([...el('spendRoute').options].some(o=>o.value===previous))el('spendRoute').value=previous;
    } catch(error) {if(id===epoch){data=null;message='';}}
    finally {if(id===epoch){loading=false;render();}}
  }
  async function submit(allowPaid,replay=false) {
    if(sending||loading||(!data&&!replay))return;
    let body=pending;
    if(!replay) {
      if(pending)return;
      const raw=el('spendAmount').value.trim(),match=/^(0|[1-9][0-9]{0,6})(?:\.([0-9]{1,6}))?$/.exec(raw);
      if(allowPaid&&(!el('spendConfirm').checked||!match||!el('spendRoute').value)) {message='approval_input';render();return;}
      const amount=allowPaid?Number(match[1])*1000000+Number((match[2]||'').padEnd(6,'0')):0;
      const account=data.accounts.find(a=>a.scope===el('spendScope').value);
      body={approval_id:crypto.randomUUID().replaceAll('-',''),scope:account.scope,task_id:taskId,
            amount_micro_usd:amount,allow_paid:allowPaid,reference:allowPaid?el('spendRoute').value:null,
            expected_revision:account.revision,expected_policy_sha256:data.policy_sha256,confirm:true};
      pending=body;
    }
    if(body.task_id!==taskId)return;
    sending=true;message='';render();
    const timeout=new AbortController(),timer=setTimeout(()=>timeout.abort(),15000);
    try {
      const response=await fetch('/spending/approvals',{method:'POST',signal:timeout.signal,
        headers:{'Content-Type':'application/json','X-AICommander-Request':'1'},body:JSON.stringify(body)});
      const result=await response.json();
      if(!response.ok) {if(response.status<500)pending=null;const error=new Error('Approval rejected');error.code=result.detail?.code;throw error;}
      pending=null;el('spendConfirm').checked=false;
      message=taskId===body.task_id?'approval_saved':'';
    } catch(error) {message=pending?'approval_unconfirmed':error.code||'approval_rejected';}
    finally {clearTimeout(timer);sending=false;await refresh();}
  }
  el('refreshSpending').onclick=()=>{message='';refresh();};
  el('spendScope').onchange=render;
  el('spendApprovalForm').onsubmit=event=>{event.preventDefault();submit(true);};
  el('revokeBudget').onclick=()=>submit(false);
  el('retryBudget').onclick=()=>submit(pending.allow_paid,true);
  document.addEventListener('panel-language-change',render);
  document.addEventListener('spend-refresh',()=>refresh());
  document.addEventListener('panel-task-change',event=>{
    const next=event.detail.taskId,nextStamp=JSON.stringify(event.detail);
    runId=event.detail.runId;mode=event.detail.mode;
    if(stamp===nextStamp)return;
    stamp=nextStamp;
    if(taskId!==next){taskId=next;el('spendScope').value=next===null?'global':'task';el('spendConfirm').checked=false;message='';}
    refresh();
  });
  el('spendScope').value=taskId===null?'global':'task';refresh();
})();
