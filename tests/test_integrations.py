import json
from datetime import date
from pathlib import Path
import httpx
import pytest
from app import integrations as i
from app.db import connect


def config(**values):
    with connect() as db:
        for k,v in values.items():
            db.execute('INSERT INTO settings VALUES(?,?)',(k,json.dumps(v)))


def transport(monkeypatch,handler):
    monkeypatch.setattr(i,'client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(i.time,'sleep',lambda _:None)


def test_paginated_scan_and_old_page_stop(monkeypatch):
    calls=[]
    html=Path('tests/fixtures/listing.html').read_text()
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,text=html if 'page=2' not in str(request.url) else html.replace('22.10.2026','19.10.2026').replace('21.10.2026','18.10.2026'))
    transport(monkeypatch,handler)
    monkeypatch.setattr(i,'SOURCES',{'test':'https://devil-torrents.pl/files/torrent/test'})
    assert 'complete' in i.scan(date(2026,10,20),10,lambda _:None)
    assert len(calls)==2
    with connect() as db:
        assert db.execute('SELECT count(*) FROM releases').fetchone()[0]==2


def test_page_limit_reports_partial_success(monkeypatch):
    transport(monkeypatch,lambda r:httpx.Response(200,text=Path('tests/fixtures/listing.html').read_text()))
    monkeypatch.setattr(i,'SOURCES',{'test':'https://devil-torrents.pl/files/torrent/test'})
    with pytest.raises(i.IntegrationError,match='Page limit'):
        i.scan(date(2026,10,20),1,lambda _:None)
    with connect() as db:
        assert db.execute('SELECT count(*) FROM releases').fetchone()[0]==2


def test_plex_pagination_atomic_sync_and_file_details(monkeypatch):
    config(plex_url='http://plex.local:32400',plex_token='test-token')
    def handler(request):
        assert request.headers['X-Plex-Token']=='test-token'
        if request.url.path=='/library/sections':
            return httpx.Response(200,text='<MediaContainer><Directory key="1" title="Movies" type="movie"/><Directory key="2" title="TV" type="show"/></MediaContainer>')
        start=request.url.params['X-Plex-Container-Start']
        return httpx.Response(200,text=f'<MediaContainer totalSize="2"><Video ratingKey="{start}" title="Film {start}" year="2020"><Genre tag="Drama"/><Media videoCodec="hevc" videoResolution="1080"><Part file="/movies/test.mkv" size="1234"/></Media></Video></MediaContainer>')
    transport(monkeypatch,handler)
    assert i.plex_libraries()==[{'id':'1','title':'Movies'}]
    assert '2 films' in i.sync_plex('1',lambda _:None)
    with connect() as db:
        records=db.execute('SELECT * FROM plex_items WHERE active=1').fetchall()
        assert len(records)==2
        assert json.loads(records[0]['details'])['files'][0]['file']=='/movies/test.mkv'
    def failure(request):
        if request.url.params['X-Plex-Container-Start']=='0':
            return httpx.Response(200,text='<MediaContainer totalSize="2"><Video ratingKey="new" title="New film" year="2026"/></MediaContainer>')
        return httpx.Response(500)
    transport(monkeypatch,failure)
    with pytest.raises(i.IntegrationError):
        i.sync_plex('1',lambda _:None)
    with connect() as db:
        assert [r['rating_key'] for r in db.execute('SELECT * FROM plex_items WHERE active=1')]==['0','1']
    transport(monkeypatch,lambda r:httpx.Response(200,text='<html><body>Login required</body></html>'))
    with pytest.raises(i.IntegrationError,match='Invalid Plex'):
        i.sync_plex('1',lambda _:None)
    with connect() as db:
        assert db.execute('SELECT count(*) FROM plex_items WHERE active=1').fetchone()[0]==2
    transport(monkeypatch,lambda r:httpx.Response(200,text='<MediaContainer totalSize="2"><Video ratingKey="repeated" title="Repeated" year="2020"/></MediaContainer>'))
    with pytest.raises(i.IntegrationError,match='did not advance'):
        i.sync_plex('1',lambda _:None)
    with connect() as db:
        assert db.execute('SELECT count(*) FROM plex_items WHERE active=1').fetchone()[0]==2
    transport(monkeypatch,lambda r:httpx.Response(200,text='<MediaContainer size="0" totalSize="0"/>'))
    i.sync_plex('1',lambda _:None)
    with connect() as db:
        assert db.execute('SELECT count(*) FROM plex_items WHERE active=1').fetchone()[0]==0


def test_ratings_cache_and_identity(monkeypatch):
    config(omdb_key='test-key')
    with connect() as db:
        db.execute("INSERT INTO films(title,normalized,year) VALUES('Arrival','arrival',2016)")
        db.execute("INSERT INTO films(title,normalized,year) VALUES('Wrong Title','wrong title',2016)")
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'Response':'True','Title':'Arrival','Year':'2016','Genre':'Drama, Sci-Fi','Plot':'A linguist makes contact.','imdbRating':'7.9','imdbID':'tt2543164','Ratings':[{'Source':'Internet Movie Database','Value':'7.9/10'}]})
    transport(monkeypatch,handler)
    assert '1 matched, 1 unmatched' in i.enrich(lambda _:None)
    with connect() as db:
        rows=db.execute('SELECT * FROM films ORDER BY id').fetchall()
        assert rows[0]['rating']==7.9 and json.loads(rows[0]['genres'])==['Drama','Sci-Fi']
        assert rows[1]['rating'] is None and rows[1]['metadata_error']
    i.enrich(lambda _:None)
    assert len(calls)==2


def test_redirect_does_not_leak_credentials(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(302,headers={'location':'https://other.example/steal'})
    transport(monkeypatch,handler)
    with i.client() as http, pytest.raises(i.IntegrationError,match='another origin'):
        i.fetch(http,'https://plex.example/library/sections',headers={'X-Plex-Token':'private'})
    assert len(calls)==1
