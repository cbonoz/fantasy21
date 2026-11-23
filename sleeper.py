# Sleeper weekly player projections (documented, free, no API key).
# Cached per week in the ranking/ folder, mirroring weather/spread caching.

import json
import os

import requests

import config
from nfl_teams import normalize_player_name, normalize_team_abbr

SLEEPER_BASE = 'https://api.sleeper.com'


def get_projections(week, season=None):
    """Raw Sleeper projections for a week, cached per week."""
    season = season or int(config.SEASON_START.split('/')[-1])
    cache_file = f"{config.CACHE_FOLDER}/sleeper_week_{week}.json"
    if config.cache_is_fresh(cache_file):
        print('return cached data', f"sleeper week {week}")
        with open(cache_file) as f:
            return json.load(f)
    if os.path.isfile(cache_file):
        print('stale cache; refreshing', f"sleeper week {week}")

    url = (f"{SLEEPER_BASE}/projections/nfl/{season}/{week}"
           "?season_type=regular&position[]=QB&position[]=RB&position[]=WR"
           "&position[]=TE&position[]=K&position[]=DEF")
    print('fetching data', url)
    data = requests.get(url, timeout=60).json()
    with open(cache_file, 'w') as f:
        json.dump(data, f)
    return data


def build_projection_map(week, season=None):
    """
    Map (team, position, normalized_full_name) -> FanDuel half-PPR projection.
    Prefer Sleeper's pts_half_ppr; convert pts_std by adding half a point per
    projected reception when the half-PPR field is unavailable. Team defenses
    are keyed by (team, 'DEF', '').
    """
    result = {}
    for entry in get_projections(week, season):
        stats = entry.get('stats') or {}
        player = entry.get('player') or {}
        position = player.get('position')
        half_ppr = stats.get('pts_half_ppr')
        pts = half_ppr if half_ppr is not None else stats.get('pts_std')
        try:
            pts = float(pts)
        except (TypeError, ValueError):
            continue
        if half_ppr is None and position in ('RB', 'WR', 'TE'):
            try:
                pts += 0.5 * float(stats.get('rec') or 0)
            except (TypeError, ValueError):
                pass
        team = normalize_team_abbr(entry.get('team'))
        if position == 'DEF':
            if team:
                result[(team, 'DEF', '')] = float(pts)
            continue
        full_name = f"{player.get('first_name')} {player.get('last_name')}".strip()
        if not full_name or not position or team == '':
            continue
        result[(team, position, normalize_player_name(full_name))] = float(pts)
    return result
