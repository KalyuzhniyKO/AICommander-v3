"""Real Chromium/HTTP; saved synthetic reports, no models or Zorin."""
import hashlib
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_UI_TESTS') != '1', reason='Chromium UI opt-in')


@pytest.fixture
def review_page(saved_review_store, browser_page):
    page, expect = browser_page
    expect(page.locator('#reviewSelect')).to_be_enabled()
    return page, expect, saved_review_store


def screenshot(page, name):
    folder = os.environ.get('REVIEW_SCREENSHOTS')
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.locator('#localReviews').screenshot(path=str(Path(folder)/name))


def test_passing_failing_language_and_mobile(review_page):
    page, expect, (root, bundles) = review_page
    requests = []
    page.on('request', lambda request: requests.append((request.method, request.url)))
    page.locator('#reviewSelect').select_option(bundles['passing'].name)
    expect(page.locator('#reviewStatus')).to_have_text('Целостность пакета проверена.')
    expect(page.locator('#reviewDetail')).to_contain_text('QA: успешно в сохранённом прогоне')
    expect(page.locator('#reviewDetail')).to_contain_text('Актуальность исходников сейчас не проверена')
    expect(page.locator('#reviewDetail summary')).to_have_count(7)
    page.locator('#reviewDetail details').first.locator('summary').click()
    expect(page.locator('#reviewDetail details').first).to_contain_text('passed')
    screenshot(page, 'passing-ru.png')
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#reviewsHeading')).to_have_text('Saved QA bundles')
    expect(page.locator('#reviewDetail')).to_contain_text('Independent review and owner approval are required')
    page.locator('#reviewSelect').select_option(bundles['failing'].name)
    expect(page.locator('#reviewDetail')).to_contain_text('QA: not passed')
    page.locator('#reviewDetail details').first.locator('summary').click()
    expect(page.locator('#reviewDetail details').first).to_contain_text('FAILED')
    screenshot(page, 'failing-en.png')
    page.set_viewport_size({'width':390, 'height':844})
    screenshot(page, 'failing-mobile.png')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert all(method == 'GET' for method, url in requests)
    assert not any('/models/check' in url or '/commands' in url for _, url in requests)
    expect(page.locator('#taskTitle')).to_have_text('Choose a task')


def test_corruption_and_index_error_clear_old_success(review_page):
    page, expect, (root, bundles) = review_page
    page.locator('#reviewSelect').select_option(bundles['passing'].name)
    expect(page.locator('#reviewStatus')).to_have_text('Целостность пакета проверена.')
    (bundles['failing']/'changes.diff').write_text('CORRUPTED TEXT', encoding='utf-8')
    page.locator('#reviewSelect').select_option(bundles['failing'].name)
    expect(page.locator('#reviewStatus')).to_contain_text('проверка целостности не пройдена')
    expect(page.locator('#reviewDetail')).to_be_empty()
    screenshot(page, 'integrity-error.png')
    page.route('**/local-reviews', lambda route: route.fulfill(status=503, json={'detail':'Unavailable'}))
    page.locator('#refreshReviews').click()
    expect(page.locator('#reviewStatus')).to_contain_text('Список пакетов недоступен')
    expect(page.locator('#reviewDetail')).to_be_empty()
    expect(page.locator('#reviewSelect')).to_be_disabled()


def test_refresh_invalidates_late_detail(review_page):
    page, expect, (root, bundles) = review_page
    # Capture a pending detail; the refresh must discard it even if it later completes.
    captured = []
    page.route('**/local-reviews/*', lambda route: captured.append(route))
    page.locator('#reviewSelect').select_option(bundles['passing'].name)
    expect(page.locator('#reviewStatus')).to_have_text('Загрузка…')
    page.locator('#refreshReviews').click()
    expect(page.locator('#reviewStatus')).to_contain_text('Выберите пакет')
    assert captured
    try:
        captured[0].continue_()
    except Exception as error:
        assert 'cancel' in str(error).lower() or 'closed' in str(error).lower()
    expect(page.locator('#reviewDetail')).to_be_empty()


def test_verified_content_is_plain_text(review_page):
    page, expect, (root, bundles) = review_page
    path = bundles['passing']
    content = '<img src=x onerror="window.reviewXss=true"><script>window.reviewXss=true</script> Привет'
    file = path/'requirements.md'
    file.write_text(content, encoding='utf-8')
    manifest = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    manifest['files'][file.name] = {'bytes': len(file.read_bytes()), 'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    (path/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    page.locator('#reviewSelect').select_option(path.name)
    expect(page.locator('#reviewStatus')).to_have_text('Целостность пакета проверена.')
    requirements = page.locator('#reviewDetail details').nth(3)
    requirements.locator('summary').click()
    expect(requirements.locator('pre')).to_have_text(content)
    expect(page.locator('#reviewDetail img, #reviewDetail script')).to_have_count(0)
    assert page.evaluate('window.reviewXss') is None
    expect(page.locator('#reviewDetail')).to_contain_text('Хеши не подтверждают автора')


def test_disabled_viewer_has_no_fixture_fallback(browser_page):
    page, expect = browser_page
    expect(page.locator('#reviewStatus')).to_contain_text('Просмотр выключен')
    expect(page.locator('#reviewSelect')).to_be_disabled()
    expect(page.locator('#reviewDetail')).to_be_empty()
    page.locator('#languageSelect').select_option('en')
    expect(page.locator('#reviewStatus')).to_contain_text('Viewer disabled')
