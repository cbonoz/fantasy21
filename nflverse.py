# Free, open-source NFL stats from nflverse (nflfastR data releases).
# All API downloads are cached per week in the ranking/ folder, mirroring
# the existing weather/spread cache pattern.

import json
import os
from collections import defaultdict

import pandas as pd

import config
from nfl_teams import normalize_player_name, normalize_team_abbr
from projection import compute_defense_fd_points, compute_fd_points

NFLVERSE_BASE = 'https://github.com/nflverse/nflverse-data/releases/download'
OFFENSIVE_POSITIONS = ('QB', 'RB', 'WR', 'TE')
DEFENSIVE_GROUPS = ('DL', 'LB', 'DB')


def _season():
    return int(config.SEASON_START.split('/')[-1])


def _cache_path(name, week):
    return f"{config.CACHE_FOLDER}/{name}_week_{week}.csv"


def get_weekly_player_stats(week, season=None):
    """Weekly player stats for the season, cached per week."""
    season = season or _season()
    cache_file = _cache_path('nflverse', week)
    if os.path.isfile(cache_file):
        print('return cached data', f"nflverse week {week}")
        return pd.read_csv(cache_file)
    url = f"{NFLVERSE_BASE}/stats_player/stats_player_week_{season}.csv"
    print('fetching data', url)
    df = pd.read_csv(url)
    df.to_csv(cache_file, index=False)
    return df


def get_snap_counts(week, season=None):
    """Season snap counts, cached per week."""
    season = season or _season()
    cache_file = _cache_path('snap_counts', week)
    if os.path.isfile(cache_file):
        print('return cached data', f"snap counts week {week}")
        return pd.read_csv(cache_file)
    url = f"{NFLVERSE_BASE}/snap_counts/snap_counts_{season}.csv"
    print('fetching data', url)
    df = pd.read_csv(url)
    df.to_csv(cache_file, index=False)
    return df


def prior_week_stats(week, season=None):
    """Stats from weeks strictly before the current one (don't peek at this week's games)."""
    df = get_weekly_player_stats(week, season)
    return df[df['week'].astype(int) < week]


def compute_fpa_map(week, season=None):
    """
    Fantasy points allowed per team per position, averaged over prior weeks.
    Returns {defense_team: {position: avg_fd_points_allowed}}.
    """
    cache_file = f"{config.CACHE_FOLDER}/fpa_week_{week}.json"
    if os.path.isfile(cache_file):
        print('return cached data', f"fpa week {week}")
        with open(cache_file) as f:
            return json.load(f)

    prior = prior_week_stats(week, season)
    fpa = defaultdict(lambda: defaultdict(list))
    for row in prior.itertuples(index=False):
        if row.position not in OFFENSIVE_POSITIONS:
            continue
        pts = compute_fd_points(row._asdict(), row.position)
        opp = normalize_team_abbr(row.opponent_team)
        fpa[opp][row.position].append(pts)

    result = {}
    for team, positions in fpa.items():
        result[team] = {pos: round(sum(v) / len(v), 3) for pos, v in positions.items() if v}

    with open(cache_file, 'w') as f:
        json.dump(result, f)
    return result


def compute_history(week, season=None):
    """
    Prior-week averages of FanDuel points per player (normalized name key) and
    per defense team (lowercase abbr key), matching draft.py's lookup keys.
    """
    prior = prior_week_stats(week, season)
    player_fd = defaultdict(list)
    team_fd = defaultdict(list)
    for row in prior.itertuples(index=False):
        if row.position in OFFENSIVE_POSITIONS:
            player_fd[normalize_player_name(row.player_display_name)].append(
                compute_fd_points(row._asdict(), row.position))
        elif row.position_group in DEFENSIVE_GROUPS:
            team_fd[normalize_team_abbr(row.team).lower()].append(
                compute_defense_fd_points(row._asdict()))

    history = {name: round(sum(v) / len(v), 3) for name, v in player_fd.items() if v}
    history.update({team: round(sum(v) / len(v), 3) for team, v in team_fd.items() if v})
    return history


def compute_qb_snap_share_map(week, season=None):
    """Avg offense snap share per (team, player) for QBs over prior weeks."""
    snaps = get_snap_counts(week, season)
    snaps = snaps[snaps['week'].astype(int) < week]
    if snaps.empty:
        return {}
    qb = snaps[(snaps['position'] == 'QB') & snaps['offense_pct'].notna()]
    acc = defaultdict(list)
    for row in qb.itertuples(index=False):
        acc[(normalize_team_abbr(row.team), normalize_player_name(row.player))].append(float(row.offense_pct))
    return {k: sum(v) / len(v) for k, v in acc.items()}