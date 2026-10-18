// UI-only localization. The original Russian labels are the source of truth.
// Never translate user-provided task/artifact data or server-returned errors.
(() => {
  const dictionary = {
    'Подключение…':'Connecting…', 'Подключено':'Connected',
    'Сохранение…':'Saving…', 'Все изменения сохранены':'All changes saved',
    'Данные обновлены':'Data refreshed',
    'Запустить процесс':'Start process', 'Продолжить / Повторить':'Resume / retry',
    'Нет taskflow':'No taskflow', 'Пока нет задач.':'No tasks yet.',
    'Выбери задачу':'Choose a task', 'Процесс не выбран':'No run selected',
    'Входы:':'Inputs:', 'нет':'none', 'Артефактов пока нет.':'No artifacts yet.',
    'Событий пока нет.':'No events yet.', 'Legacy-раундов нет.':'No legacy rounds.',
    'Результат запроса неизвестен. Повторите тот же запрос кнопкой «Повторить запрос».':
      'Request outcome is unknown. Retry the exact same request with the Retry request button.',
    'Taskflow работает с Mock Director без вызовов моделей. Учёт затрат реальных моделей ожидает #18.':
      'Taskflow uses Mock Director without model calls. Real model cost tracking is planned for #18.',
    'В legacy-раундах модели ещё не вызывались.':'No models have been called in legacy rounds yet.'
  };
  const selectors = [
    ['label[for="languageSelect"]','Language'],
    ['#languageSelect option[value="ru"]','Russian'],
    ['#languageSelect option[value="en"]','English'],
    ['.hero .eyebrow','AICommander · Owner panel'],
    ['.hero h1','Your AI team'],
    ['.hero > div:first-child > p:nth-of-type(2)','Projects, work history and decisions in one place.'],
    ['.notice.warning','Offline taskflow · Mock Director. Requirements → design → plan are generated deterministically. Approval accepts only mock artifacts; no code, models, QA or deployment are run.'],
    ['aside.card h2:first-of-type','Projects'],
    ['aside.card h2:last-of-type','Tasks'],
    ['label[for="projectSelect"]','Current project'],
    ['label[for="projectName"]','New project'],
    ['#projectForm button','Add project'],
    ['#refreshTasks','Refresh'],
    ['section > article.card:first-child > h2','New task'],
    ['#taskForm label','What to do'],
    ['#taskForm button','Create task'],
    ['#taskDetail label[for="commentText"]','Comment for decision or next run'],
    ['#taskDetail label[for="runSelect"]','Run and history'],
    ['#taskDetail label[for="maxSteps"]','Maximum steps for a new run'],
    ['label[for="flowMode"]','History mode'],
    ['#flowMode option[value="mock"]','Mock · test workflow'],
    ['#flowMode option[value="hermes"]','Hermes · stored planning'],
    ['#operationsHeading','Hermes operations'],
    ['#stageHeading','Workflow stages'],
    ['#diagnosticsHeading','Process diagnostics'],
    ['#processActionsHeading','Process controls'],
    ['#ownerActionsHeading','Owner decision'],
    ['#newRun','New run'],
    ['#stepTask','Next step'],
    ['#pauseTask','Pause'],
    ['#stopTask','Cancel run'],
    ['#approveTask','Approve'],
    ['#rejectTask','Reject'],
    ['#retryRequest','Retry request'],
    ['#actionHint','Each step creates one mock artifact. Cancellation is final; create a new run to restart. Rejected plans can resume without recreating artifacts.'],
    ['#directorHeading','Director activity · demo'],
    ['#directorDisclaimer','This displays the stored Mock Director process. Real Hermes and code execution are not connected.'],
    ['#taskDetail > h3:nth-of-type(1)','Role stages'],
    ['#taskDetail > h3:nth-of-type(2)','Typed mock artifacts'],
    ['#taskDetail > h3:nth-of-type(3)','Events and owner decisions'],
    ['#taskDetail .grid.two section:first-child p','No verified Git diff is available for this run.'],
    ['#taskDetail .grid.two section:last-child p','No real task QA report yet (#16). A model response is not test evidence.'],
    ['#taskDetail > details > summary','Legacy round history (separate from taskflow)'],
    ['#taskDetail .grid.two section:first-child h3','Git diff'],
    ['#taskDetail .grid.two section:last-child h3','Tests / QA'],
    ['section > article.card:last-child > h2','Models and costs'],
    ['#refreshModels','Refresh status']
  ];
  const panelLocale = {
    language:'ru',
    tr(value) { return this.language === 'en' ? dictionary[value] || value : value; },
    apply() {
      document.documentElement.lang = this.language;
      document.getElementById('languageSelect').setAttribute('aria-label',
        this.language === 'en' ? 'Interface language' : 'Выбор языка интерфейса');
      document.getElementById('stageProgress').setAttribute('aria-label',
        this.language === 'en' ? 'Workflow stage status' : 'Статус этапов процесса');
      const thread = document.getElementById('directorMessages');
      if (thread) thread.setAttribute('aria-label', this.language === 'en' ? 'Mock process history' : 'История mock-процесса');
      for (const [selector, english] of selectors) {
        const element = document.querySelector(selector);
        if (!element) continue;
        if (!Object.hasOwn(element.dataset, 'originalText')) element.dataset.originalText = element.textContent;
        element.textContent = this.language === 'en' ? english : element.dataset.originalText;
      }
      const input = document.querySelector('#taskText');
      if (input) {
        if (!Object.hasOwn(input.dataset, 'originalPlaceholder')) input.dataset.originalPlaceholder = input.placeholder;
        input.placeholder = this.language === 'en' ? 'Describe the requirements and acceptance criteria' : input.dataset.originalPlaceholder;
      }
      const state = document.querySelector('#status');
      // Preserve actual API outcome when switching RU/EN; never infer success from UI text.
      if (state?.dataset.statusKey) state.textContent = this.tr(state.dataset.statusKey);
    },
    setLanguage(lang) {
      if (!['ru','en'].includes(lang)) return;
      this.language = lang;
      this.apply();
      document.dispatchEvent(new Event('panel-language-change'));
    }
  };
  window.panelLocale = panelLocale;
})();
