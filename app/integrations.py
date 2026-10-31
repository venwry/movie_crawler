import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit, parse_qs
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from defusedxml import ElementTree

from .db import connect, settings
from .domain import film_for, identify, normalize

SOURCES = {
    'x265 / h265': 'https://devil-torrents.pl/files/torrent/Filmy%20x265/h265',
    'x264 / h264': 'https://devil-torrents.pl/files/torrent/Filmy%20x264/h264',
    'XviD / DivX': 'https://devil-torrents.pl/files/torrent/Filmy%20XviD/DivX',
    'Blu-Ray / HD': 'https://devil-torrents.pl/files/torrent/Filmy%20Blu-Ray/HD',
}


class IntegrationError(Exception):
    pass


class SourceAccessRequired(IntegrationError):
    """A human must sign in or complete a challenge in the local browser."""


class SourceCancelled(IntegrationError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def client():
    return httpx.Client(timeout=30, follow_redirects=False, headers={'User-Agent': 'MovieHarbor/1.0 personal film catalog'})


def fetch(http, url, **kwargs):
    # Keep headers/tokens on the configured origin; never follow arbitrary redirects.
    for _ in range(4):
        response = http.get(url, **kwargs)
        if response.status_code in (301, 302, 303, 307, 308):
            target = urljoin(url, response.headers.get('location', ''))
            if urlsplit(target).netloc != urlsplit(url).netloc or urlsplit(target).scheme != urlsplit(url).scheme:
                raise IntegrationError('Server redirected to another origin; configure its final URL directly.')
            url = target
            continue
        if response.status_code != 200:
            raise IntegrationError(f'Remote service returned HTTP {response.status_code}. Check access and credentials.')
        return response
    raise IntegrationError('Too many redirects.')


def record_date(text, today=None):
    today = today or datetime.now(ZoneInfo('Europe/Warsaw')).date()
    lower = text.casefold()
    if 'dzisiaj' in lower or 'today' in lower:
        return today.isoformat()
    if 'wczoraj' in lower or 'yesterday' in lower:
        return (today - timedelta(days=1)).isoformat()
    for pattern, order in [(r'(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)', 'ymd'),
                           (r'(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](20\d{2})(?!\d)', 'dmy')]:
        match = re.search(pattern, text)
        if match:
            a, b, c = map(int, match.groups())
            try:
                return (date(a,b,c) if order == 'ymd' else date(c,b,a)).isoformat()
            except ValueError:
                pass
    return None


def number(text):
    match = re.search(r'\d[\d\s,]*', text or '')
    return int(re.sub(r'\D', '', match[0])) if match else None


def parse_listing(html, url, category):
    soup = BeautifulSoup(html, 'html.parser')
    entries = []
    rows = soup.select('tr, .torrent-row, .torrent-item, [data-torrent-id]')
    for row in rows:
        links = [a for a in row.select('a[href]') if '/torrent/' in a['href'] and '/files/torrent/' not in a['href']]
        if not links:
            continue
        link = max(links, key=lambda a: len(a.get_text(' ', strip=True)))
        title = link.get_text(' ', strip=True) or link.get('title', '')
        if not title:
            continue
        target = urljoin(url, link['href'])
        if urlsplit(target).hostname != 'devil-torrents.pl':
            continue
        text = row.get_text(' ', strip=True)
        time_node = row.select_one('time[datetime]')
        recorded = record_date(time_node['datetime'] if time_node else text)
        if not recorded:
            raise IntegrationError('A film row has an unrecognized date. Scan stopped to avoid silently skipping entries.')
        size_match = re.search(r'\b\d+(?:[.,]\d+)?\s*(?:[KMGT]i?B)\b', text, re.I)
        cells = row.find_all(['td', 'th'], recursive=False)
        table = row.find_parent('table')
        headers = [normalize(h.get_text(' ', strip=True)) for h in table.select('thead th, tr:first-child th')] if table else []
        values = dict(zip(headers, [c.get_text(' ', strip=True) for c in cells]))
        def count(kind):
            for key, value in values.items():
                if any(term in key for term in (('seed', 'seedow') if kind == 'seeds' else ('peer', 'leech'))):
                    return number(value)
            node = row.select_one(f'.{kind}, [data-{kind}]')
            if node:
                return number(node.get(f'data-{kind}') or node.get_text())
            match = re.search(rf'{kind}?\s*:?\s*(\d+)', text, re.I)
            return int(match[1]) if match else None
        entries.append(dict(title=title, url=target.split('#')[0], category=category,
                            size=size_match[0] if size_match else None, seeds=count('seeds'),
                            peers=count('peers'), recorded_at=recorded))
    if not entries:
        # Many sites embed a login form in the header even on public listings.
        # Only report access trouble after looking for usable film rows.
        if soup.select_one('input[type=password]') or any(marker in html.lower() for marker in (
            'captcha', 'cf-chl-', 'challenge-platform', 'verify you are human', 'just a moment',
        )):
            raise SourceAccessRequired('Source login or CAPTCHA is required. Open Connections → Sign in to source, complete it in the local browser, then scan again.')
        raise IntegrationError('No recognizable film rows found. The site may be blocked or its HTML layout may need an adapter update.')
    next_url = None
    for a in soup.select('a[href]'):
        label = a.get_text(' ', strip=True).casefold()
        if 'next' in a.get('rel', []) or label in ('next', 'następna', 'następny', '›', '»', '>') or 'next' in a.get('class', []):
            next_url = urljoin(url, a['href'])
            break
    if not next_url:
        current = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
        for a in soup.select('a[href]'):
            target = urljoin(url, a['href'])
            page = parse_qs(urlsplit(target).query).get('page', [''])[0]
            if page.isdigit() and int(page) == current + 1:
                next_url = target
                break
    if next_url and (urlsplit(next_url).hostname != 'devil-torrents.pl' or urlsplit(next_url).scheme != 'https'):
        raise IntegrationError('Unsafe pagination link rejected.')
    return entries, next_url


def save_entries(entries, cutoff):
    count = 0
    with connect() as db:
        for e in entries:
            if date.fromisoformat(e['recorded_at']) <= cutoff:
                continue
            title, year = identify(e['title'])
            existing = db.execute('SELECT film_id FROM releases WHERE url=?', (e['url'],)).fetchone()
            fid = existing['film_id'] if existing else film_for(db, title, year)
            db.execute('''INSERT INTO releases(film_id,title,url,category,size,seeds,peers,recorded_at,first_seen,last_seen)
              VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,
              size=excluded.size,seeds=excluded.seeds,peers=excluded.peers,last_seen=excluded.last_seen''',
              (fid,e['title'],e['url'],e['category'],e['size'],e['seeds'],e['peers'],e['recorded_at'],now(),now()))
            count += 1
    return count


def scan(cutoff, max_pages, progress, read_page=None):
    count = 0
    failures = []
    with client() as http:
        for category, start in SOURCES.items():
            url, visited = start, set()
            try:
                for page in range(max_pages):
                    if url in visited:
                        raise IntegrationError('Repeated pagination URL; scan incomplete.')
                    visited.add(url)
                    progress(f'{category}: page {page + 1}; {count} entries processed')
                    html = read_page(url, category) if read_page else fetch(http, url).text
                    entries, next_url = parse_listing(html, url, category)
                    count += save_entries(entries, cutoff)
                    # Traverse until a full page is old, tolerating pinned entries in mixed pages.
                    if all(date.fromisoformat(e['recorded_at']) <= cutoff for e in entries) or not next_url:
                        break
                    url = next_url
                    time.sleep(1)
                else:
                    raise IntegrationError(f'Page limit ({max_pages}) reached; scan incomplete. Increase the limit and scan again.')
            except SourceCancelled:
                raise
            except (IntegrationError, httpx.HTTPError) as exc:
                message = str(exc) if isinstance(exc, IntegrationError) else 'Network request failed; check network access.'
                failures.append(f'{category}: {message}')
    if failures:
        raise IntegrationError(f'{count} entries processed. ' + ' | '.join(failures))
    return f'Scan complete: {count} entries processed (existing URLs updated).'


def plex_config():
    config = settings()
    url, token = config.get('plex_url', '').rstrip('/'), config.get('plex_token', '')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise IntegrationError('Configure a valid Plex server URL, e.g. http://192.168.1.20:32400.')
    if not token:
        raise IntegrationError('Configure your Plex token first.')
    return url, {'X-Plex-Token': token, 'Accept': 'application/xml'}


def plex_libraries():
    url, headers = plex_config()
    with client() as http:
        root = ElementTree.fromstring(fetch(http, url + '/library/sections', headers=headers).content)
    if root.tag != 'MediaContainer':
        raise IntegrationError('The server did not return a Plex library response. Check its URL.')
    return [{'id': d.get('key'), 'title': d.get('title')} for d in root.findall('Directory') if d.get('type') == 'movie']


def sync_plex(library, progress):
    url, headers = plex_config()
    if not library.isdigit():
        raise IntegrationError('Select a movie library.')
    records, offset = [], 0
    with client() as http:
        while True:
            root = ElementTree.fromstring(fetch(http, f'{url}/library/sections/{library}/all', headers=headers,
              params={'type': 1, 'X-Plex-Container-Start': offset, 'X-Plex-Container-Size': 200}).content)
            if root.tag != 'MediaContainer' or not any(k in root.attrib for k in ('size','totalSize')):
                raise IntegrationError('Invalid Plex movie response; existing snapshot preserved.')
            videos = root.findall('Video')
            total = int(root.get('totalSize', root.get('size', len(videos))))
            if total < 0 or (root.get('offset') is not None and int(root.get('offset')) != offset):
                raise IntegrationError('Unexpected Plex pagination; existing snapshot preserved.')
            if not videos and offset < total:
                raise IntegrationError('Plex returned an incomplete library; the existing snapshot was preserved.')
            records.extend(videos)
            offset += len(videos)
            progress(f'Retrieved {offset} of {total} Plex films')
            if offset > 100000 or any(not v.get('ratingKey') for v in videos) or len({v.get('ratingKey') for v in records}) != len(records):
                raise IntegrationError('Plex pagination did not advance; existing snapshot preserved.')
            if offset >= total:
                break
    with connect() as db:
        db.execute('UPDATE plex_items SET active=0 WHERE server=? AND library=?', (url, library))
        for v in records:
            title, year = v.get('title', 'Untitled'), int(v.get('year')) if v.get('year') else None
            fid = film_for(db, title, year)
            genres = [g.get('tag') for g in v.findall('Genre')]
            files = []
            for media in v.findall('Media'):
                for part in media.findall('Part'):
                    files.append({**media.attrib, **part.attrib})
            details = {'summary': v.get('summary', ''), 'genres': genres, 'files': files, 'synced_at': now()}
            db.execute('''INSERT INTO plex_items(server,library,rating_key,film_id,title,details,active) VALUES(?,?,?,?,?,?,1)
              ON CONFLICT(server,library,rating_key) DO UPDATE SET film_id=excluded.film_id,title=excluded.title,details=excluded.details,active=1''',
              (url,library,v.get('ratingKey'),fid,title,json.dumps(details)))
            if genres:
                db.execute("UPDATE films SET genres=? WHERE id=? AND genres='[]'", (json.dumps(genres), fid))
    return f'Plex library synchronized: {len(records)} films.'


def enrich(progress):
    key = settings().get('omdb_key')
    if not key:
        raise IntegrationError('Add a free OMDb API key in Connections first.')
    with connect() as db:
        films = db.execute("SELECT * FROM films WHERE metadata_at IS NULL OR metadata_at < ? LIMIT 200",
                           ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),)).fetchall()
    count, misses = 0, 0
    with client() as http:
        for f in films:
            progress(f'Looking up {f["title"]} ({count + misses + 1}/{len(films)})')
            params = {'apikey': key, 't': f['title'], 'type': 'movie', 'plot': 'full'}
            if f['year']:
                params['y'] = f['year']
            result = fetch(http, 'https://www.omdbapi.com/', params=params).json()
            if result.get('Response') != 'True':
                error = result.get('Error', 'Lookup failed')
                if 'key' in error.lower() or 'limit' in error.lower():
                    raise IntegrationError('OMDb rejected the key or its daily quota is exhausted.')
                with connect() as db:
                    db.execute('UPDATE films SET metadata_error=?,metadata_at=? WHERE id=?', ('No matching OMDb movie.', now(), f['id']))
                misses += 1
                continue
            # Refuse a plausible but wrong movie rather than attach a misleading score.
            if (f['year'] and str(f['year']) != result.get('Year')) or normalize(result.get('Title','')) != normalize(f['title']):
                with connect() as db:
                    db.execute('UPDATE films SET metadata_error=?,metadata_at=? WHERE id=?', ('Ambiguous title; metadata was not attached.', now(), f['id']))
                misses += 1
                continue
            rating = result.get('imdbRating', 'N/A')
            with connect() as db:
                db.execute('UPDATE films SET genres=?,plot=?,rating=?,ratings=?,imdb_id=?,metadata_at=?,metadata_error=NULL WHERE id=?',
                  (json.dumps(result.get('Genre', '').split(', ') if result.get('Genre') != 'N/A' else []), result.get('Plot'),
                   float(rating) if rating != 'N/A' else None,json.dumps(result.get('Ratings', [])),result.get('imdbID'),now(),f['id']))
            count += 1
            time.sleep(.25)
    return f'Ratings refreshed: {count} matched, {misses} unmatched. Up to 200 movies per run; cached for 30 days.'
