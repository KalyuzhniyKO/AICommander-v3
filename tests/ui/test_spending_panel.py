"""Actual owner UI / budget API with synthetic, explicitly trusted tariff fixtures."""
from contextlib import closing
import io
import json
import os
from pathlib import Path

import pytest

pytestmark=pytest.mark.skipif(os.environ.get('RUN_UI_TESTS')!='1',reason='Chromium opt-in')


@pytest.fixture
def spending_page(app_spend_policy,browser_page):
    page,expect=browser_page
    page.locator('#taskText').fill('Синтетическая задача расходов')
    page.locator('#taskForm button').click()
    expect(page.locator('#spendContext')).to_have_text('Бюджеты для задачи #1')
    expect(page.locator('#spendApprovalForm')).to_be_visible()
    expect(page.locator('#spendAmount')).to_be_enabled()
    return page,expect


def capture(page,name):
    folder=os.environ.get('SPENDING_SCREENSHOTS')
    if folder:
        Path(folder).mkdir(parents=True,exist_ok=True)
        page.locator('#spendingPanel').screenshot(path=str(Path(folder)/name))


def approve(page,expect,scope='task'):
    page.locator('#spendScope').select_option(scope)
    page.locator('#spendRoute').select_option('openrouter/fixture/paid')
    page.locator('#spendAmount').fill('0.001')
    page.locator('#spendConfirm').check()
    page.locator('#approveBudget').click()
    expect(page.locator('#spendStatus')).to_contain_text('Лимит записан')
    expect(page.locator('#spendAmount')).to_be_enabled()


def test_default_unknown_has_no_false_subscription_balance(browser_page):
    page,expect=browser_page
    expect(page.locator('#spendStatus')).to_contain_text('Стоимость неизвестна')
    expect(page.locator('#spendApprovalForm')).to_be_hidden()
    expect(page.locator('#spendAccounts')).to_contain_text('Подтверждённая стоимость API: Неизвестно')
    expect(page.locator('#spendQuota')).to_contain_text('Остаток подписки ChatGPT/Codex: неизвестно')
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#spendStatus')).to_contain_text('Cost unknown')
    expect(page.locator('#spendQuota')).to_contain_text('subscription quota: unknown')
    capture(page,'default-unknown.png')


def test_estimate_owner_grants_and_real_runtime_accounting(spending_page):
    page,expect=spending_page
    posts=[]
    page.on('request',lambda request:posts.append(request.url) if request.method=='POST' else None)
    expect(page.locator('#spendEstimates')).to_contain_text('Верхняя оценка: $0.000111')
    page.locator('#spendRoute').select_option('openrouter/fixture/paid')
    page.locator('#spendAmount').fill('0.001')
    page.locator('#approveBudget').click()
    expect(page.locator('#spendStatus')).to_contain_text('Укажите сумму, маршрут')
    assert not posts
    for scope in ('global','project','task'):approve(page,expect,scope)
    expect(page.locator('#spendStatus')).to_contain_text('В утверждённом бюджете')
    assert len(posts)==3 and all(url.endswith('/spending/approvals') for url in posts)
    from backend.app import main
    from backend.app.storage.db import connect
    from backend.app.budgets.runtime import RuntimeSpend,ProviderBudget
    from backend.app.providers.openrouter import OpenRouterProvider
    from backend.app.providers.base import ChatMessage
    class SyntheticOpener:
        def open(self,request,timeout):
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'synthetic result'}}],
                'usage':{'prompt_tokens':12,'completion_tokens':2,'cost':'0.000015'}}).encode())
    with closing(connect(main.settings.database_path)) as conn:
        runtime=RuntimeSpend(conn,main.settings)
        provider=OpenRouterProvider('synthetic-not-real-key','https://models.invalid/v1',opener=SyntheticOpener(),
            budget=ProviderBudget(runtime,task_id=1,origin='legacy',role='manager'))
        provider.chat('fixture/paid',[ChatMessage('user','synthetic fixture')],1)
    page.locator('#refreshSpending').click()
    expect(page.locator('#spendAccounts')).to_contain_text('Подтверждённая стоимость API: $0.000015')
    expect(page.locator('#spendAccounts')).to_contain_text('Подтверждённые токены: вход / выход: 12 / 2')
    expect(page.locator('#spendHistory')).to_contain_text('owner')
    capture(page,'budget-ru.png')
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#spendHeading')).to_have_text('Budget and spending')
    expect(page.locator('#spendStatus')).to_contain_text('Within approved budget')
    expect(page.locator('#spendAccounts')).to_contain_text('Confirmed API cost: $0.000015')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    capture(page,'budget-mobile-en.png')


def test_lost_approval_response_replays_same_permission_not_new_budget(spending_page):
    page,expect=spending_page
    bodies=[]
    state={'lost':False,'failed_read':False}
    def approval_route(route):
        bodies.append(route.request.post_data_json)
        if not state['lost']:
            result=route.fetch()
            assert result.ok
            state['lost']=True
            route.abort()
        else:route.continue_()
    def read_route(route):
        if state['lost'] and not state['failed_read']:
            state['failed_read']=True
            route.fulfill(status=503,json={'detail':'temporary fixture failure'})
        else:route.continue_()
    page.route('**/spending/approvals',approval_route)
    page.route('**/spending/tasks/*',read_route)
    page.locator('#spendRoute').select_option('openrouter/fixture/paid')
    page.locator('#spendAmount').fill('0.001')
    page.locator('#spendConfirm').check()
    page.locator('#approveBudget').click()
    expect(page.locator('#retryBudget')).to_be_enabled()
    page.locator('#retryBudget').click()
    expect(page.locator('#retryBudget')).to_be_hidden()
    expect(page.locator('#spendStatus')).to_contain_text('Лимит записан')
    assert len(bodies)==2 and bodies[0]==bodies[1]
    result=page.request.get('/spending/tasks/1').json()
    assert len(result['approvals'])==1 and result['approvals'][0]['revision']==1
    assert all(a['dispatches_claimed']==0 for a in result['accounts'])


def test_unsafe_history_text_is_literal_and_error_clears_approval_state(spending_page):
    page,expect=spending_page
    from backend.app import main
    from backend.app.storage.db import connect
    from backend.app.budgets.runtime import RuntimeSpend
    with closing(connect(main.settings.database_path)) as conn:
        RuntimeSpend(conn,main.settings).event(1,'<img src=x onerror="window.spendXss=true">','<script>window.spendXss=true</script>','pricing_unknown')
    page.locator('#refreshSpending').click()
    expect(page.locator('#spendHistory')).to_contain_text('<img src=x')
    expect(page.locator('#spendHistory img, #spendHistory script')).to_have_count(0)
    assert page.evaluate('window.spendXss') is None
    page.route('**/spending/tasks/*',lambda route:route.fulfill(status=503,json={'detail':'failure'}))
    page.locator('#refreshSpending').click()
    expect(page.locator('#spendStatus')).to_contain_text('Расходы недоступны')
    expect(page.locator('#spendAccounts')).to_be_empty()
    expect(page.locator('#spendApprovalForm')).to_be_hidden()
