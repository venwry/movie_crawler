"""Optional end-to-end checks; synthetic records, isolated database, local Chromium."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import shutil

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory() as directory:
    os.environ['MOVIE_DB'] = str(Path(directory) / 'catalog.sqlite3')
    os.environ.pop('APP_PASSWORD',None)
    from app.db import initialize, connect
    from app.integrations import parse_listing, save_entries, SOURCES
    from datetime import date
    initialize()
    rows,_=parse_listing((ROOT/'tests/fixtures/listing.html').read_text(),next(iter(SOURCES.values())),'x265 / h265')
    save_entries(rows,date(2026,10,20))
    with connect() as db:
        db.execute('UPDATE films SET genres=?,rating=?,plot=?,ratings=?',
                   (json.dumps(['Drama','Sci-Fi']),7.9,'A linguist makes contact with visitors from another world.',json.dumps([{'Source':'Internet Movie Database','Value':'7.9/10'}])))
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--port','8001','--host','127.0.0.1'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(trust_env=False) as http:
            for _ in range(50):
                try:
                    if http.get('http://127.0.0.1:8001/api/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('Test server failed to start')
        with sync_playwright() as p:
            browser_path = os.environ.get('CHROMIUM_PATH') or shutil.which('chromium') or shutil.which('chromium-browser')
            # With no explicit/system executable, use Playwright's installed browser.
            browser=p.chromium.launch(executable_path=browser_path,headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1050})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8001')
            page.get_by_role('button',name='Arrival').click()
            page.locator('#detail h2').filter(has_text='Arrival').wait_for()
            assert page.locator('.release').count()==2
            page.locator('#film-flag').select_option('download')
            expect(page.locator('#metric-queued')).to_have_text('1')
            page.get_by_role('button',name='Download list').click()
            page.locator('.film-row').wait_for()
            page.reload()
            page.locator('.film-row').wait_for()
            page.get_by_role('button',name='Arrival').click()
            page.locator('#film-flag').wait_for()
            assert page.locator('#film-flag').input_value()=='download'
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'movie-harbor-desktop.png'),full_page=True)
            page.locator('#search').fill('no such film')
            page.get_by_role('heading',name='No matching films').wait_for()
            page.locator('#search').fill('')
            page.locator('.film-row').wait_for()
            page.get_by_role('button',name='Connections',exact=False).click()
            page.locator('#settings-dialog').wait_for(state='visible')
            page.locator('[data-close="settings-dialog"]').click()
            page.get_by_role('button',name='Scan sources').click()
            assert page.locator('#cutoff').input_value()=='2026-10-20'
            page.locator('[data-close="scan-dialog"]').click()
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'movie-harbor-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e => e.getBoundingClientRect().right > innerWidth).map(e => [e.tagName, e.className, e.getBoundingClientRect().right])")
            assert not errors,errors
            browser.close()
        print('Browser checks passed: browse, details, flags, persistence, queue, search, dialogs, mobile layout; no JavaScript errors.')
    finally:
        process.terminate()
        process.wait(timeout=10)
