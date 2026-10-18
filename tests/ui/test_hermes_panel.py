"""Real HTTP/SQLite UI tests with synthetic v2 artifacts, no Hermes/model calls."""
from contextlib import closing
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_UI_TESTS') != '1', reason='Opt-in Chromium tests')


def seed_run(*, task_id=None, operation_state=None, failed=False, include_architect=True):
    from backend.app import main
    from backend.app.storage.db import connect, init_db
    from backend.app.storage.repositories import Repository
    from backend.app.taskflow.models import HermesCreateRun, Command, Requirements, Design, RealPlan, PlanReview
    from backend.app.taskflow.ports import ProducedArtifact
    from backend.app.taskflow.service import Taskflow

    class FixtureDirector:
        binding='synthetic-ui-only'
        def produce_with_context(self,role,goal,inputs,context):
            previous={a.role:a.content for a in inputs}
            if role=='analyst':content=Requirements(objective=goal,acceptance_criteria=['ТЕСТ: положительное количество <img src=x onerror=alert(1)>'])
            elif role=='architect':content=Design(requirements=previous['analyst'],components=['Тестовая валидация'])
            elif role=='developer':content=RealPlan(requirements=previous['analyst'],design=previous.get('architect'),steps=['ТЕСТ: предложить проверку количества'])
            else:content=PlanReview(plan=previous['developer'],verdict='revise',findings=['ТЕСТ: уточнить границы; код и тесты не выполнялись'])
            return ProducedArtifact(content.model_dump(),{'operation_id':context.operation_id,'model_id':'fixture/model',
                'sdk_commit':'f'*40,'parent_session_id':'fixture-parent','child_session_id':'fixture-child',
                'input_sha256':'0'*64})

    with closing(connect(main.settings.database_path)) as conn:
        init_db(conn)
        if task_id is None:task_id=Repository(conn).create_task('ТЕСТОВЫЕ ДАННЫЕ: история Hermes без вызова модели')['id']
        flow=Taskflow(conn,FixtureDirector(),mode='hermes')
        run=flow.create(task_id,HermesCreateRun(idempotency_key='fixture-create',include_architect=include_architect,max_steps=4 if include_architect else 3))
        run=flow.apply(run.run_id,Command(idempotency_key='fixture-start',expected_version=1,action='start'))
        if failed:
            run=flow.apply(run.run_id,Command(idempotency_key='fixture-fail',expected_version=run.version,action='fail',reason='ТЕСТ: ошибка роли, результат не принят'))
        elif operation_state:
            conn.execute("""INSERT INTO taskflow_operations(id,run_id,key_hash,request_hash,expected_version,role,goal,input_json,reason,state,worker_hash)
                VALUES('fixture-operation',?,'fixture-key','fixture-request',?,'analyst','synthetic',?,'',?,'fixture-worker')""",
                (run.run_id,run.version,run.model_dump_json(),operation_state))
            conn.commit()
        else:
            for index in range(4 if include_architect else 3):run=flow.apply(run.run_id,Command(idempotency_key='fixture-step-'+str(index),expected_version=run.version,action='step'))
        return run.model_dump(mode='json')


def open_hermes(page,expect):
    page.locator('#refreshTasks').click()
    page.locator('[data-task]').first.click()
    page.locator('#flowMode').select_option('hermes')
    expect(page.locator('.notice')).to_contain_text('Hermes')


def test_saved_hermes_roles_provenance_language_and_mobile_without_model_commands(browser_page):
    page,expect=browser_page
    saved=seed_run()
    # Both contracts have history for the same task; never mix their artifacts.
    path='/taskflow/tasks/'+str(saved['task_id'])+'/runs'
    response=page.request.post(path,headers={'X-AICommander-Request':'1'},data={'idempotency_key':'fixture-mock'})
    mock=response.json()
    for action in ('start','step'):
        mock=page.request.post('/taskflow/runs/'+str(mock['run_id'])+'/commands',headers={'X-AICommander-Request':'1'},data={'idempotency_key':'fixture-'+action,'expected_version':mock['version'],'action':action}).json()
    writes=[]
    page.on('request',lambda r:writes.append((r.method,r.url)) if r.method not in ('GET','HEAD') else None)
    open_hermes(page,expect)
    expect(page.locator('#artifacts > details')).to_have_count(4)
    expect(page.locator('#roleBoard')).to_contain_text('Ревьюер плана')
    expect(page.locator('#artifacts')).to_contain_text('fixture/model')
    expect(page.locator('#artifacts')).to_contain_text('ТЕСТ: уточнить границы')
    assert page.locator('#artifacts img').count()==0
    page.locator('#artifacts > details').first.evaluate('(element) => element.open = true')
    content=page.locator('#artifacts pre').all_text_contents()
    for language in ('en','ru'):
        page.locator('#languageSelect').select_option(language)
        page.locator('#artifacts > details').first.evaluate('(element) => element.open = true')
        expect(page.locator('#artifactsHeading')).to_contain_text('Hermes')
        expect(page.locator('#directorHeading')).to_contain_text('Hermes')
        expect(page.locator('.notice')).not_to_contain_text('Mock Director')
        for button in ('newRun','runTask','stepTask','pauseTask','approveTask','rejectTask'):
            expect(page.locator('#'+button)).to_be_disabled()
            expect(page.locator('#'+button)).to_be_hidden()
        for size,width,height in (('desktop',1440,1050),('mobile',390,844)):
            page.set_viewport_size({'width':width,'height':height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('#artifacts pre').all_text_contents()==content
            if os.environ.get('HERMES_PANEL_VISUAL_DIR'):
                folder=Path(os.environ['HERMES_PANEL_VISUAL_DIR']);folder.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(folder/f'hermes-{language}-{size}.png'),full_page=True)
    assert not writes
    response=page.request.get('/taskflow/v2/runs/'+str(saved['run_id']))
    assert response.json()==saved
    # Switching back restores only the mock run of the same task.
    page.locator('#flowMode').select_option('mock')
    expect(page.locator('#artifacts > details')).to_have_count(1)
    expect(page.locator('#runSelect')).to_have_value(str(mock['run_id']))
    expect(page.locator('.notice')).to_contain_text('Mock Director')
    page.locator('#flowMode').select_option('hermes')
    expect(page.locator('#artifacts > details')).to_have_count(4)
    assert not writes


@pytest.mark.parametrize('state',['running','result_ready','unknown'])
def test_operation_history_refresh_and_cancel_only(browser_page,state):
    page,expect=browser_page
    saved=seed_run(operation_state=state)
    open_hermes(page,expect)
    expected={'running':'Выполняется','result_ready':'ожидает фиксации','unknown':'Исход неизвестен'}[state]
    expect(page.locator('#operations')).to_contain_text(expected)
    requests=[]
    page.on('request',lambda r:requests.append(r.post_data_json) if r.method=='POST' else None)
    page.locator('#refreshTasks').click()
    expect(page.locator('#operations')).to_contain_text(expected)
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#operationsHeading')).to_have_text('Hermes operations')
    if state=='unknown':
        expect(page.locator('#operationHint')).to_contain_text('Automatic retry is blocked')
        if os.environ.get('HERMES_PANEL_VISUAL_DIR'):
            folder=Path(os.environ['HERMES_PANEL_VISUAL_DIR']);folder.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(folder/'hermes-unknown-en-desktop.png'),full_page=True)
    assert not requests
    page.locator('#stopTask').click()
    expect(page.locator('#taskMeta')).to_contain_text('Cancelled')
    expect(page.locator('#stopTask')).to_be_disabled()
    assert len(requests)==1 and requests[0]['action']=='cancel'
    assert not page.request.get('/taskflow/v2/runs/'+str(saved['run_id'])).json()['artifacts']
    operations=page.request.get('/taskflow/v2/runs/'+str(saved['run_id'])+'/operations').json()
    assert operations[0]['cancel_requested'] is True


def test_hermes_cancel_lost_response_replays_exact_cancel_and_locks_mode(browser_page):
    page,expect=browser_page
    saved=seed_run(operation_state='unknown')
    open_hermes(page,expect)
    bodies=[]
    def lose(route):
        bodies.append(route.request.post_data_json)
        if len(bodies)==1:
            assert route.fetch().ok
            route.abort('failed')
        else:route.continue_()
    page.route('**/taskflow/v2/runs/*/commands',lose)
    page.locator('#stopTask').click()
    expect(page.locator('#status')).to_contain_text('Результат запроса неизвестен')
    expect(page.locator('#flowMode')).to_be_disabled()
    page.locator('#retryRequest').click()
    expect(page.locator('#taskMeta')).to_contain_text('Отменён')
    assert len(bodies)==2 and bodies[0]==bodies[1] and bodies[0]['action']=='cancel'
    expect(page.locator('#flowMode')).to_be_enabled()
    response=page.request.get('/taskflow/v2/runs/'+str(saved['run_id'])).json()
    assert response['version']==saved['version']+1


def test_operation_fetch_failure_is_not_reported_as_empty_history(browser_page):
    page,expect=browser_page
    seed_run(operation_state='unknown')
    page.route('**/taskflow/v2/runs/*/operations',lambda route:route.fulfill(status=503,json={'detail':'fixture unavailable'}))
    open_hermes(page,expect)
    expect(page.locator('#operationHint')).to_contain_text('Статус операций недоступен')
    expect(page.locator('#operations')).to_contain_text('Не удалось прочитать операции')
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#operationHint')).to_contain_text('Operation status unavailable')
    expect(page.locator('#operations')).to_contain_text('Could not read operations')
    expect(page.locator('#stepTask')).to_be_disabled()


def test_saved_role_failure_does_not_offer_model_retry(browser_page):
    page,expect=browser_page
    seed_run(failed=True)
    open_hermes(page,expect)
    expect(page.locator('#flowReason')).to_have_text('ТЕСТ: ошибка роли, результат не принят')
    expect(page.locator('#runTask')).to_be_disabled()
    expect(page.locator('#stepTask')).to_be_disabled()
    expect(page.locator('#retryRequest')).to_be_hidden()
    expect(page.locator('#stopTask')).to_be_enabled()


def test_empty_or_unavailable_v2_history_never_falls_back_to_mock(browser_page):
    page,expect=browser_page
    seed_run()
    page.route('**/taskflow/v2/tasks/*/runs',lambda route:route.fulfill(json=[]))
    open_hermes(page,expect)
    expect(page.locator('#directorMessages')).to_contain_text('нет сохранённых процессов Hermes')
    expect(page.locator('#artifacts > details')).to_have_count(0)
    expect(page.locator('#newRun')).to_be_disabled()
    page.unroute('**/taskflow/v2/tasks/*/runs')
    page.locator('#refreshTasks').click()
    expect(page.locator('#artifacts > details')).to_have_count(4)
    page.locator('#flowMode').select_option('mock')
    expect(page.locator('#artifacts > details')).to_have_count(0)
    page.route('**/taskflow/v2/tasks/*/runs',lambda route:route.fulfill(status=503,json={'detail':'fixture unavailable'}))
    page.locator('#flowMode').select_option('hermes')
    expect(page.locator('#status')).to_have_text('fixture unavailable')
    expect(page.locator('#artifacts > details')).to_have_count(0)
    expect(page.locator('#stepTask')).to_be_disabled()


def test_hermes_progress_uses_only_saved_roles_and_unknown_operations(browser_page):
    """Hermes Reviewer is planning-only; an unknown external operation is never green."""
    page, expect = browser_page
    saved = seed_run(include_architect=False)
    open_hermes(page, expect)
    expect(page.locator('#stageProgress')).to_contain_text('Ревьюер плана')
    expect(page.locator('#stageProgress')).not_to_contain_text('Архитектор')
    assert page.locator('#stageProgress li').count() == 6
    expect(page.locator('#stageProgress li').nth(4)).to_have_attribute('data-state', 'waiting')
    expect(page.locator('#stageSummary')).to_contain_text('код и QA не проверены')
    expect(page.locator('#approveTask')).to_be_hidden()
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#stageProgress')).to_contain_text('Plan reviewer')
    expect(page.locator('#stageSummary')).to_contain_text('code and QA not verified')
    assert page.request.get('/taskflow/v2/runs/' + str(saved['run_id'])).json() == saved

    unknown = seed_run(operation_state='unknown')
    page.locator('#refreshTasks').click()
    page.locator(f'[data-task="{unknown["task_id"]}"]').click()
    expect(page.locator('#stageProgress')).to_contain_text('Outcome unknown')
    assert page.locator('#stageProgress li[data-state="unknown"]').count() >= 1
    expect(page.locator('#stageSummary')).to_contain_text('do not retry automatically')
    expect(page.locator('#processDiagnostics')).to_contain_text('Unknown outcome')
    expect(page.locator('#stageProgress li').last).not_to_have_attribute('data-state', 'completed')
    assert page.request.get('/taskflow/v2/runs/' + str(unknown['run_id'])).json() == unknown
