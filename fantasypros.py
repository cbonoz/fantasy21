# FantasyPros weekly projections (https://www.fantasypros.com/nfl/projections).
# Fetched from the standard projections pages (the /nfl/start/ tool is
# WAF-protected); cached per WEEK, mirroring the weather/spread cache pattern.
# Converted to FanDuel (non-PPR) scoring via projection.compute_fp_projection.

import json
import os
import time

import requests

import config
from nfl_teams import NFL_TEAM_MAP, normalize_player_name, normalize_team_abbr
from projection import compute_fp_projection

FANTASYPROS_BASE = 'https://www.fantasypros.com/nfl/projections'
FANTASYPROS_API_BASE = 'https://api.fantasypros.com/public/v2/json'
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

LAST_PROJECTION_STATUS = {
    'status': 'not_loaded',
    'requested_week': None,
    'source_week': None,
    'detail': 'not requested',
}


def get_projection_source_status():
    """Status/provenance for the most recent weekly projection load."""
    return dict(LAST_PROJECTION_STATUS)


def _cache_path(week):
    return f"{config.CACHE_FOLDER}/fantasypros_week_{week}.json"


def _status_cache_path(week):
    return f"{config.CACHE_FOLDER}/fantasypros_week_{week}.status.json"


def _save_projection_status(week, status, source_week, detail, method='html'):
    global LAST_PROJECTION_STATUS
    LAST_PROJECTION_STATUS = {
        'status': status,
        'requested_week': week,
        'source_week': source_week,
        'detail': detail,
        'method': method,
        'updated_at': time.time(),
    }
    with open(_status_cache_path(week), 'w') as f:
        json.dump(LAST_PROJECTION_STATUS, f)
    return LAST_PROJECTION_STATUS


def _fresh_cached_status(week, cache_file, cached):
    """Read source provenance and apply a short retry TTL to failed scrapes."""
    status_file = _status_cache_path(week)
    status = None
    if os.path.isfile(status_file):
        try:
            with open(status_file) as f:
                status = json.load(f)
        except (OSError, ValueError):
            status = None

    if status and status.get('requested_week') == week:
        age = time.time() - float(status.get('updated_at') or 0)
        ttl_hours = (config.FANTASYPROS_CACHE_MAX_AGE_HOURS
                     if status.get('status') == 'ok'
                     else config.FANTASYPROS_FAILURE_CACHE_HOURS)
        if age < ttl_hours * 60 * 60:
            return status

    # Older cache files predate provenance sidecars; retain them only within
    # the normal source TTL and label their provenance as legacy/current-week.
    if os.path.isfile(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if cached and age < config.FANTASYPROS_CACHE_MAX_AGE_HOURS * 60 * 60:
            return {
                'status': 'ok', 'requested_week': week, 'source_week': week,
                'detail': 'legacy current-week cache (no provenance metadata)',
                'method': 'html',
            }
        if not cached and age < config.FANTASYPROS_FAILURE_CACHE_HOURS * 60 * 60:
            return {
                'status': 'unavailable', 'requested_week': week,
                'source_week': None, 'detail': 'cached unavailable result (legacy)',
                'method': 'html',
            }
    return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_position(position, html, diagnostics=None):
    """Parse one position's projections table into normalized entries."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table')
    if not table:
        if diagnostics is not None:
            diagnostics.append('no HTML table found')
        return []
    entries = []
    player_rows = 0
    for tr in table.find_all('tr'):
        classes = tr.get('class') or []
        if not any(str(c).startswith('mpb-player-') for c in classes):
            continue
        player_rows += 1
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
    if diagnostics is not None and not entries:
        if not player_rows:
            diagnostics.append('table found, but no rows matched expected mpb-player-* class')
        else:
            diagnostics.append(f'{player_rows} expected player rows found, but none parsed')
    return entries


def _response_preview(html):
    """Short safe diagnostic for an unexpected HTML response (no scripts)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else '(no title)'
    text = ' '.join(soup.get_text(' ', strip=True).split())
    return f"title={title!r}; text={text[:100]!r}"


def _summarize_fetch_diagnostics(diagnostics, body_diagnostic=None):
    if diagnostics and all(
            'HTTP 200' in item and 'no HTML table found' in item
            for item in diagnostics):
        summary = f"{len(diagnostics)} position pages returned HTTP 200 without tables"
        if body_diagnostic:
            summary += f"; {body_diagnostic}"
        return summary
    summary = '; '.join(diagnostics[:2]) if diagnostics else 'no position rows parsed'
    if len(diagnostics) > 2:
        summary += f"; plus {len(diagnostics) - 2} other position errors"
    if body_diagnostic:
        summary += f"; {body_diagnostic}"
    return summary


def _flatten_api_stats(raw_stats):
    """Flatten the official API's one-or-more stat-object response shape."""
    if isinstance(raw_stats, dict):
        return raw_stats
    flattened = {}
    if isinstance(raw_stats, list):
        for item in raw_stats:
            if isinstance(item, dict):
                flattened.update(item)
    return flattened


def _fetch_api_projections(week, api_key):
    """Fetch weekly stats from FantasyPros' authenticated JSON API."""
    entries = []
    diagnostics = []
    for position in POSITIONS:
        url = f"{FANTASYPROS_API_BASE}/nfl/{int(config.SEASON_START.split('/')[-1])}/projections"
        try:
            response = requests.get(
                url,
                headers={'x-api-key': api_key, 'Accept': 'application/json'},
                params={'position': position, 'week': week},
                timeout=30,
            )
        except requests.RequestException as exc:
            diagnostics.append(f"{position}: API request error: {exc}")
            continue
        if response.status_code != 200:
            # Never print the key. A short body helps distinguish auth, quota,
            # and endpoint errors without dumping a full response.
            body = ' '.join((response.text or '').split())[:160]
            diagnostics.append(f"{position}: API HTTP {response.status_code}; {body}")
            continue
        try:
            payload = response.json()
        except ValueError as exc:
            diagnostics.append(f"{position}: API returned invalid JSON ({exc})")
            continue

        for player in payload.get('players', []):
            name = player.get('name') or player.get('player_name')
            team = normalize_team_abbr(player.get('team_id') or player.get('player_team_id') or '')
            player_pos = player.get('position_id') or position
            if player_pos == 'DEF':
                player_pos = 'DST'
            stats = _flatten_api_stats(player.get('stats'))
            projection_points = _to_float(stats.get('points'))
            if not name or not team or player_pos not in POSITIONS:
                continue
            entries.append({
                'position': player_pos,
                'name': normalize_player_name(name),
                'team': team,
                'stats': stats,
                'fpts': projection_points,
                'projection_points': projection_points,
            })

        if not payload.get('players'):
            diagnostics.append(f"{position}: API returned no players for week {week}")
    return entries, diagnostics


def get_projections(week):
    """FantasyPros projections for all positions, cached per week.

    Returns a previous week's cache only with explicit stale-source provenance.
    The caller must decide whether to use it; it is never cached as if it were
    the requested week's projection.
    """
    global LAST_PROJECTION_STATUS
    LAST_PROJECTION_STATUS = {
        'status': 'unavailable',
        'requested_week': week,
        'source_week': None,
        'detail': 'no current-week projection data',
        'method': None,
    }
    cache_file = _cache_path(week)

    stale_cache = None
    stale_method = 'html'
    api_key = os.environ.get('FANTASYPROS_API_KEY', '').strip()
    force_refresh = os.environ.get('FANTASYPROS_FORCE_REFRESH', '').strip().lower() in ('1', 'true', 'yes')
    if os.path.isfile(cache_file):
        with open(cache_file) as f:
            cached = json.load(f)
        cached_status = _fresh_cached_status(week, cache_file, cached)
        api_cache_mismatch = bool(api_key) and cached_status and cached_status.get('method') != 'api'
        if cached_status and not force_refresh and not api_cache_mismatch:
            LAST_PROJECTION_STATUS = cached_status
            if not cached:
                print('return cached unavailable data', 'fantasypros',
                      os.path.basename(cache_file), cached_status.get('detail'))
                return []
            print('return cached data', 'fantasypros', os.path.basename(cache_file),
                  cached_status.get('detail'))
            return cached
        stale_cache = cached or None
        if cached_status:
            stale_method = cached_status.get('method') or 'html'
        if force_refresh:
            print('forcing FantasyPros refresh', os.path.basename(cache_file))
        elif api_cache_mismatch:
            print('FantasyPros API key configured; bypassing non-API cache')
        else:
            print('stale or empty cache', os.path.basename(cache_file))

    method = 'api' if api_key else 'html'
    if api_key:
        entries, fetch_diagnostics = _fetch_api_projections(week, api_key)
        for diagnostic in fetch_diagnostics:
            print('fantasypros API diagnostic:', diagnostic)
    else:
        entries = []
        fetch_diagnostics = []
    body_diagnostic = None
    if not api_key:
        for position, slug in POSITIONS.items():
            url = f"{FANTASYPROS_BASE}/{slug}.php"
            params = {'week': week} if week else None
            print('fetching data', url)
            try:
                resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
            except requests.RequestException as exc:
                fetch_diagnostics.append(f"{position}: request error: {exc}")
                print('fantasypros fetch failed', slug, exc)
                continue
            if resp.status_code != 200:
                fetch_diagnostics.append(f"{position}: HTTP {resp.status_code} from {resp.url}")
                print('fantasypros fetch failed', slug, resp.status_code)
                continue
            parse_diagnostics = []
            position_entries = _parse_position(position, resp.text, parse_diagnostics)
            entries.extend(position_entries)
            if parse_diagnostics:
                final_url = getattr(resp, 'url', url)
                content_type = getattr(resp, 'headers', {}).get(
                    'Content-Type', 'unknown content type'
                )
                fetch_diagnostics.append(
                    f"{position}: HTTP 200 from {final_url}, {len(position_entries)} parsed; "
                    f"{parse_diagnostics[0]} ({content_type})"
                )
                if body_diagnostic is None:
                    body_diagnostic = _response_preview(resp.text)
                print('fantasypros parse issue', slug, final_url,
                      parse_diagnostics[0], content_type, body_diagnostic)
            else:
                print('fantasypros parsed', slug, len(position_entries), 'players')

    if entries:
        if not fetch_diagnostics:
            with open(cache_file, 'w') as f:
                json.dump(entries, f)
            detail = 'current-week fetch'
            _save_projection_status(week, 'ok', week, detail, method)
            print(f"Saved FantasyPros projections to {cache_file} ({len(entries)} players)")
        else:
            partial_detail = _summarize_fetch_diagnostics(
                fetch_diagnostics, body_diagnostic
            )
            _save_projection_status(
                week, 'degraded', week,
                f"partial current-week fetch; {partial_detail}",
                method,
            )
            print(f"FantasyPros partial fetch: {len(entries)} players; {partial_detail}")
        return entries

    # A stale cache for the requested week is safer than relabeling another
    # week's projections as current.
    if stale_cache:
        print(f"fantasypros refresh failed; returning stale current-week cache {os.path.basename(cache_file)}")
        _save_projection_status(
            week, 'degraded', week, 'stale current-week cache after refresh failure', stale_method
        )
        return stale_cache

    # Previous-week data is never relabeled as a current projection. Cache the
    # unavailable result briefly to avoid hammering a blocked page every run.
    previous_week_available = False
    if week and week > 1:
        prev_file = _cache_path(week - 1)
        if os.path.isfile(prev_file):
            previous_week_available = True
    with open(cache_file, 'w') as f:
        json.dump([], f)
    diagnostic_summary = _summarize_fetch_diagnostics(
        fetch_diagnostics, body_diagnostic
    )
    detail = f"current-week scrape unavailable; {diagnostic_summary}"
    if previous_week_available:
        detail += f"; week {week - 1} cache exists but is not used"
    _save_projection_status(week, 'unavailable', None, detail, method)
    print(f"fantasypros week {week} returned no current-week players; {detail}")
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
        fd_points = _to_float(entry.get('projection_points'))
        if fd_points <= 0:
            fd_points = compute_fp_projection(entry['position'], entry['stats'])
        if fd_points > 0:
            result[(entry['team'], entry['position'], entry['name'])] = fd_points
    return result
