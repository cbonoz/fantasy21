# FantasyPros weekly projections (https://www.fantasypros.com/nfl/projections).
# Fetched from the standard projections pages (the /nfl/start/ tool is
# WAF-protected); cached per WEEK, mirroring the weather/spread cache pattern.
# Converted to FanDuel (non-PPR) scoring via projection.compute_fp_projection.

import json
import os

import requests

import config
from nfl_teams import NFL_TEAM_MAP, normalize_player_name, normalize_team_abbr
from projection import compute_fp_projection

FANTASYPROS_BASE = 'https://www.fantasypros.com/nfl/projections'
POSITIONS = {'QB': 'qb', 'RB': 'rb', 'WR': 'wr', 'TE': 'te', 'K': 'k', 'DST': 'dst'}

# Stat columns after the player cell (which embeds the team abbreviation).
# 'fpts' is FantasyPros' own projection.
POS_LAYOUT = {
    'QB': ['pass_att', 'pass_cmp', 'pass_yds', 'pass_tds', 'pass_ints',
           'rush_att', 'rush_yds', 'rush_tds', 'fl', 'fpts'],
    'RB': ['rush_att', 'rush_yds', 'rush_tds', 'rec_rec', 'rec_yds', 'rec_tds', 'fl', 'fpts'],
    'WR': ['rec_rec', 'rec_yds', 'rec_tds', 'rush_att', 'rush_yds', 'rush_tds', 'fl', 'fpts'],
    'TE': ['rec_rec', 'rec_yds', 'rec_tds', 'fl', 'fpts'],
    'K': ['fg', 'fga', 'xpt', 'fpts'],
    'DST': ['sack', 'int', 'fr', 'ff', 'td', 'safety', 'pa', 'yds_agn', 'fpts'],
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}


def _cache_path(week):
    return f"{config.CACHE_FOLDER}/fantasypros_week_{week}.json"


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_position(position, html):
    """Parse one position's projections table into normalized entries."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table')
    if not table:
        return []
    entries = []
    for tr in table.find_all('tr'):
        classes = tr.get('class') or []
        if not any(str(c).startswith('mpb-player-') for c in classes):
            continue
        cells = tr.find_all('td')
        if len(cells) < 2:
            continue
        # Player name lives in the <a>; team abbreviation is trailing text.
        td0 = cells[0]
        a = td0.find('a')
        name = a.get_text(strip=True) if a else td0.get_text(strip=True)
        if not name:
            continue
        full_text = td0.get_text(strip=True)
        team_text = full_text[len(name):].strip()
        if position == 'DST':
            team_abbr = normalize_team_abbr(NFL_TEAM_MAP.get(name, team_text))
        else:
            team_abbr = normalize_team_abbr(team_text)
        stats = {}
        for i, key in enumerate(POS_LAYOUT[position]):
            idx = 1 + i
            if idx < len(cells):
                stats[key] = _to_float(cells[idx].get_text(strip=True))
        entries.append({
            'position': position,
            'name': normalize_player_name(name),
            'team': team_abbr,
            'stats': stats,
            'fpts': stats.get('fpts', 0.0),
        })
    return entries


def get_projections(week):
    """FantasyPros projections for all positions, cached per week.

    Falls back to the previous week's cache when the requested week's scrape
    yields no players (e.g. layout/WAF issues) and caches that fallback, so the
    pipeline keeps a projection source without re-fetching on every run. Delete
    the cache file to force a fresh scrape.
    """
    cache_file = _cache_path(week)

    if os.path.isfile(cache_file):
        with open(cache_file) as f:
            cached = json.load(f)
        if cached:
            print('return cached data', 'fantasypros', os.path.basename(cache_file))
            return cached
        print('empty cache', os.path.basename(cache_file))

    entries = []
    for position, slug in POSITIONS.items():
        url = f"{FANTASYPROS_BASE}/{slug}.php"
        params = {'week': week} if week else None
        print('fetching data', url)
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if resp.status_code != 200:
            print('fantasypros fetch failed', slug, resp.status_code)
            continue
        entries.extend(_parse_position(position, resp.text))

    if entries:
        with open(cache_file, 'w') as f:
            json.dump(entries, f)
        print(f"Saved FantasyPros projections to {cache_file} ({len(entries)} players)")
        return entries

    # Nothing usable for this week: prefer last week's projections over zero,
    # and cache the fallback so we don't re-fetch on every run.
    if week and week > 1:
        prev_file = _cache_path(week - 1)
        if os.path.isfile(prev_file):
            with open(prev_file) as f:
                fallback = json.load(f)
            if fallback:
                with open(cache_file, 'w') as f:
                    json.dump(fallback, f)
                print(f"fantasypros week {week} returned no players; cached week {week - 1} projections as fallback")
                return fallback

    if os.path.isfile(cache_file):
        os.remove(cache_file)
    print(f"fantasypros week {week} returned no players and no fallback available")
    return []


def build_projection_map(week):
    """
    Map (team, position, normalized_name) -> FanDuel-adjusted projection.
    Defenses keyed by (team, 'D', '') using FantasyPros' own DST projection
    (which bakes in points-allowed scoring).
    """
    result = {}
    for entry in get_projections(week):
        if entry['position'] == 'DST':
            result[(entry['team'], 'D', '')] = entry['fpts']
            continue
        fd_points = compute_fp_projection(entry['position'], entry['stats'])
        if fd_points > 0:
            result[(entry['team'], entry['position'], entry['name'])] = fd_points
    return result