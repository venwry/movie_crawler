import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def db_path():
    return Path(os.environ.get('MOVIE_DB', 'data/catalog.sqlite3'))


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=30)
    if os.name != 'nt':
        # Windows access is controlled by NTFS ACLs, not POSIX mode bits.
        os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS films(
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, normalized TEXT NOT NULL,
          year INTEGER, flag TEXT NOT NULL DEFAULT 'none', genres TEXT NOT NULL DEFAULT '[]',
          plot TEXT, rating REAL, ratings TEXT NOT NULL DEFAULT '[]', imdb_id TEXT,
          metadata_at TEXT, metadata_error TEXT);
        CREATE INDEX IF NOT EXISTS film_year ON films(year);
        CREATE TABLE IF NOT EXISTS releases(
          id INTEGER PRIMARY KEY, film_id INTEGER NOT NULL REFERENCES films(id),
          title TEXT NOT NULL, url TEXT UNIQUE NOT NULL, category TEXT NOT NULL,
          size TEXT, seeds INTEGER, peers INTEGER, recorded_at TEXT NOT NULL,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, flag TEXT NOT NULL DEFAULT 'none');
        CREATE TABLE IF NOT EXISTS plex_items(
          server TEXT NOT NULL, library TEXT NOT NULL, rating_key TEXT NOT NULL,
          film_id INTEGER NOT NULL REFERENCES films(id), title TEXT NOT NULL,
          details TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(server, library, rating_key));
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
          message TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT);
        ''')
        db.execute("UPDATE jobs SET state='interrupted',message='Application restarted; run this operation again.' WHERE state='running'")


def settings():
    with connect() as db:
        result = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
    for key, env in [('plex_url', 'PLEX_URL'), ('plex_token', 'PLEX_TOKEN'), ('omdb_key', 'OMDB_API_KEY')]:
        if os.environ.get(env):
            result[key] = os.environ[env]
    return result
