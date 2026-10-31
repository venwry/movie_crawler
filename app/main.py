import hmac
import json
import os
import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field

from .db import connect, initialize, settings
from .domain import FLAGS, normalize
from .integrations import IntegrationError, now, scan, plex_libraries, sync_plex, enrich, SOURCES

STATIC = Path(__file__).parent / 'static'
executor = ThreadPoolExecutor(max_workers=1)
job_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title='Movie Harbor', lifespan=lifespan)


@app.middleware('http')
async def security(request: Request, call_next):
    password = os.environ.get('APP_PASSWORD')
    if not password and request.url.hostname not in ('localhost', '127.0.0.1', '::1'):
        return JSONResponse({'detail': 'Use localhost, or configure APP_PASSWORD for network access.'}, 403)
    if password:
        try:
            scheme, encoded = request.headers.get('authorization', '').split(' ', 1)
            username, supplied = base64.b64decode(encoded, validate=True).decode().split(':', 1)
            valid = scheme.lower() == 'basic' and username == 'admin' and hmac.compare_digest(supplied, password)
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            return JSONResponse({'detail': 'Authentication required'}, 401, headers={'WWW-Authenticate': 'Basic realm="Movie Harbor"'})
    # JSON + same-origin checks prevent browser forms and cross-site scripts changing state.
    if request.method in ('POST', 'PATCH', 'DELETE', 'PUT'):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Cross-origin writes are not allowed'}, 403)
        if 'application/json' not in request.headers.get('content-type', ''):
            return JSONResponse({'detail': 'Use application/json'}, 415)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(IntegrationError)
async def integration_error(request, exc):
    return JSONResponse({'detail': str(exc)}, 400)


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/health')
def health():
    with connect() as db:
        db.execute('SELECT 1').fetchone()
    return {'status': 'ok'}


@app.get('/api/settings')
def get_settings():
    s = settings()
    return {'plex_url': s.get('plex_url', ''), 'plex_library': s.get('plex_library', ''),
            'plex_token_set': bool(s.get('plex_token')), 'omdb_key_set': bool(s.get('omdb_key')),
            'environment_overrides': [k for k,e in [('plex_url','PLEX_URL'),('plex_token','PLEX_TOKEN'),('omdb_key','OMDB_API_KEY')] if os.environ.get(e)],
            'sources': SOURCES}


class SettingsInput(BaseModel):
    plex_url: str = Field(default='', max_length=500)
    plex_library: str = Field(default='', max_length=20)
    plex_token: str | None = Field(default=None, max_length=500)
    omdb_key: str | None = Field(default=None, max_length=500)


@app.put('/api/settings')
def save_settings(body: SettingsInput):
    with connect() as db:
        for key, value in body.model_dump().items():
            if value is not None:
                db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value.strip())))
    return get_settings()


@app.get('/api/plex/libraries')
def libraries():
    try:
        return plex_libraries()
    except IntegrationError:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to read Plex libraries. Check the server URL, token and network connection.')


def launch(kind, operation):
    if not job_lock.acquire(blocking=False):
        raise HTTPException(409, 'Another operation is running. Wait for it to finish.')
    try:
        with connect() as db:
            jid = db.execute('INSERT INTO jobs(kind,state,message,started_at) VALUES(?,?,?,?)', (kind,'running','Starting…',now())).lastrowid
        def progress(message):
            with connect() as db:
                db.execute('UPDATE jobs SET message=? WHERE id=?', (message, jid))
        def run():
            try:
                message = operation(progress)
                state = 'complete'
            except Exception as exc:
                state = 'failed'
                message = str(exc) if isinstance(exc, IntegrationError) else 'Operation failed. Check the remote service, credentials and response format; existing data is preserved.'
            finally:
                try:
                    with connect() as db:
                        db.execute('UPDATE jobs SET state=?,message=?,finished_at=? WHERE id=?', (state,message,now(),jid))
                finally:
                    job_lock.release()
        executor.submit(run)
        return {'id': jid}
    except Exception:
        job_lock.release()
        raise


class ScanInput(BaseModel):
    cutoff: date = date(2026, 10, 20)
    max_pages: int = Field(default=100, ge=1, le=1000)


@app.post('/api/scan')
def start_scan(body: ScanInput):
    return launch('scan', lambda progress: scan(body.cutoff, body.max_pages, progress))


@app.post('/api/plex/sync')
def start_sync():
    library = settings().get('plex_library', '')
    if not library:
        raise HTTPException(400, 'Select and save a Plex library in Connections first.')
    return launch('plex', lambda progress: sync_plex(library, progress))


@app.post('/api/ratings')
def start_ratings():
    return launch('ratings', enrich)


@app.get('/api/jobs')
def jobs():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 10')]


def decoded(row):
    r = dict(row)
    for key in ('genres', 'ratings'):
        r[key] = json.loads(r[key])
    r['effective_flag'] = r['flag'] if r['flag'] != 'none' else ('owned' if r.get('in_plex') else 'none')
    return r


FILM_SELECT = '''SELECT f.*,
    EXISTS(SELECT 1 FROM plex_items p WHERE p.film_id=f.id AND p.active=1) in_plex,
    (SELECT count(*) FROM releases r WHERE r.film_id=f.id) release_count,
    (SELECT max(recorded_at) FROM releases r WHERE r.film_id=f.id) latest,
    (SELECT max(seeds) FROM releases r WHERE r.film_id=f.id) seeds
    FROM films f'''


@app.get('/api/films')
def films(view: str = 'discover', q: str = '', year: str = '', genre: str = '', flag: str = '',
          category: str = '', sort: str = 'newest', page: int = 1):
    with connect() as db:
        rows = [decoded(r) for r in db.execute(FILM_SELECT)]
        releases = [dict(r) for r in db.execute('SELECT film_id,category,flag FROM releases')]
    by_film = {}
    for r in releases:
        by_film.setdefault(r['film_id'], []).append(r)
    base = [r for r in rows if r['in_plex']] if view == 'plex' else [r for r in rows if r['release_count']]
    facets = {'years': sorted({r['year'] for r in base if r['year']}, reverse=True),
              'genres': sorted({g for r in base for g in r['genres']})}
    results = []
    for r in base:
        variants = by_film.get(r['id'], [])
        if q.casefold() not in r['title'].casefold() or (year and str(r['year']) != year) or (genre and genre not in r['genres']):
            continue
        if category and not any(v['category'] == category for v in variants):
            continue
        if flag and r['effective_flag'] != flag and not any((v['flag'] if v['flag'] != 'none' else r['effective_flag']) == flag for v in variants):
            continue
        results.append(r)
    if sort == 'title':
        results.sort(key=lambda r:r['title'].casefold())
    else:
        key = {'newest':'latest','year':'year','rating':'rating','seeds':'seeds'}.get(sort, 'latest')
        results.sort(key=lambda r:(r[key] is not None, r[key] or ('' if key == 'latest' else 0),r['id']),reverse=True)
    page = max(1,page)
    return {'films':results[(page-1)*50:page*50], 'total':len(results), 'page':page, 'facets':facets,
            'stats':{'discovered':sum(bool(r['release_count']) for r in rows),'plex':sum(bool(r['in_plex']) for r in rows),
                     'queued':sum(r['effective_flag']=='download' or any(v['flag']=='download' for v in by_film.get(r['id'],[])) for r in rows)}}


@app.get('/api/films/{film_id}')
def detail(film_id: int):
    with connect() as db:
        row = db.execute(FILM_SELECT + ' WHERE f.id=?', (film_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Film not found')
        result = decoded(row)
        result['releases'] = [dict(r) for r in db.execute('SELECT * FROM releases WHERE film_id=? ORDER BY recorded_at DESC', (film_id,))]
        result['plex'] = [{**dict(r), 'details': json.loads(r['details'])} for r in db.execute('SELECT * FROM plex_items WHERE film_id=? AND active=1', (film_id,))]
    return result


class FlagInput(BaseModel):
    flag: str


@app.patch('/api/{kind}/{item_id}/flag')
def flag_item(kind: str, item_id: int, body: FlagInput):
    if kind not in ('films', 'releases') or body.flag not in FLAGS:
        raise HTTPException(400, 'Invalid flag or item type')
    with connect() as db:
        if db.execute(f'UPDATE {kind} SET flag=? WHERE id=?', (body.flag, item_id)).rowcount == 0:
            raise HTTPException(404, 'Item not found')
    return {'ok':True}


class MoveInput(BaseModel):
    title: str = Field(min_length=1,max_length=300)
    year: int | None = Field(default=None,ge=1888,le=2100)


@app.patch('/api/releases/{item_id}/identity')
def move_release(item_id: int, body: MoveInput):
    with connect() as db:
        if not db.execute('SELECT 1 FROM releases WHERE id=?',(item_id,)).fetchone():
            raise HTTPException(404,'Release not found')
        title = body.title.strip()
        if not title:
            raise HTTPException(400,'Film title cannot be blank')
        row = db.execute('SELECT id FROM films WHERE normalized=? AND year IS ?', (normalize(title),body.year)).fetchone()
        fid = row['id'] if row else db.execute('INSERT INTO films(title,normalized,year) VALUES(?,?,?)', (title,normalize(title),body.year)).lastrowid
        db.execute('UPDATE releases SET film_id=? WHERE id=?',(fid,item_id))
    return {'film_id':fid}


app.mount('/static', StaticFiles(directory=STATIC), name='static')
