# Movie Harbor

A local web application for discovering film releases and comparing them with a Plex movie library. Python 3.12+, FastAPI, SQLite, and a responsive vanilla JavaScript interface. No frontend build step or external database is needed.

## Start on your computer or NAS

```bash
bash scripts/setup.sh
bash scripts/start.sh
```

Open the app on port **8000** on the machine where it runs. The default listener is localhost. To access it from another machine on your LAN, set `HOST=0.0.0.0` and `APP_PASSWORD` securely in your service configuration before running `scripts/start.sh`; the login username is `admin`. Use an HTTPS reverse proxy for remote access. Run **one Uvicorn worker**; jobs are serialized in-process.

On Windows, create a virtual environment with `py -m venv .venv`, install `requirements.lock` using `.venv\Scripts\python -m pip install -r requirements.lock`, and launch `.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.

## Connect Plex

1. Open **Connections**, enter your Plex address (for example, `http://192.168.1.20:32400`) and Plex token.
2. In Plex Web, open a movie, choose **Get Info → View XML**, and copy only the `X-Plex-Token` value from the URL. See [Plex's official instructions](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).
3. Choose **Save & load libraries**, select a movie library, then **Save & sync Plex**.
4. Use **Plex library** to browse titles, years, genres, media paths, sizes, codecs, resolution, duration, bitrate, and audio details exposed by Plex.

The app must be able to reach Plex from its own host. Running on your computer/NAS on the same LAN is the recommended setup. A cloud installation needs a VPN or a reachable Plex endpoint; a private LAN address alone is insufficient. Plex synchronization paginates and retrieves the entire selected library before replacing its active snapshot. Removed items disappear from the active library after a successful sync; discovery records and manual flags remain. Multiple explicitly synced libraries can coexist. Tokens never go to the browser after saving.

## Discover films

**Scan sources** visits these four categories:

- https://devil-torrents.pl/files/torrent/Filmy%20x265/h265
- https://devil-torrents.pl/files/torrent/Filmy%20x264/h264
- https://devil-torrents.pl/files/torrent/Filmy%20XviD/DivX
- https://devil-torrents.pl/files/torrent/Filmy%20Blu-Ray/HD

The default cutoff is **strictly after October 20, 2026**: October 21 onward, using Warsaw calendar dates for relative labels such as “dzisiaj”. Scans start at page one each time, following next links and stopping at a wholly older page or the last page. The maximum page count is configurable. Reaching it is reported as incomplete. The one-second delay between pages limits request rate. This assumes the source's stated descending date order.

Each release records its title, source page URL, category, size, seeds, peers, source date, first seen, and last seen. Missing source values appear as unknown. The unique page URL prevents duplicates and rescans refresh availability without resetting flags or manual grouping. Results from successfully processed pages remain available even when a later page fails. Scans are user-triggered; no recurring scheduler is installed.

Films are grouped by normalized title and identical release year; conservative fuzzy matching applies only to longer titles, with protection for different sequel numbers. Ambiguous/localized titles may need **Correct grouping** on a release. Set its canonical title and year to attach it to an existing matching film or create a new group. Films with different years remain separate. Matching with Plex uses the same identity rules.

### Live-site validation limitation

The cloud environment returned **proxy HTTP 403** for the requested website. The current HTML adapter is tested against clearly labeled synthetic fixtures, **not a captured page from devil-torrents.pl**. Actual selectors/pagination may need adjustment after a real page is accessible. It supports table/torrent rows, `/torrent/` detail links, column headers or seed/peer classes, ISO/Polish numeric dates, relative dates, and next links. Unknown layouts/dates and login pages cause explicit failures, never a false successful empty scan. It does not bypass login or anti-bot challenges.

Custom cloud domain additions were saved for `devil-torrents.pl` and `www.omdbapi.com`; a draft save does not activate network access. Apply and publish the configuration through environment settings for subsequent cloud use. Local operation uses your normal network.

## Ratings and genres

Obtain and activate a free [OMDb API key](https://www.omdbapi.com/apikey.aspx), enter it in **Connections**, and select **Refresh ratings**. The free tier currently allows 1,000 daily requests; confirm current terms with OMDb. It provides IMDb ratings, plots, genres, and Rotten Tomatoes/Metacritic ratings when present. The app shows each rating's source and native scale. No rating is fabricated when a provider lacks one.

Lookups require a matching normalized title and year. Use the canonical OMDb title when a localized release title fails to match. Each refresh processes at most 200 stale/unprocessed films and caches successful and unmatched responses for 30 days. Run refresh again to process further batches. Invalid keys and quota failures are explicit. OMDb is optional: browsing, flags, scanning and Plex sync still work without it; ratings remain unavailable. Live provider behavior was not verified without a user-supplied key.

## Flags, browsing, and storage

- Filter by year, genre, text, category, and status; sort by source date, year, title, IMDb rating, or seeds.
- Mark a **film** or individual **release** as ignored, already owned, or for download.
- A release inherits film status unless overridden. An unmarked film currently in Plex is automatically owned.
- The download list contains films with a film/release download flag. It is a shortlist: opening a source page does not automatically download files or send anything to a torrent client.

SQLite data lives in `data/catalog.sqlite3`, outside Git. Stop the app before copying this file for a consistent backup. Credentials saved through the UI are stored in this database with file mode `0600`; the data directory is created with mode `0700`. They are not encrypted at rest. Use a private persistent volume on a NAS and protect backups. Environment variables override saved connection values:

| Variable | Purpose |
| --- | --- |
| `PLEX_URL` | Plex server base URL |
| `PLEX_TOKEN` | Plex access token |
| `OMDB_API_KEY` | OMDb API key |
| `APP_PASSWORD` | Optional HTTP Basic password (`admin` username); required by launcher for LAN binding |
| `MOVIE_DB` | Alternate SQLite path |
| `HOST`, `PORT` | Launcher bind address and port |

The app does not automatically load `.env` files. Supply variables through your shell/service manager. Do not commit secrets. To clear saved credentials, PUT an empty string for the corresponding field to `/api/settings`; an omitted/null field preserves the existing value. The UI leaves blank password inputs unchanged.

## Development and verification

```bash
.venv/bin/python -m pytest -q
node --check app/static/app.js
```

Tests exercise real SQLite persistence and HTTP routes, mocked HTTP integrations, cutoff boundaries, grouping, rescans, flags, Plex pagination and atomic replacement, ratings caching, and redirect credential isolation. Fixtures are synthetic and do not establish live-service compatibility.

Optional browser checks (requires an installed Chromium and Playwright):

```bash
.venv/bin/pip install playwright==1.51.0
.venv/bin/python scripts/browser_check.py
```

The browser check uses a temporary database and a separate local server. It never seeds sample data into your collection. API documentation is available at `/docs`. Runtime job history is stored in SQLite; interrupted jobs are marked at restart and can be run again. Dependencies are fully pinned in `requirements.lock`; `requirements.txt` records the direct dependencies.
