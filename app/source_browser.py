"""Local, user-operated sign-in. CAPTCHA challenges are never solved automatically."""
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

from .db import db_path
from .integrations import IntegrationError, SourceAccessRequired, SourceCancelled, SOURCES, parse_listing, scan

_waiting = threading.Event()
_continue = threading.Event()
_cancel = threading.Event()


def session_path():
    return db_path().parent / 'source-session.json'


def status():
    return {'saved': session_path().is_file(), 'waiting': _waiting.is_set()}


def respond(cancel=False):
    if not _waiting.is_set():
        raise IntegrationError('The source browser is not waiting for input.')
    (_cancel if cancel else _continue).set()


def save_session(context):
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    try:
        # Create with private permissions before writing any cookies on POSIX.
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        if os.name != 'nt':
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(context.storage_state(), stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clear_session():
    session_path().unlink(missing_ok=True)


def wait_for_user(page, progress, message, timeout=600):
    _continue.clear()
    _cancel.clear()
    _waiting.set()
    try:
        progress(message + ' Complete it in the source browser, then click Continue in Movie Harbor. Close the source window or click Cancel to stop.')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _cancel.is_set() or page.is_closed():
                raise SourceCancelled('Source browser operation cancelled. Existing catalog data is preserved.')
            if _continue.is_set():
                return
            # Pump browser events while waiting, including the user closing it.
            page.wait_for_timeout(250)
        raise SourceCancelled('Source sign-in timed out after 10 minutes. Open sign-in again when ready.')
    finally:
        _waiting.clear()
        _continue.clear()
        _cancel.clear()


@contextmanager
def browser_session(use_saved=True):
    from playwright.sync_api import sync_playwright, Error as BrowserError
    try:
        with sync_playwright() as playwright:
            options = {'headless': False}
            executable = os.environ.get('CHROMIUM_PATH')
            if executable:
                options['executable_path'] = executable
            elif os.name == 'nt':
                options['channel'] = 'msedge'
            else:
                executable = shutil.which('chromium') or shutil.which('chromium-browser')
                if executable:
                    options['executable_path'] = executable
            proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
            if proxy:
                options['proxy'] = {'server': proxy}
            browser = playwright.chromium.launch(**options)
            try:
                context = browser.new_context(storage_state=str(session_path()) if use_saved and session_path().is_file() else None)
                try:
                    yield context
                finally:
                    context.close()
            finally:
                browser.close()
    except BrowserError:
        # Playwright errors may contain page URLs, headers or browser command lines.
        raise IntegrationError('The source browser could not start or its window was closed. On Windows, install/update Microsoft Edge and rerun setup.cmd. Run the app on your desktop, not a background service. On Linux, a desktop and Chromium are required.') from None
    except (ValueError, OSError):
        raise IntegrationError('The saved source session could not be read or written. Clear it in Connections and sign in again.') from None


def navigate(page, url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'devil-torrents.pl':
        raise IntegrationError('Unexpected source URL rejected.')
    response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
    # Give client-side listing rendering a short bounded opportunity to finish.
    page.wait_for_timeout(1000)
    if urlsplit(page.url).hostname != 'devil-torrents.pl':
        raise IntegrationError('Source redirected to another site. Sign in directly to the configured source.')
    if response and response.status >= 400 and response.status not in (401,403,429,503):
        raise IntegrationError(f'Source returned HTTP {response.status}.')
    return page.content()


def sign_in(progress):
    category, url = next(iter(SOURCES.items()))
    with browser_session() as context:
        page = context.new_page()
        navigate(page, url)
        while True:
            wait_for_user(page, progress, 'Sign in to devil-torrents.pl and complete any CAPTCHA in the opened browser.')
            html = navigate(page, url)
            try:
                parse_listing(html, url, category)
            except SourceAccessRequired:
                progress('The source still asks for login or CAPTCHA. Finish it in the browser before continuing.')
                continue
            except IntegrationError:
                # Preserve the user's browser session, but do not claim verified access.
                save_session(context)
                raise IntegrationError('Browser session saved, but film rows could not be recognized. The source HTML adapter needs verification; sign-in alone could not validate scanning.') from None
            save_session(context)
            return 'Source session saved and a film listing was recognized. You can now scan sources.'


def scan_with_session(cutoff, max_pages, progress):
    if not session_path().is_file():
        return scan(cutoff, max_pages, progress)
    with browser_session() as context:
        page = context.new_page()
        def read_page(url, category):
            html = navigate(page, url)
            while True:
                try:
                    parse_listing(html, url, category)
                except SourceAccessRequired:
                    wait_for_user(page, progress, 'The scan is paused: the source needs login or a CAPTCHA.')
                    html = navigate(page, url)
                    continue
                save_session(context)
                return html
        return scan(cutoff, max_pages, progress, read_page=read_page)
