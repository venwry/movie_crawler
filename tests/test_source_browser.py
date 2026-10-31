import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import source_browser as source, integrations as integration
from app.main import app, job_lock
from app.db import connect

URL = next(iter(integration.SOURCES.values()))
LOGIN = '<form><input type="password"><div class="g-recaptcha">CAPTCHA</div></form>'


def listing():
    return Path('tests/fixtures/listing.html').read_text().split('<a rel="next"')[0]


class Page:
    def __init__(self, documents, cancel=False):
        self.documents = iter(documents)
        self.document = ''
        self.url = URL
        self.cancel = cancel
        self.visits = []

    def goto(self, url, **kwargs):
        self.url = url
        self.visits.append(url)
        self.document = next(self.documents)
        return SimpleNamespace(status=200)

    def content(self):
        return self.document

    def is_closed(self):
        return False

    def wait_for_timeout(self, milliseconds):
        if source.status()['waiting']:
            source.respond(cancel=self.cancel)


class Context:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page

    def storage_state(self):
        return {'cookies': [{'name': 'test-session', 'value': 'synthetic-private-cookie'}], 'origins': []}


def install_context(monkeypatch, page):
    @contextmanager
    def browser_session():
        yield Context(page)
    monkeypatch.setattr(source, 'browser_session', browser_session)


def test_public_listing_with_login_widget_is_allowed():
    rows, _ = integration.parse_listing(LOGIN + listing(), URL, 'test')
    assert len(rows) == 3
    with pytest.raises(integration.SourceAccessRequired):
        integration.parse_listing(LOGIN, URL, 'test')
    with pytest.raises(integration.SourceAccessRequired):
        integration.parse_listing('<title>Just a moment...</title>', URL, 'test')


def test_sign_in_saves_session_only_after_continue(monkeypatch):
    page = Page([LOGIN, listing()])
    install_context(monkeypatch, page)
    messages = []
    assert 'listing was recognized' in source.sign_in(messages.append)
    assert len(page.visits) == 2
    assert source.status() == {'saved': True, 'waiting': False}
    assert 'synthetic-private-cookie' in source.session_path().read_text()
    assert all('synthetic-private-cookie' not in message for message in messages)
    if os.name != 'nt':
        assert source.session_path().stat().st_mode & 0o777 == 0o600
    with TestClient(app, base_url='http://localhost') as http:
        assert http.get('/api/source/status').json() == {'saved': True, 'waiting': False}
        assert 'synthetic-private-cookie' not in http.get('/api/source/status').text
        assert http.post('/api/source/continue', json={}).status_code == 400
        assert http.request('DELETE', '/api/source/session', json={}).status_code == 200
        assert not source.session_path().exists()


def test_cancel_sign_in_does_not_save_credentials(monkeypatch):
    install_context(monkeypatch, Page([LOGIN], cancel=True))
    with pytest.raises(integration.SourceCancelled, match='cancelled'):
        source.sign_in(lambda _: None)
    assert source.status() == {'saved': False, 'waiting': False}


def test_authenticated_scan_pauses_and_resumes(monkeypatch):
    source.save_session(Context(None))
    page = Page([LOGIN, listing()])
    install_context(monkeypatch, page)
    monkeypatch.setattr(integration, 'SOURCES', {'test': URL})
    messages = []
    assert 'complete' in source.scan_with_session(date(2026,10,20), 2, messages.append)
    assert any('paused' in message for message in messages)
    with connect() as db:
        assert db.execute('SELECT count(*) FROM releases').fetchone()[0] == 2


def test_cancel_stops_all_categories(monkeypatch):
    source.save_session(Context(None))
    page = Page([LOGIN], cancel=True)
    install_context(monkeypatch, page)
    with pytest.raises(integration.SourceCancelled):
        source.scan_with_session(date(2026,10,20), 2, lambda _: None)
    assert len(page.visits) == 1


def test_session_cannot_be_deleted_during_a_job():
    source.save_session(Context(None))
    with TestClient(app, base_url='http://localhost') as http:
        job_lock.acquire()
        try:
            assert http.request('DELETE', '/api/source/session', json={}).status_code == 409
            assert source.session_path().exists()
        finally:
            job_lock.release()


def test_unknown_layout_saves_session_but_reports_unverified(monkeypatch):
    install_context(monkeypatch, Page([LOGIN, '<main>Unexpected layout</main>']))
    with pytest.raises(integration.IntegrationError, match='adapter needs verification'):
        source.sign_in(lambda _: None)
    assert source.status() == {'saved': True, 'waiting': False}


def test_missing_session_uses_public_scanner(monkeypatch):
    monkeypatch.setattr(source, 'scan', lambda *args: 'public scan')
    assert source.scan_with_session(date(2026,10,20), 2, lambda _: None) == 'public scan'
