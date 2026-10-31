import pytest
from app.db import initialize


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('MOVIE_DB', str(tmp_path / 'test.sqlite3'))
    for name in ('PLEX_URL','PLEX_TOKEN','OMDB_API_KEY','APP_PASSWORD'):
        monkeypatch.delenv(name, raising=False)
    initialize()
