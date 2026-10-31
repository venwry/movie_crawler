from pathlib import Path
from datetime import date
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from app.domain import film_for, identify
from app.integrations import parse_listing, save_entries, record_date, IntegrationError, SOURCES

URL = next(iter(SOURCES.values()))


def seed():
    rows, following = parse_listing(Path('tests/fixtures/listing.html').read_text(), URL, 'x265 / h265')
    save_entries(rows, date(2026,10,20))
    return rows, following


def test_parse_cutoff_grouping_idempotency_and_flags():
    rows, following = seed()
    assert following == URL+'?page=2'
    assert rows[0]['seeds'] == 42 and rows[0]['peers'] == 7
    with connect() as db:
        assert db.execute('SELECT count(*) FROM releases').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM films').fetchone()[0] == 1
        db.execute("UPDATE releases SET flag='download' WHERE id=1")
    rows[0]['seeds'] = 80
    save_entries(rows,date(2026,10,20))
    with connect() as db:
        r = db.execute('SELECT * FROM releases WHERE id=1').fetchone()
        assert r['seeds']==80 and r['flag']=='download'
        assert db.execute('SELECT count(*) FROM releases').fetchone()[0]==2


def test_grouping_year_and_sequel_safety():
    assert identify('Blade.Runner.2049.2017.1080p.x265') == ('Blade Runner 2049',2017)
    with connect() as db:
        original=film_for(db,'The Great Adventure',2020)
        assert film_for(db,'The Great Adventurre',2020)==original
        assert film_for(db,'The Great Adventure',2021)!=original
        assert film_for(db,'The Great Adventure 2',2020)!=film_for(db,'The Great Adventure 3',2020)


def test_dates_and_unknown_layout():
    assert record_date('wczoraj 20:12',date(2026,10,22))=='2026-10-21'
    assert record_date('2026-10-22T12:00:00')=='2026-10-22'
    assert record_date('31.02.2026') is None
    with pytest.raises(IntegrationError,match='No recognizable'):
        parse_listing('<h1>Access denied</h1>',URL,'x265')
    with pytest.raises(IntegrationError,match='unrecognized date'):
        parse_listing('<table><tr><td><a href="/torrent/123">Film (2020)</a></td></tr></table>',URL,'x265')


def test_api_browsing_flags_credentials_and_correction():
    seed()
    with TestClient(app,base_url='http://localhost') as client:
        assert client.get('/').status_code==200
        assert client.get('/api/health').json()=={'status':'ok'}
        assert client.put('/api/settings',json={'plex_token':'private-value','omdb_key':'secret'}).status_code==200
        assert 'private-value' not in client.get('/api/settings').text
        assert 'secret' not in client.get('/api/settings').text
        data=client.get('/api/films?year=2016&q=arr&category=x265%20%2F%20h265').json()
        assert data['total']==1
        fid=data['films'][0]['id']
        assert client.patch(f'/api/films/{fid}/flag',json={'flag':'download'}).status_code==200
        assert client.get('/api/films?flag=download').json()['total']==1
        detail=client.get(f'/api/films/{fid}').json()
        assert len(detail['releases'])==2
        assert detail['effective_flag']=='download'
        rid=detail['releases'][0]['id']
        assert client.patch(f'/api/releases/{rid}/flag',json={'flag':'ignored'}).status_code==200
        assert client.get('/api/films?flag=ignored').json()['total']==1
        moved=client.patch(f'/api/releases/{rid}/identity',json={'title':'A different film','year':2015}).json()
        assert moved['film_id'] != fid
        save_entries(seed()[0],date(2026,10,20))
        assert client.get(f'/api/films/{moved["film_id"]}').json()['release_count']==1
        assert client.patch(f'/api/films/{fid}/flag',json={'flag':'bad'}).status_code==400
        assert client.post('/api/scan',json={},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/scan',content='{}',headers={'Content-Type':'text/plain'}).status_code==415


def test_authentication(monkeypatch):
    monkeypatch.setenv('APP_PASSWORD','testing-password')
    with TestClient(app,base_url='http://localhost') as client:
        assert client.get('/api/settings').status_code==401
        assert client.get('/api/settings',auth=('admin','wrong')).status_code==401
        assert client.get('/api/settings',auth=('admin','testing-password')).status_code==200


def test_plex_ownership_and_genre_filters():
    seed()
    with connect() as db:
        fid=db.execute('SELECT id FROM films').fetchone()[0]
        db.execute('UPDATE films SET genres=? WHERE id=?',(json.dumps(['Drama']),fid))
        db.execute('INSERT INTO plex_items VALUES(?,?,?,?,?,?,1)',('server','1','99',fid,'Arrival',json.dumps({'files':[]})))
    with TestClient(app,base_url='http://localhost') as client:
        data=client.get('/api/films?view=plex&genre=Drama&flag=owned').json()
        assert data['total']==1 and data['films'][0]['in_plex']==1
        assert client.get('/api/films?genre=Comedy').json()['total']==0
