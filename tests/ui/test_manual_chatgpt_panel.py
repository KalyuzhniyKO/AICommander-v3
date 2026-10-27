"""Our local panel only: never automate ChatGPT web, cookies or a model picker."""
import os
import re
from pathlib import Path

import pytest

pytestmark=pytest.mark.skipif(os.environ.get('RUN_UI_TESTS')!='1',reason='Chromium opt-in')

DOC=b'<h1>GPT-9 and other models in ChatGPT</h1><table><tr><td>Plus</td><td>Included</td><td>Not included</td><td>Not included</td></tr></table>'


@pytest.fixture
def manual_page(monkeypatch,browser_page):
    from backend.app.manual_chatgpt import source
    monkeypatch.setattr(source,'fetch_public',lambda url:(DOC,url))
    page,expect=browser_page
    page.locator('#taskText').fill('Synthetic manual expert task')
    page.locator('#taskForm button').click()
    expect(page.locator('#manualConfirmSelection')).to_be_enabled()
    return page,expect


def capture(page,name):
    folder=os.environ.get('MANUAL_SCREENSHOTS')
    if folder:
        Path(folder).mkdir(parents=True,exist_ok=True)
        page.locator('#manualChatGPT').screenshot(path=str(Path(folder)/name))


def test_latest_visible_high_manual_handoff_does_not_call_models(manual_page):
    page,expect=manual_page
    writes=[]
    page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
    expect(page.locator('#manualEffort')).to_have_value('High')
    expect(page.locator('#manualChosen')).to_contain_text('не подтверждён')
    page.locator('#manualCheckSource').click()
    expect(page.locator('#manualSourceState')).to_contain_text('Публичный источник проверен')
    expect(page.locator('#manualCandidate')).to_contain_text('GPT-9')
    expect(page.locator('#manualChosen')).to_contain_text('не подтверждён')
    page.locator('#manualSelectStep > summary').click()
    page.locator('#manualPlan').select_option('plus')
    expect(page.locator('#manualPlanInfo')).to_contain_text('High — включён по публичным данным')
    expect(page.locator('#manualPlanInfo')).to_contain_text('Pro — не включён по публичным данным')
    assert page.locator('#manualEffort option[value="Pro"]').is_disabled()
    assert page.locator('#manualEffort option[value="Extra High"]').is_disabled()
    # New announcement is not in the owner account: record its actual older choice.
    page.locator('#manualModel').fill('GPT-8')
    page.locator('#manualHigh').check()
    page.locator('#manualPicker').check()
    page.locator('#manualConfirmSelection').click()
    expect(page.locator('#manualChosen')).to_contain_text('GPT-8 · High')
    page.locator('#manualContextStep > summary').click()
    page.locator('#manualPrepare').click()
    expect(page.locator('#manualContext')).to_have_value('Synthetic manual expert task')
    page.locator('#manualContext').fill('Owner-reviewed synthetic context')
    page.locator('#manualSanitized').check()
    page.locator('#manualExport').click()
    expect(page.locator('#manualPrompt')).to_have_value(re.compile('Owner-reviewed synthetic context'))
    assert page.locator('#manualOpen').get_attribute('href')=='https://chatgpt.com/'
    assert page.locator('#manualOpen').get_attribute('rel')=='noopener noreferrer'
    # Inspect the link only; no ChatGPT browser or session is opened by the test.
    page.locator('#manualAnswerStep > summary').click()
    page.locator('#manualAnswer').fill('<img src=x onerror="window.manualXss=true"> imported manual note')
    page.locator('#manualOrigin').check()
    page.locator('#manualImport').click()
    expect(page.locator('#manualHistory')).to_contain_text('imported manual note')
    expect(page.locator('#manualHistory img')).to_have_count(0)
    assert page.evaluate('window.manualXss') is None
    assert all('/manual-chatgpt/' in url for url in writes)
    finance=page.request.get('/spending/tasks/1').json()
    assert all(a['dispatches_claimed']==0 and not a['limits']['allow_paid'] for a in finance['accounts'])
    capture(page,'manual-ru.png')
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#manualHeading')).to_have_text('Manual ChatGPT expertise')
    expect(page.locator('#manualChosen')).to_contain_text('Owner reported: GPT-8 · High')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    capture(page,'manual-mobile-en.png')


def test_public_network_failure_is_unconfirmed_and_task_switch_clears_context(manual_page,monkeypatch):
    from backend.app.manual_chatgpt import source
    page,expect=manual_page
    page.locator('#manualCheckSource').click()
    expect(page.locator('#manualSourceState')).to_contain_text('Публичный источник проверен')
    def fail(url):raise source.SourceError('network unavailable')
    monkeypatch.setattr(source,'fetch_public',fail)
    page.locator('#manualCheckSource').click()
    expect(page.locator('#manualSourceState')).to_contain_text('не подтверждена')
    expect(page.locator('#manualCandidate')).to_contain_text('Сохранённое объявление')
    page.locator('#manualContextStep > summary').click()
    page.locator('#manualContext').fill('Old task context')
    page.locator('#projectSelect').dispatch_event('change')
    expect(page.locator('#manualContext')).to_have_value('')
    expect(page.locator('#manualConfirmSelection')).to_be_disabled()
