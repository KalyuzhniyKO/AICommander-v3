"""Authenticated read-only access to exact verified saved bytes, no execution."""
from dataclasses import replace
import hashlib
import json
import os

import pytest

from backend.app import main
from backend.app.localrun import verify_bundle
from backend.app.localrun.workflow import read_bundle


def test_disabled_default_and_authentication(client):
    assert client.get('/local-reviews').json() == {'enabled': False, 'runs': []}
    assert client.get('/local-reviews/' + 'a'*32).status_code == 404
    assert client.get('/local-reviews', headers={'Authorization': ''}).status_code == 401
    assert client.get('/local-reviews', headers={'Host': 'evil.invalid'}).status_code == 400


def test_index_and_exact_verified_content(client, saved_review_store):
    root, bundles = saved_review_store
    index = client.get('/local-reviews').json()
    assert index == {'enabled': True, 'runs': sorted([{'run_id': p.name} for p in bundles.values()], key=lambda item:item['run_id'])}
    for case, path in bundles.items():
        response = client.get('/local-reviews/' + path.name)
        assert response.status_code == 200
        data = response.json()
        assert data['integrity_valid'] is True
        assert all(data[k] is False for k in ('authenticated', 'current_source_verified', 'ready_for_pr', 'ready_for_merge'))
        assert data['manifest']['qa_passed'] is (case == 'passing')
        assert data['manifest']['independent_review'] == data['manifest']['owner_approval'] == 'required'
        for name, content in data['artifacts'].items():
            assert content == (path / name).read_bytes().decode('utf-8')
        assert data['qa_report'] == json.loads(data['artifacts']['qa-report.json'])
        assert str(root) not in response.text
    assert client.get('/tasks').json() == []


@pytest.mark.parametrize('mutation', ['content', 'missing', 'extra', 'manifest', 'identity', 'invalid_utf8'])
def test_invalid_bundle_never_exposes_unverified_text(client, saved_review_store, mutation):
    root, bundles = saved_review_store
    path = bundles['passing']
    if mutation == 'content':
        (path / 'requirements.md').write_text('UNVERIFIED PAYLOAD', encoding='utf-8')
    elif mutation == 'missing':
        (path / 'changes.diff').unlink()
    elif mutation == 'extra':
        (path / 'unapproved.txt').write_text('UNVERIFIED PAYLOAD', encoding='utf-8')
    elif mutation == 'manifest':
        (path / 'manifest.json').write_text('{}', encoding='utf-8')
    elif mutation == 'identity':
        new = root / ('a'*32)
        path.rename(new)
        path = new
    else:
        (path / 'requirements.md').write_bytes(b'\xff')
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        manifest['files']['requirements.md'] = {'bytes': 1, 'sha256': hashlib.sha256(b'\xff').hexdigest()}
        (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    response = client.get('/local-reviews/' + path.name)
    assert response.status_code == 409
    assert 'UNVERIFIED' not in response.text and str(root) not in response.text
    assert 'artifacts' not in response.json()


@pytest.mark.parametrize('identifier', ['manifest.json', 'passing', 'A'*32, 'a'*31, 'a'*33, '%2e%2e%5cmanifest.json'])
def test_no_browser_paths(client, saved_review_store, identifier):
    assert client.get('/local-reviews/' + identifier).status_code == 404


def test_absolute_existing_root_required(client, monkeypatch, tmp_path):
    for value in ('relative/path', str(tmp_path/'absent')):
        monkeypatch.setattr(main, 'settings', replace(main.settings, local_review_root=value))
        response = client.get('/local-reviews')
        assert response.status_code == 503
        assert value not in response.text


def test_index_bounded_nonrecursive_and_read_only(client, saved_review_store):
    root, bundles = saved_review_store
    nested = root/'unrelated'
    nested.mkdir()
    (nested/('a'*32)).mkdir()
    before = {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert len(client.get('/local-reviews').json()['runs']) == 2
    assert client.post('/local-reviews', json={}).status_code == 405
    assert client.post('/local-reviews/'+bundles['passing'].name, json={'action':'approve'}).status_code == 405
    after = {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert before == after
    for n in range(98):
        (root/f'ignored-{n}').touch()
    assert client.get('/local-reviews').status_code == 503


@pytest.mark.skipif(os.name == 'nt', reason='POSIX symlink privileges')
@pytest.mark.parametrize('target', ['root', 'directory', 'file', 'hardlink'])
def test_links_rejected(client, saved_review_store, monkeypatch, target):
    root, bundles = saved_review_store
    path = bundles['passing']
    if target == 'root':
        alias = root.parent/'alias'
        alias.symlink_to(root, target_is_directory=True)
        monkeypatch.setattr(main, 'settings', replace(main.settings, local_review_root=str(alias)))
        assert client.get('/local-reviews').status_code == 503
    elif target == 'directory':
        (root/('a'*32)).symlink_to(path, target_is_directory=True)
        assert client.get('/local-reviews').status_code == 503
        assert client.get('/local-reviews/'+'a'*32).status_code == 409
    else:
        file = path/'requirements.md'
        original = root/'original'
        file.rename(original)
        if target == 'file':
            file.symlink_to(original)
        else:
            os.link(original, file)
        assert client.get('/local-reviews/'+path.name).status_code == 409


def test_masking_does_not_change_saved_evidence(client, saved_review_store, monkeypatch):
    root, bundles = saved_review_store
    path = bundles['passing']
    file = path/'requirements.md'
    payload = 'token=synthetic-test-token <img src=x onerror=alert(1)> Привет'
    file.write_text(payload, encoding='utf-8')
    manifest = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    manifest['files'][file.name] = {'bytes':len(file.read_bytes()),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    (path/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    original = (path/'manifest.json').read_bytes()
    data = client.get('/local-reviews/'+path.name).json()
    assert data['artifacts']['requirements.md'] == 'token=[REDACTED] <img src=x onerror=alert(1)> Привет'
    assert file.read_text(encoding='utf-8') == payload
    assert (path/'manifest.json').read_bytes() == original
    assert verify_bundle(path, allowed_root=root)['integrity_valid']


def test_reader_returns_the_verified_bytes_not_a_second_read(saved_review_store, monkeypatch):
    from backend.app.localrun import workflow
    root, bundles = saved_review_store
    path = bundles['passing']
    expected = (path/'requirements.md').read_bytes().decode('utf-8')
    original = workflow._read
    calls = []
    def read(file, limit):
        data = original(file, limit)
        calls.append(file.name)
        if file.name == 'requirements.md':
            file.write_text('Changed after read', encoding='utf-8')
        return data
    monkeypatch.setattr(workflow, '_read', read)
    assert read_bundle(path, allowed_root=root)['artifacts']['requirements.md'] == expected
    assert len(calls) == len(set(calls)) == 5


@pytest.mark.parametrize('malformed', ['overflow', 'surrogate', 'surrogate_key'])
def test_invalid_json_values_fail_before_response_serialization(client, saved_review_store, malformed):
    from backend.app.qa.runner import _hash
    root, bundles = saved_review_store
    path = bundles['passing']
    report = json.loads((path/'qa-report.json').read_text(encoding='utf-8'))
    if malformed == 'overflow':
        report['checks'][0]['duration_seconds'] = float('inf')
    elif malformed == 'surrogate':
        report['checks'][0]['stdout'] = chr(0xD800)
    else:
        report['checks'][0][chr(0xD800)] = 'invalid key'
    report['report_sha256'] = _hash({k:v for k,v in report.items() if k != 'report_sha256'})
    raw = json.dumps(report).replace('Infinity', '1e9999').encode('utf-8')
    (path/'qa-report.json').write_bytes(raw)
    manifest = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    manifest['report_sha256'] = report['report_sha256']
    manifest['files']['qa-report.json'] = {'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}
    (path/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    assert client.get('/local-reviews/'+path.name).status_code == 409
