"""Браузерные сценарии через настоящий HTTP-сервер и временную SQLite."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_UI_TESTS') != '1', reason='RUN_UI_TESTS=1 и requirements-ui.txt включают Chromium UI tests')





def create_task(page, expect, description):
    page.locator('#taskText').fill(description)
    page.get_by_role('button', name='Создать задачу', exact=True).click()
    expect(page.locator('#taskTitle')).to_have_text(description)


def new_run(page, expect, max_steps=3):
    page.locator('#maxSteps').fill(str(max_steps))
    page.locator('#newRun').click()
    expect(page.locator('#taskMeta')).to_contain_text('Запланирован')


def start(page, expect):
    page.locator('#runTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Готов к следующему шагу')


def step(page, expect, count):
    page.locator('#stepTask').click()
    expect(page.locator('#artifacts details')).to_have_count(count)


def current(page):
    run_id=page.locator('#runSelect').input_value()
    return page.request.get('/taskflow/runs/'+run_id).json()


def external_command(page, action):
    run=current(page)
    result=page.request.post(f"/taskflow/runs/{run['run_id']}/commands",headers={'X-AICommander-Request':'1'},data={
        'idempotency_key':f"external-{run['version']}", 'expected_version':run['version'],'action':action})
    assert result.ok
    return result.json()


def test_projects_handoff_reject_resume_approve_reload_and_mobile(browser_page):
    page, expect=browser_page
    requests=[]
    page.on('request',lambda request: requests.append(request.url))
    page.locator('#projectName').fill('Склад')
    page.get_by_role('button',name='Добавить проект',exact=True).click()
    expect(page.locator('#projectSelect option:checked')).to_have_text('Склад')
    create_task(page,expect,'Приёмка товара')
    new_run(page,expect)
    expect(page.locator('#approveTask')).to_be_disabled()
    start(page,expect)
    for count in range(1,4):
        step(page,expect,count)
    expect(page.locator('#taskMeta')).to_contain_text('Ожидает решения')
    artifacts=current(page)['artifacts']
    assert artifacts[1]['content']['requirements']==artifacts[0]['content']
    assert artifacts[2]['content']['design']==artifacts[1]['content']
    page.locator('#commentText').fill('Уточнить план')
    page.locator('#rejectTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Заблокирован')
    expect(page.locator('#decisions')).to_contain_text('Уточнить план')
    page.locator('#runTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Ожидает решения')
    assert current(page)['artifacts']==artifacts
    page.locator('#commentText').fill('Принимаю mock-план')
    page.locator('#approveTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('План согласован')
    expect(page.locator('#stepTask')).to_be_disabled()
    page.reload()
    expect(page.locator('#status')).to_have_text('Подключено')
    page.locator('#projectSelect').select_option(label='Склад')
    page.locator('[data-task]').click()
    expect(page.locator('#decisions')).to_contain_text('Принимаю mock-план')
    expect(page.locator('#artifacts details')).to_have_count(3)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if os.environ.get('PANEL_SCREENSHOT'):
        page.set_viewport_size({'width':1440,'height':1050})
        page.evaluate('scrollTo(0,0)')
        page.screenshot(path=os.environ['PANEL_SCREENSHOT'],full_page=True)
    assert not any('/rounds' in url or '/decision' in url.replace('/owner-decision','') or url.endswith('/run') for url in requests)


def test_cancel_new_run_history_and_step_limit(browser_page):
    page,expect=browser_page
    create_task(page,expect,'Остановить процесс')
    new_run(page,expect)
    start(page,expect)
    step(page,expect,1)
    old_run=current(page)
    page.locator('#stopTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Отменён')
    expect(page.locator('#runTask')).to_be_disabled()
    expect(page.locator('#approveTask')).to_be_disabled()
    new_run(page,expect,max_steps=1)
    start(page,expect)
    step(page,expect,1)
    expect(page.locator('#taskMeta')).to_contain_text('Заблокирован')
    expect(page.locator('#flowReason')).to_contain_text('лимит')
    expect(page.locator('#runTask')).to_be_disabled()
    page.locator('#runSelect').select_option(str(old_run['run_id']))
    expect(page.locator('#taskMeta')).to_contain_text('Отменён')
    assert current(page)['artifacts']==old_run['artifacts']


def test_stale_version_refreshes_without_repeating_step(browser_page):
    page,expect=browser_page
    create_task(page,expect,'Конкурентное изменение')
    new_run(page,expect)
    start(page,expect)
    changed=external_command(page,'step')
    page.locator('#stepTask').click()
    expect(page.locator('#status')).to_contain_text('Версия изменилась')
    expect(page.locator('#artifacts details')).to_have_count(1)
    assert current(page)['version']==changed['version']
    step(page,expect,2)


@pytest.mark.parametrize('failure',['network','server_error'])
def test_lost_response_replays_same_key_and_reads_latest_snapshot(browser_page,failure):
    page,expect=browser_page
    create_task(page,expect,'Потеря ответа')
    new_run(page,expect)
    start(page,expect)
    bodies=[]
    def lose_first_response(route):
        bodies.append(route.request.post_data_json)
        if len(bodies)==1:
            result=route.fetch()
            assert result.ok
            if failure=='network':
                route.abort('failed')
            else:
                route.fulfill(status=503,json={'detail':'Временная ошибка после commit'})
        else:
            route.continue_()
    page.route('**/taskflow/runs/*/commands',lose_first_response)
    page.locator('#stepTask').click()
    expect(page.locator('#status')).to_contain_text('Результат запроса неизвестен')
    expect(page.locator('#stepTask')).to_be_disabled()
    changed=external_command(page,'step')
    assert len(changed['artifacts'])==2
    page.locator('#retryRequest').click()
    expect(page.locator('#artifacts details')).to_have_count(2)
    expect(page.locator('#retryRequest')).to_be_hidden()
    assert bodies[0]==bodies[1]
    assert current(page)['version']==changed['version']


def test_failed_stage_resume_and_html_is_escaped(browser_page,monkeypatch):
    from backend.app.taskflow.director import MockDirector
    page,expect=browser_page
    create_task(page,expect,'<img src=x onerror="alert(1)">')
    new_run(page,expect,max_steps=4)
    start(page,expect)
    original=MockDirector.produce
    def fail(*args):
        raise RuntimeError('raw-secret-exception')
    monkeypatch.setattr(MockDirector,'produce',fail)
    page.locator('#stepTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Ошибка')
    expect(page.locator('#flowReason')).to_contain_text('Ошибка Mock Director')
    assert 'raw-secret-exception' not in page.content()
    monkeypatch.setattr(MockDirector,'produce',original)
    page.locator('#runTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Готов к следующему шагу')
    step(page,expect,1)
    assert page.locator('#artifacts img').count()==0
    assert '<img' in page.locator('#artifacts pre').text_content()
    run=current(page)
    page.reload()
    expect(page.locator('#status')).to_have_text('Подключено')
    page.locator('[data-task]').click()
    expect(page.locator('#artifacts details')).to_have_count(1)
    assert current(page)==run


def test_language_switch_preserves_task_artifacts_and_does_not_write(browser_page):
    """English UI is opt-in; switching never mutates stored tasks or invokes APIs."""
    page, expect = browser_page
    assert page.locator('html').get_attribute('lang') == 'ru'
    description = 'Пока нет задач. <тестовые данные>'
    create_task(page, expect, description)
    new_run(page, expect)
    start(page, expect)
    step(page, expect, 1)
    before = current(page)
    artifact_content = page.locator('#artifacts pre').first.text_content()
    writes = []
    page.on('request', lambda request: writes.append(request.url) if request.method not in ('GET', 'HEAD') else None)

    page.locator('#languageSelect').select_option('en')
    expect(page.locator('html')).to_have_attribute('lang', 'en')
    expect(page.locator('header h1')).to_have_text('Your AI team')
    expect(page.locator('#taskTitle')).to_have_text(description)
    expect(page.locator('#description')).to_have_text(description)
    expect(page.locator('#taskMeta')).to_contain_text('Ready for next step')
    expect(page.locator('#newRun')).to_have_text('New run')
    expect(page.locator('#artifacts details summary').first).to_contain_text('Analyst')
    expect(page.locator('#taskDetail > details > summary')).to_have_text('Legacy round history (separate from taskflow)')
    assert page.locator('#artifacts pre').first.text_content() == artifact_content
    assert current(page) == before

    page.locator('#languageSelect').select_option('ru')
    expect(page.locator('html')).to_have_attribute('lang', 'ru')
    expect(page.locator('header h1')).to_have_text('Твоя AI-команда')
    expect(page.locator('#taskMeta')).to_contain_text('Готов к следующему шагу')
    expect(page.locator('#taskDetail > details > summary')).to_have_text('История legacy-раундов (отдельно от taskflow)')
    assert current(page) == before
    assert not writes, 'Switching languages must not send mutations or model requests'


def test_mock_director_chat_shows_only_stored_evidence(browser_page):
    """Chat-looking bubbles are a read-only view, not fabricated Hermes/QA."""
    page, expect = browser_page
    expect(page.locator('#directorMessages .chat-bubble')).to_have_count(1)
    description = '<img src=x onerror=alert(1)> проверка безопасности'
    create_task(page, expect, description)
    expect(page.locator('#directorMessages .chat-bubble')).to_have_count(2)
    expect(page.locator('#directorMessages')).to_contain_text(description)
    assert page.locator('#directorMessages img').count() == 0
    expect(page.locator('#directorDisclaimer')).to_contain_text('Настоящий Hermes')

    new_run(page, expect)
    start(page, expect)
    step(page, expect, 1)
    record = current(page)
    assert len(record['artifacts']) == 1
    expect(page.locator('#directorMessages .chat-bubble')).to_have_count(3)
    expect(page.locator('#directorMessages')).to_contain_text(f"#{record['artifacts'][0]['artifact_id']}")
    expect(page.locator('#directorMessages')).to_contain_text('реальное программирование и QA не выполнялись')

    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#directorHeading')).to_have_text('Director activity · demo')
    expect(page.locator('#directorMessages')).to_contain_text('Saved status')
    expect(page.locator('#directorMessages')).to_contain_text(description)
    assert page.locator('#directorMessages img').count() == 0
    assert current(page) == record
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_visual_acceptance_ru_en_desktop_mobile(browser_page):
    """Reproducible local preview; responsive emulation, not a real-device claim."""
    from pathlib import Path
    page, expect = browser_page
    create_task(page, expect, 'Демонстрация: требования и план проверки приёмки товара')
    new_run(page, expect)
    start(page, expect)
    for count in range(1, 4):
        step(page, expect, count)
    before = current(page)
    assert before['simulated'] is True and before['completion_scope'] == 'mock_artifacts_only'
    writes = []
    page.on('request', lambda request: writes.append(request.url) if request.method not in ('GET', 'HEAD') else None)
    for locale in ('ru', 'en'):
        page.locator('#languageSelect').select_option(locale)
        expect(page.locator('html')).to_have_attribute('lang', locale)
        for size, width, height in [('desktop', 1440, 1050), ('mobile', 390, 844)]:
            page.set_viewport_size({'width': width, 'height': height})
            expect(page.locator('#approveTask')).to_be_enabled()
            expect(page.locator('#stepTask')).to_be_disabled()
            expect(page.locator('.notice')).to_contain_text('Mock Director')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.action-buttons button:not([hidden])').count() >= 5
            assert page.locator('.action-buttons button:not([hidden])').evaluate_all('buttons => buttons.every(b => { const r=b.getBoundingClientRect(); return r.width > 0 && r.left >= 0 && r.right <= innerWidth; })')
            if os.environ.get('PANEL_VISUAL_DIR'):
                directory = Path(os.environ['PANEL_VISUAL_DIR'])
                directory.mkdir(parents=True, exist_ok=True)
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(directory / f'panel-{locale}-{size}.png'), full_page=True)
    assert current(page) == before
    assert not writes, 'Locale/viewport previews must not mutate the saved run'


def test_workflow_toolbar_language_and_saved_progress(browser_page):
    """Controls form two aligned groups; progress is based on persisted mock states."""
    page, expect = browser_page
    process_ids = ('newRun', 'runTask', 'stepTask', 'pauseTask', 'stopTask')
    owner_ids = ('approveTask', 'rejectTask')
    page.set_viewport_size({'width': 1440, 'height': 1050})
    process_y = [page.locator('#' + key).bounding_box()['y'] for key in process_ids]
    owner_y = [page.locator('#' + key).bounding_box()['y'] for key in owner_ids]
    assert max(process_y) - min(process_y) < 2, process_y
    assert max(owner_y) - min(owner_y) < 2, owner_y
    assert owner_y[0] > process_y[0]
    expect(page.locator('label[for="languageSelect"]')).to_have_text('Язык')
    expect(page.locator('#languageSelect option[value="ru"]')).to_have_text('Русский')

    create_task(page, expect, 'Тест этапов без исполнения кода')
    new_run(page, expect)
    assert page.locator('#stageProgress li').count() == 6  # task + 3 actual roles + owner + planning
    expect(page.locator('#stageProgress li').first).to_have_attribute('data-state', 'completed')
    expect(page.locator('#stageProgress')).to_contain_text('Архитектор')
    expect(page.locator('#stageSummary')).to_contain_text('Только планирование')
    start(page, expect)
    expect(page.locator('#stageProgress')).to_contain_text('Выполняется')
    step(page, expect, 1)
    expect(page.locator('#stageProgress li').nth(1)).to_have_attribute('data-state', 'completed')
    expect(page.locator('#stageProgress li').nth(2)).to_have_attribute('data-state', 'active')
    step(page, expect, 2)
    step(page, expect, 3)
    expect(page.locator('#stageProgress li').nth(4)).to_have_attribute('data-state', 'waiting')
    expect(page.locator('#stageProgress li').nth(5)).to_have_attribute('data-state', 'pending')

    saved = current(page)
    writes = []
    page.on('request', lambda request: writes.append(request.url) if request.method not in ('GET', 'HEAD') else None)
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('label[for="languageSelect"]')).to_have_text('Language')
    expect(page.locator('#languageSelect option[value="ru"]')).to_have_text('Russian')
    expect(page.locator('#languageSelect option[value="en"]')).to_have_text('English')
    expect(page.locator('#languageSelect')).to_have_attribute('aria-label', 'Interface language')
    expect(page.locator('#stageHeading')).to_have_text('Workflow stages')
    expect(page.locator('#stageProgress')).to_contain_text('Awaiting owner')
    expect(page.locator('#stageSummary')).to_contain_text('Planning only')
    page.locator('#diagnosticDetails').evaluate('(element) => element.open = true')
    expect(page.locator('#processDiagnostics')).to_contain_text('Last saved action')
    assert current(page) == saved
    page.locator('#languageSelect').select_option('ru')
    expect(page.locator('label[for="languageSelect"]')).to_have_text('Язык')
    expect(page.locator('#languageSelect option[value="ru"]')).to_have_text('Русский')
    assert not writes

    page.locator('#approveTask').click()
    expect(page.locator('#stageProgress li').nth(4)).to_have_attribute('data-state', 'completed')
    expect(page.locator('#stageProgress li').nth(5)).to_have_attribute('data-state', 'completed')
    expect(page.locator('#stageSummary')).to_contain_text('код и QA не проверены')
    page.reload()
    page.locator('[data-task]').first.click()
    expect(page.locator('#stageProgress li').nth(5)).to_have_attribute('data-state', 'completed')

    for width in (1280, 1024, 768, 390, 320):
        page.set_viewport_size({'width': width, 'height': 850})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        for key in (*process_ids, *owner_ids):
            assert page.locator('#' + key).bounding_box()['width'] > 0


def test_workflow_progress_blocked_cancelled_and_unverified_qa(browser_page):
    page, expect = browser_page
    create_task(page, expect, 'Тест заблокированной роли')
    new_run(page, expect, max_steps=1)
    start(page, expect)
    step(page, expect, 1)
    expect(page.locator('#stageProgress')).to_contain_text('Ошибка / блокировка')
    assert page.locator('#stageProgress li[data-state="error"]').count() >= 1
    expect(page.locator('#stageSummary')).to_contain_text('Только планирование')
    page.locator('#stopTask').click()
    expect(page.locator('#stageProgress li[data-state="error"]')).not_to_have_count(0)
    expect(page.locator('#processDiagnostics')).to_contain_text('Отменён')
    expect(page.locator('#stageProgress')).not_to_contain_text('QA passed')
