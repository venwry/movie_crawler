import re
import unicodedata
from difflib import SequenceMatcher

FLAGS = {'none', 'ignored', 'owned', 'download'}


def normalize(title):
    title = unicodedata.normalize('NFKD', title.casefold().replace('ł', 'l'))
    return ' '.join(re.findall(r'[a-z0-9]+', ''.join(c for c in title if not unicodedata.combining(c))))


def identify(title):
    matches = list(re.finditer(r'(?<!\d)((?:19|20)\d{2})(?!\d)', title))
    match = matches[-1] if matches else None
    year = int(match[1]) if match else None
    clean = title[:match.start()] if match else re.split(r'\b(?:2160p|1080p|720p|x265|x264|bluray|webrip)\b', title, flags=re.I)[0]
    clean = re.sub(r'[._]+', ' ', clean).strip(' []()-')
    return clean or title, year


def film_for(db, title, year):
    normalized = normalize(title)
    # Never fuzzy-merge unknown years or short titles. A year must agree exactly.
    rows = db.execute('SELECT id,normalized FROM films WHERE year IS ?', (year,)).fetchall()
    exact = [r for r in rows if r['normalized'] == normalized]
    if exact:
        return exact[0]['id']
    if year is not None and len(normalized) >= 12:
        candidates = []
        for row in rows:
            # Distinct sequel numbers must never be merged.
            if re.findall(r'\d+', row['normalized']) != re.findall(r'\d+', normalized):
                continue
            score = SequenceMatcher(None, normalized, row['normalized']).ratio()
            if score >= .94:
                candidates.append((score, row['id']))
        candidates.sort(reverse=True)
        if candidates and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= .03):
            return candidates[0][1]
    return db.execute('INSERT INTO films(title,normalized,year) VALUES(?,?,?)', (title, normalized, year)).lastrowid
