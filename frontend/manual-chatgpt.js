/* Human handoff. This module never reads or controls a ChatGPT account. */
(() => {
  const el=id=>document.getElementById(id),t=(ru,en)=>panelLocale.language==='en'?en:ru;
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const date=n=>Number.isSafeInteger(n)?new Date(n).toLocaleString(panelLocale.language==='en'?'en-GB':'ru-RU'):t('Не проверено','Not checked');
  const uuid=()=>crypto.randomUUID().replaceAll('-','');
  let taskId=window.panelCurrentTaskId??null,data=null,epoch=0,busy=false,pending=null,message='',chosenId=null;
  const errors={effort_unavailable:['Указанный уровень не подтверждён в селекторе','Selected effort is not confirmed in the picker'],effort_not_in_plan:['Уровень не входит в указанный тариф по публичным данным','Effort is not included in this plan according to public data'],higher_effort_unconfirmed:['Высокий режим не подтверждён для этого тарифа','Higher effort is not confirmed for this plan'],selection_unconfirmed:['Подтвердите свежий ручной выбор модели','Confirm a recent manual model selection'],sanitization_confirmation_required:['Проверьте и обезличьте контекст перед экспортом','Review and sanitize context before export'],picker_confirmation_required:['Подтвердите выбор в официальном селекторе','Confirm the choice in the official picker'],origin_confirmation_required:['Подтвердите источник ручного ответа','Confirm the origin of the manual answer']};
  function render() {
    for(const [id,ru,en] of [
      ['manualHeading','Ручная экспертиза ChatGPT','Manual ChatGPT expertise'],['manualSelectStepHeading','1. Подтвердить модель и уровень вручную','1. Confirm model and effort manually'],['manualContextStepHeading','2. Подготовить обезличенный контекст','2. Prepare sanitized context'],['manualAnswerStepHeading','3. Перенести ответ обратно','3. Bring the answer back'],['manualCheckSource','Проверить публичные сведения','Check public information'],
      ['manualPlanLabel','Тариф: указывается владельцем','Plan: entered by the owner'],['manualModelLabel','Название модели из вашего селектора','Model name from your picker'],
      ['manualEffortLabel','Уровень этой сессии · предпочтение High','Session effort · High preferred'],['manualHighText','High доступен мне в селекторе','High is available in my picker'],
      ['manualAnnouncedText','Опубликованная модель уже видна в моём аккаунте','The announced model is visible in my account'],
      ['manualPickerText','Я вручную выбрал самую новую видимую модель и указанный доступный уровень','I manually chose the newest visible model and the available effort shown'],
      ['manualConfirmSelection','Записать мой выбор','Record my choice'],['manualContextLabel','Контекст: проверьте и удалите секреты и личные данные','Context: review and remove secrets and personal information'],
      ['manualPrepare','Подготовить черновик из задачи','Prepare a draft from the task'],['manualSanitizedText','Я проверил и обезличил текст для передачи вручную','I reviewed and sanitized this text for manual sharing'],
      ['manualExport','Сформировать текст запроса','Prepare prompt text'],['manualOpen','Открыть ChatGPT','Open ChatGPT'],['manualPromptLabel','Текст для ручного копирования','Text to copy manually'],
      ['manualExportLabel','Экспорт, к которому относится ответ','Export this answer belongs to'],['manualAnswerLabel','Ответ: вставляется владельцем','Answer: pasted by the owner'],
      ['manualOriginText','Я перенёс ответ из указанной ручной сессии','I copied this answer from the specified manual session'],
      ['manualImport','Сохранить ручной ответ','Save manual answer'],['manualRetry','Повторить тот же ручной запрос','Retry the same manual request'],['manualHistoryHeading','История ручных подтверждений и ответов','Manual choice and answer history']
    ])el(id).textContent=t(ru,en);
    el('manualNotice').textContent=t('Политика: latest_available; предпочтение High. Модель и уровень выбираете вы в официальном селекторе. Текст prompt не включает High автоматически. Приложение не читает сессию, cookies, подписку или ответы ChatGPT.','Policy: latest_available; High preferred. You choose the model and effort in the official picker. Prompt text does not enable High. This app does not read ChatGPT sessions, cookies, subscription details or answers.');
    el('manualSourceState').textContent=data?.source_status==='fresh'?t('Публичный источник проверен. Доступность в аккаунте автоматически не подтверждена.','Public source checked. Account availability is not automatically verified.'):t('Актуальная линейка не подтверждена. Откройте официальный источник и проверьте селектор вручную.','Current lineup unconfirmed. Open the official source and check your picker manually.');
    el('manualDates').textContent=`${t('Успешная проверка','Last successful check')}: ${date(data?.last_success_ms)} · ${t('Последняя попытка','Last attempt')}: ${date(data?.last_attempt_ms)}`;
    el('manualCandidate').textContent=`${data?.source_status==='fresh'?t('Объявленная модель, не выбор аккаунта','Announced model, not an account selection'):t('Сохранённое объявление; актуальность не подтверждена','Saved announcement; current validity unconfirmed')}: ${data?.announced_model??t('Не подтверждено','Unconfirmed')}`;
    el('manualSourceLink').textContent=t('Официальная документация OpenAI','Official OpenAI documentation');
    if(data)el('manualSourceLink').href=data.source_url;
    const phrases={saved:t('Ручной шаг записан. Вызовов модели из приложения не было.','Manual step recorded. No model was called by this app.'),unconfirmed:t('Ответ запроса неизвестен. Обновите состояние или повторите тот же запрос.','Request response unconfirmed. Refresh or retry the same request.'),invalid:t('Проверьте поля и подтверждения.','Check fields and confirmations.'),failed:t('Ручной шаг недоступен.','Manual step unavailable.')};
    el('manualMessage').textContent=phrases[message]||(errors[message]?t(...errors[message]):message);
    el('manualPlan').querySelector('option[value="unknown"]').textContent=t('Неизвестно','Unknown');
    const eligibility=data?.eligibility[el('manualPlan').value]??{};
    const availability=value=>value===true?t('включён по публичным данным','included in public data'):value===false?t('не включён по публичным данным','not included in public data'):t('не подтверждено','unconfirmed');
    el('manualPlanInfo').textContent=`${t('Опубликованные условия тарифа; не проверка аккаунта','Published plan eligibility; not an account check')}: High — ${availability(eligibility.High)}; Extra High — ${availability(eligibility['Extra High'])}; Pro — ${availability(eligibility.Pro)}.`;
    for(const option of el('manualEffort').options)option.disabled=eligibility[option.value]===false||(['Extra High','Pro'].includes(option.value)&&eligibility[option.value]!==true);
    for(const id of ['manualConfirmSelection','manualPrepare','manualExport','manualImport','manualPlan','manualModel','manualEffort','manualHigh','manualAnnounced','manualPicker','manualContext','manualSanitized','manualExportSelect','manualAnswer','manualOrigin'])el(id).disabled=busy||Boolean(pending)||taskId===null||!data;
    el('manualCheckSource').disabled=busy;
    el('manualRetry').hidden=!pending;el('manualRetry').disabled=busy||pending?.taskId!==taskId;
    el('manualHistory').replaceChildren();
    el('manualChosen').textContent='';
    if(!data)return;
    const last=data.selections.find(s=>s.selection_id===chosenId);
    el('manualChosen').textContent=last?t(`Владелец сообщил: ${last.model_label} · ${last.effort} · ${date(last.confirmed_ms)}. Это ручное подтверждение, не автоматическая проверка аккаунта.`,`Owner reported: ${last.model_label} · ${last.effort} · ${date(last.confirmed_ms)}. This is manual confirmation, not an automatic account check.`):t('Выбор модели в аккаунте не подтверждён.','Account model selection unconfirmed.');
    for(const selection of data.selections)el('manualHistory').append(node('p',`${date(selection.confirmed_ms)} · ${selection.model_label} · ${selection.effort} · ${selection.plan} · ${selection.actor}`));
    for(const answer of data.answers){const item=node('details');item.append(node('summary',`${date(answer.created_ms)} · ${t('Ручной ответ','Manual answer')} · ${answer.export_id}`),node('pre',answer.answer));el('manualHistory').append(item);}
    el('manualBoundary').textContent=t('Ответ остаётся внешней ручной заметкой: он не подтверждает QA, независимое ревью или готовность к merge. Квота подписки и денежная стоимость этой сессии неизвестны.','The answer remains an external manual note: it does not establish QA, independent review or merge readiness. Subscription quota and this session’s monetary cost are unknown.');
    const placeholder=el('manualExportSelect').querySelector('option[value=""]');if(placeholder)placeholder.textContent=t('Выберите экспорт','Choose an export');
  }
  async function request(path,body) {
    const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-AICommander-Request':'1'},body:JSON.stringify(body)};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
    try{const response=await fetch(path,{...options,cache:'no-store',signal:controller.signal});const result=await response.json();
    if(!response.ok){const error=new Error('Manual step failed');error.code=result.detail?.code;error.status=response.status;throw error;}
    return result;}finally{clearTimeout(timer);}
  }
  async function refresh() {
    const current=++epoch,selected=taskId;
    try {
      const result=await request('/manual-chatgpt/status'+(selected===null?'':`?task_id=${selected}`));
      if(current!==epoch||result.task_id!==selected)return;
      data=result;
      if(!el('manualModel').value&&data.recommended_model)el('manualModel').value=data.recommended_model;
      const chosen=el('manualExportSelect').value;
      el('manualExportSelect').replaceChildren(new Option(t('Выберите экспорт','Choose an export'),''));
      for(const exported of data.exports)el('manualExportSelect').append(new Option(`${date(exported.created_ms)} · ${exported.id}`,exported.id));
      if(data.exports.some(e=>e.id===chosen))el('manualExportSelect').value=chosen;
    } catch(error) {if(current===epoch){data=null;message='failed';}}
    if(current===epoch)render();
  }
  async function perform(kind,body,replay=false) {
    if(busy)return;
    if(pending&&!replay)return;
    const saved=replay?pending:{kind,body,taskId};
    if(saved.taskId!==taskId)return;
    pending=saved;busy=true;message='';render();
    try {
      const result=await request('/manual-chatgpt/'+saved.kind,saved.body);
      pending=null;
      if(taskId===saved.taskId){
        message='saved';
        if(saved.kind==='selections')chosenId=result.selection_id;
        if(saved.kind==='exports'){
          el('manualPrompt').value=result.prompt;
          await refresh();el('manualExportSelect').value=result.export_id;
        }
      }
    } catch(error){if(error.status&&error.status<500){pending=null;message=error.code||'failed';}else message='unconfirmed';}
    finally{busy=false;await refresh();}
  }
  el('manualCheckSource').onclick=async()=>{if(busy)return;busy=true;render();try{await request('/manual-chatgpt/check-source',{force:true});}catch(error){message='failed';}finally{busy=false;await refresh();}};
  el('manualPlan').onchange=render;
  el('manualSelectionForm').onsubmit=event=>{event.preventDefault();const confirmed=el('manualPicker').checked;perform('selections',{
    selection_id:uuid(),task_id:taskId,model_label:el('manualModel').value,effort:el('manualEffort').value,plan:el('manualPlan').value,
    model_available:confirmed,effort_available:confirmed,high_available:el('manualHigh').checked,announced_available:el('manualAnnounced').checked,
    latest_visible_confirmed:confirmed,confirmed_picker:confirmed});};
  el('manualPrepare').onclick=async()=>{if(busy||taskId===null)return;const selected=taskId;busy=true;render();try{const result=await request(`/manual-chatgpt/tasks/${selected}/context`);if(taskId===selected)el('manualContext').value=result.draft;}catch(error){message='failed';}finally{busy=false;render();}};
  el('manualExport').onclick=()=>{const selected=data?.selections.find(s=>s.selection_id===chosenId);if(!selected){message='selection_unconfirmed';render();return;}perform('exports',{
    export_id:uuid(),task_id:taskId,selection_id:selected.selection_id,reviewed_context:el('manualContext').value,confirm_sanitized:el('manualSanitized').checked,language:panelLocale.language});};
  el('manualImport').onclick=()=>{if(!el('manualExportSelect').value){message='invalid';render();return;}perform('answers',{
    import_id:uuid(),export_id:el('manualExportSelect').value,answer:el('manualAnswer').value,confirm_origin:el('manualOrigin').checked});};
  el('manualRetry').onclick=()=>perform(null,null,true);
  document.addEventListener('panel-language-change',render);
  document.addEventListener('panel-task-change',event=>{
    if(event.detail.taskId===taskId)return;
    taskId=event.detail.taskId;data=null;message='';chosenId=null;epoch++;
    for(const id of ['manualContext','manualPrompt','manualAnswer','manualModel'])el(id).value='';
    for(const id of ['manualPicker','manualHigh','manualAnnounced','manualSanitized','manualOrigin'])el(id).checked=false;
    el('manualEffort').value='High';el('manualExportSelect').replaceChildren();render();refresh();
  });
  refresh();
})();
