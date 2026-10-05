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

    Uses the standard FPA metric: all FanDuel points scored at a position by
    the opposing team in a single game are summed, then averaged per game.
    Summing per game (instead of averaging per player) avoids diluting a
    matchup when teams rotate multiple players at a position.

    Returns {defense_team: {position: avg_fd_points_allowed_per_game}}.
    """
    cache_file = f"{config.CACHE_FOLDER}/fpa_week_{week}.json"
    if os.path.isfile(cache_file):
        print('return cached data', f"fpa week {week}")
        with open(cache_file) as f:
            return json.load(f)

    prior = prior_week_stats(week, season)
    game_totals = defaultdict(float)
    for row in prior.itertuples(index=False):
        if row.position not in OFFENSIVE_POSITIONS:
            continue
        pts = compute_fd_points(row._asdict(), row.position)
        opp = normalize_team_abbr(row.opponent_team)
        game_totals[(opp, row.position, row.week)] += pts

    fpa = defaultdict(lambda: defaultdict(list))
    for (opp, pos, _week), total in game_totals.items():
        fpa[opp][pos].append(total)

    result = {}
    for team, positions in fpa.items():
        result[team] = {pos: round(sum(v) / len(v), 3) for pos, v in positions.items() if v}

    with open(cache_file, 'w') as f:
        json.dump(result, f)
    return result


def compute_giveaway_map(week, season=None):
    """
    Per-offense expected DST big-play points surrendered, from prior weeks:
    interceptions*2 + fumbles lost*2 + sacks allowed*1. A defense facing a
    giveaway-prone offense scores more. Returns {offense_team: avg_points}.

    Each team's raw rate is regressed toward the league mean (shrinkage) so a
    small, high-variance early-season sample doesn't overreact.
    """
    cache_file = f"{config.CACHE_FOLDER}/giveaway_week_{week}.json"
    if os.path.isfile(cache_file):
        print('return cached data', f"giveaway week {week}")
        with open(cache_file) as f:
            return json.load(f)

    prior = prior_week_stats(week, season)
    per_game = defaultdict(lambda: defaultdict(float))
    for row in prior.itertuples(index=False):
        team = normalize_team_abbr(row.team)
        if not team:
            continue
        ints = float(row.passing_interceptions or 0)
        fumbles = float(row.fumbles_lost_total or 0)
        sacks = float(row.sacks_suffered or 0)
        per_game[row.week][team] += 2 * ints + 2 * fumbles + sacks

    acc = defaultdict(list)
    for _week, teams in per_game.items():
        for team, pts in teams.items():
            if pts > 0:
                acc[team].append(pts)
    raw = {team: sum(v) / len(v) for team, v in acc.items() if v}
    if not raw:
        return {}
    league = sum(raw.values()) / len(raw)
    k = config.GIVEAWAY_SHRINKAGE_GAMES
    result = {
        team: round((len(acc[team]) * rate + k * league) / (len(acc[team]) + k), 3)
        for team, rate in raw.items()
    }
    with open(cache_file, 'w') as f:
        json.dump(result, f)
    return result


def compute_history(week, season=None):
    """
    Prior-week averages of FanDuel points per player (normalized name key) and
    per defense team (lowercase abbr key), matching draft.py's lookup keys.
    Defensive points are summed per game (not averaged per player row) so
    multi-defender rotations don't dilute a team's defensive scoring.
    """
    prior = prior_week_stats(week, season)
    player_fd = defaultdict(list)
    team_fd = defaultdict(lambda: defaultdict(float))
    for row in prior.itertuples(index=False):
        if row.position in OFFENSIVE_POSITIONS:
            player_fd[normalize_player_name(row.player_display_name)].append(
                compute_fd_points(row._asdict(), row.position))
        elif row.position_group in DEFENSIVE_GROUPS:
            team = normalize_team_abbr(row.team).lower()
            team_fd[team][row.week] += compute_defense_fd_points(row._asdict())

    history = {name: round(sum(v) / len(v), 3) for name, v in player_fd.items() if v}
    for team, weeks in team_fd.items():
        if weeks:
            history[team] = round(sum(weeks.values()) / len(weeks), 3)
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