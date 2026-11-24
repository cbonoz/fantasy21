# Free, open-source NFL stats from nflverse (nflfastR data releases).
# All API downloads are cached per week in the ranking/ folder, mirroring
# the existing weather/spread cache pattern.

import json
import os
from collections import defaultdict

import pandas as pd

import config
from nfl_teams import normalize_player_name, normalize_team_abbr
from projection import (
    compute_defense_fd_points,
    compute_fd_points,
    compute_recent_form_adjustment,
    compute_role_competition_penalty,
)

NFLVERSE_BASE = 'https://github.com/nflverse/nflverse-data/releases/download'
OFFENSIVE_POSITIONS = ('QB', 'RB', 'WR', 'TE', 'K')
DEFENSIVE_GROUPS = ('DL', 'LB', 'DB')


def _season():
    return int(config.SEASON_START.split('/')[-1])


def _cache_path(name, week):
    return f"{config.CACHE_FOLDER}/{name}_week_{week}.csv"


def get_weekly_player_stats(week, season=None):
    """Weekly player stats for the season, cached per week."""
    season = season or _season()
    cache_file = _cache_path('nflverse', week)
    if config.cache_is_fresh(cache_file):
        print('return cached data', f"nflverse week {week}")
        return pd.read_csv(cache_file, low_memory=False)
    if os.path.isfile(cache_file):
        print('stale cache; refreshing', f"nflverse week {week}")
    url = f"{NFLVERSE_BASE}/stats_player/stats_player_week_{season}.csv"
    print('fetching data', url)
    df = pd.read_csv(url, low_memory=False)
    df.to_csv(cache_file, index=False)
    return df


def get_snap_counts(week, season=None):
    """Season snap counts, cached per week."""
    season = season or _season()
    cache_file = _cache_path('snap_counts', week)
    if config.cache_is_fresh(cache_file):
        print('return cached data', f"snap counts week {week}")
        return pd.read_csv(cache_file)
    if os.path.isfile(cache_file):
        print('stale cache; refreshing', f"snap counts week {week}")
    url = f"{NFLVERSE_BASE}/snap_counts/snap_counts_{season}.csv"
    print('fetching data', url)
    df = pd.read_csv(url)
    df.to_csv(cache_file, index=False)
    return df


def prior_week_stats(week, season=None):
    """Stats from weeks strictly before the current one (don't peek at this week's games)."""
    df = get_weekly_player_stats(week, season)
    return df[df['week'].astype(int) < week]


def compute_recent_target_share_map(week, recent_weeks=None, season=None):
    """Recent per-game target share by team/player for WRs, TEs, and RBs."""
    recent_weeks = recent_weeks or config.UPSIDE_RECENT_GAMES
    prior = prior_week_stats(week, season)
    recent = prior[prior['week'].astype(int) >= max(1, week - recent_weeks)]
    shares = defaultdict(list)
    for row in recent.itertuples(index=False):
        target_share = row._asdict().get('target_share')
        if row.position not in ('WR', 'TE', 'RB') or pd.isna(target_share):
            continue
        team = normalize_team_abbr(row.team)
        name = normalize_player_name(row.player_display_name)
        if team and name:
            shares[(team, name)].append(float(target_share))
    return {key: sum(values) / len(values) for key, values in shares.items() if values}


def compute_recent_opportunity_map(week, recent_weeks=None, season=None):
    """Recent touches/targets and snap share by player, team, and position.

    RB opportunity is carries plus targets; WR/TE opportunity is targets.
    Per-game rates use games with a recorded player row, while opportunity
    share uses each player's total in the recent window. The map is strictly
    prior-week data, so it can inform a current injury-driven role change
    without leaking this week's results.
    """
    recent_weeks = recent_weeks or config.UPSIDE_RECENT_GAMES
    recent_start = max(1, week - recent_weeks)
    try:
        stats = prior_week_stats(week, season)
        recent_stats = stats[stats['week'].astype(int) >= recent_start]
    except Exception as exc:
        print(f'Could not load recent player opportunities: {exc}')
        recent_stats = pd.DataFrame()
    usage = defaultdict(lambda: {'opportunities': 0.0, 'games': 0, 'points': 0.0})

    def number(row, field):
        try:
            value = float(row.get(field, 0) or 0)
            return value if pd.notna(value) else 0.0
        except (TypeError, ValueError):
            return 0.0

    for row in recent_stats.to_dict('records'):
        pos = row.get('position')
        if pos not in ('RB', 'WR', 'TE'):
            continue
        team = normalize_team_abbr(row.get('team'))
        name = normalize_player_name(row.get('player_display_name'))
        if not team or not name:
            continue
        opportunities = number(row, 'targets')
        if pos == 'RB':
            opportunities += number(row, 'carries')
        entry = usage[(team, pos, name)]
        entry['opportunities'] += opportunities
        entry['games'] += 1
        entry['points'] += compute_fd_points(row, pos)

    snap_totals = defaultdict(lambda: {'share': 0.0, 'games': 0})
    try:
        snaps = get_snap_counts(week, season)
        snaps = snaps[
            (snaps['week'].astype(int) >= recent_start)
            & (snaps['week'].astype(int) < week)
            & snaps['position'].isin(('RB', 'WR', 'TE'))
            & snaps['offense_pct'].notna()
        ]
        for row in snaps.to_dict('records'):
            team = normalize_team_abbr(row.get('team'))
            name = normalize_player_name(row.get('player'))
            if not team or not name:
                continue
            entry = snap_totals[(team, row['position'], name)]
            entry['share'] += number(row, 'offense_pct')
            entry['games'] += 1
    except Exception as exc:
        print(f'Could not load recent offensive snap shares: {exc}')

    team_totals = defaultdict(float)
    for (team, pos, _), entry in usage.items():
        team_totals[(team, pos)] += entry['opportunities']

    result = {}
    for key in set(usage) | set(snap_totals):
        entry = usage.get(key, {'opportunities': 0.0, 'games': 0, 'points': 0.0})
        snaps = snap_totals.get(key, {})
        games = entry['games']
        opportunities = entry['opportunities']
        result[key] = {
            'opportunities': opportunities,
            'opportunities_per_game': opportunities / games if games else 0.0,
            'opportunity_share': opportunities / team_totals[key[:2]]
            if team_totals[key[:2]] > 0 else 0.0,
            'points_per_opportunity': entry['points'] / opportunities
            if opportunities > 0 else 0.0,
            'snap_share': snaps.get('share', 0.0) / snaps.get('games', 1)
            if snaps.get('games', 0) else 0.0,
            'games': max(games, snaps.get('games', 0)),
        }
    return result


def compute_returning_competition_map(week, available_players=None, season=None):
    """Estimate role loss when an available teammate returns after an absence.

    Compare each teammate's opportunity share in games shared with the returner
    against games during the absence. Require multiple games in both samples and
    a meaningful returner role; otherwise make no adjustment.
    """
    try:
        stats = prior_week_stats(week, season)
    except Exception as exc:
        print(f'Could not load returning-teammate role data: {exc}')
        return {}
    if stats.empty:
        return {}

    def number(value):
        try:
            value = float(value)
            return max(value, 0.0) if pd.notna(value) else 0.0
        except (TypeError, ValueError):
            return 0.0

    weekly = defaultdict(lambda: defaultdict(float))
    for row in stats[stats['position'].isin(('RB', 'WR', 'TE'))].to_dict('records'):
        team = normalize_team_abbr(row.get('team'))
        pos = row.get('position')
        name = normalize_player_name(row.get('player_display_name'))
        try:
            game_week = int(row.get('week'))
        except (TypeError, ValueError):
            continue
        if team and name:
            targets = number(row.get('targets'))
            carries = number(row.get('carries')) if pos == 'RB' else 0.0
            weekly[(team, pos)][(game_week, name)] += targets + carries

    available = defaultdict(dict)
    if isinstance(available_players, dict):
        available_entries = available_players.items()
    else:
        available_entries = ((key, 1.0) for key in available_players or ())
    for (team, pos, name), weight in available_entries:
        try:
            weight = min(max(float(weight), 0.0), 1.0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight:
            available[(team, pos)][name] = weight

    results = {}
    for role, player_weeks in weekly.items():
        team, pos = role
        game_weeks = sorted({w for w, _ in player_weeks})
        if len(game_weeks) < 4:
            continue
        latest_week = game_weeks[-1]

        def team_total(game_week):
            return sum(value for (w, _), value in player_weeks.items()
                       if w == game_week)

        for returner, availability in available.get(role, {}).items():
            appearances = sorted(
                w for (w, name), opportunities in player_weeks.items()
                if name == returner and opportunities > 0
            )
            if len(appearances) < 2:
                continue

            gaps = [
                (before, after) for before, after in zip(appearances, appearances[1:])
                if after - before >= config.RETURNING_ROLE_MIN_ABSENCE_WEEKS
            ]
            if gaps:
                absence_start, return_week = gaps[-1]
                # Don't keep applying an old return adjustment after the role
                # has had several games to settle.
                if latest_week - return_week > config.RETURNING_ROLE_HISTORY_GAMES:
                    continue
                absence_games = [w for w in game_weeks if absence_start < w < return_week]
                prospective = False
            else:
                absence_start = appearances[-1]
                if latest_week - absence_start < config.RETURNING_ROLE_MIN_ABSENCE_WEEKS:
                    continue
                absence_games = [w for w in game_weeks if w > absence_start]
                prospective = True

            shared_games = appearances[-config.RETURNING_ROLE_HISTORY_GAMES:]
            absence_games = absence_games[-config.RETURNING_ROLE_HISTORY_GAMES:]
            if len(shared_games) < 2 or len(absence_games) < 2:
                continue
            returner_shares = [
                player_weeks[(w, returner)] / team_total(w)
                for w in shared_games if team_total(w) > 0
            ]
            returner_share = (
                sum(returner_shares) / len(returner_shares) if returner_shares else 0.0
            )
            if returner_share < config.RETURNING_ROLE_MIN_SHARE:
                continue

            for teammate in available.get(role, {}).keys() - {returner}:
                shared_appearances = sum(
                    player_weeks.get((w, teammate), 0.0) > 0 for w in shared_games
                )
                absent_appearances = sum(
                    player_weeks.get((w, teammate), 0.0) > 0 for w in absence_games
                )
                if shared_appearances < 2 or absent_appearances < 2:
                    continue
                shared_share = sum(
                    player_weeks.get((w, teammate), 0.0) / team_total(w)
                    for w in shared_games if team_total(w) > 0
                ) / len(shared_games)
                absent_share = sum(
                    player_weeks.get((w, teammate), 0.0) / team_total(w)
                    for w in absence_games if team_total(w) > 0
                ) / len(absence_games)
                penalty = compute_role_competition_penalty(
                    shared_share,
                    absent_share,
                    returner_share,
                    config.RETURNING_ROLE_MIN_SHARE_DROP,
                    config.RETURNING_ROLE_MIN_SHARE,
                    config.RETURNING_ROLE_POINTS_PER_SHARE,
                    config.RETURNING_ROLE_MAX_PENALTY,
                )
                penalty *= availability
                key = (team, pos, teammate)
                if penalty > results.get(key, {}).get('penalty', 0.0):
                    results[key] = {
                        'penalty': penalty,
                        'recent_share': shared_share,
                        'earlier_share': absent_share,
                        'returning_teammate': returner,
                        'returning_teammate_share': returner_share,
                        'games': min(shared_appearances, absent_appearances),
                        'prospective': prospective,
                    }
    return results

def compute_target_share_trend_map(week, recent_weeks=None, season=None):
    """Recent vs. earlier target share by (team, normalized player name)."""
    recent_weeks = recent_weeks or config.UPSIDE_RECENT_GAMES
    prior = prior_week_stats(week, season)
    recent_start = max(1, week - recent_weeks)
    shares = defaultdict(lambda: {'recent': [], 'earlier': []})
    for row in prior.itertuples(index=False):
        target_share = row._asdict().get('target_share')
        if row.position not in ('WR', 'TE', 'RB') or pd.isna(target_share):
            continue
        team = normalize_team_abbr(row.team)
        name = normalize_player_name(row.player_display_name)
        if not team or not name:
            continue
        period = 'recent' if int(row.week) >= recent_start else 'earlier'
        shares[(team, name)][period].append(float(target_share))

    trends = {}
    for key, periods in shares.items():
        recent_values, earlier_values = periods['recent'], periods['earlier']
        if len(recent_values) < 3 or len(earlier_values) < 2:
            continue
        recent = sum(recent_values) / len(recent_values)
        earlier = sum(earlier_values) / len(earlier_values)
        trends[key] = {
            'recent': recent,
            'earlier': earlier,
            'delta': recent - earlier,
        }
    return trends


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
    if config.cache_is_fresh(cache_file):
        print('return cached data', f"fpa week {week}")
        with open(cache_file) as f:
            return json.load(f)
    if os.path.isfile(cache_file):
        print('stale cache; refreshing', f"fpa week {week}")

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
    if config.cache_is_fresh(cache_file):
        print('return cached data', f"giveaway week {week}")
        with open(cache_file) as f:
            return json.load(f)
    if os.path.isfile(cache_file):
        print('stale cache; refreshing', f"giveaway week {week}")

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


def compute_upside_map(week, season=None):
    """
    Per-player upside: a shrinkage blend of the season and recent high-percentile
    FanDuel scores (falling back to the average). Used to tilt projections
    toward ceiling in GPP mode. Interpolated percentiles and sample-size
    shrinkage keep short recent samples from collapsing to a single boom game.
    Returns {normalized_name: ceiling_points}.
    """
    prior = prior_week_stats(week, season)
    acc = defaultdict(list)
    for row in prior.itertuples(index=False):
        if row.position in OFFENSIVE_POSITIONS:
            acc[normalize_player_name(row.player_display_name)].append((
                int(row.week),
                compute_fd_points(row._asdict(), row.position),
            ))
    upside = {}
    recent_games = config.UPSIDE_RECENT_GAMES
    for name, records in acc.items():
        if not records:
            continue
        records.sort(key=lambda item: item[0])
        vals = [score for _, score in records]
        avg = sum(vals) / len(vals)
        pct = float(pd.Series(vals).quantile(config.GPP_UPSIDE_PERCENTILE))
        ceiling = max(pct, avg)

        # Keep the full-season tail as an anchor, but let the most recent
        # appearances move it in either direction. Shrink the recent tail
        # estimate because four games are still a small sample.
        recent = records[-recent_games:]
        earlier = records[:-recent_games]
        if (len(recent) >= config.RECENT_FORM_MIN_GAMES
                and len(earlier) >= config.RECENT_FORM_MIN_PRIOR_GAMES):
            recent_scores = [score for _, score in recent]
            recent_avg = sum(recent_scores) / len(recent_scores)
            recent_ceiling = max(
                float(pd.Series(recent_scores).quantile(config.GPP_UPSIDE_PERCENTILE)),
                recent_avg,
            )
            recent_reliability = len(recent_scores) / (
                len(recent_scores) + config.RECENT_CEILING_PRIOR_GAMES
            )
            recent_weight = config.RECENT_CEILING_WEIGHT * recent_reliability
            ceiling = (1 - recent_weight) * ceiling + recent_weight * recent_ceiling

        upside[name] = round(ceiling, 3)
    return upside


def compute_score_distributions(week, season=None):
    """Per-game FanDuel score samples from prior weeks, for tail simulation.

    Returns (player_scores, defense_scores):
      player_scores  : {normalized_name: [fd_points, ...]} for offensive players
      defense_scores : {team_abbr_lower: [fd_points, ...]} per team defense game

    compute_upside_map collapses these to a single 75th percentile, which is
    enough for a ceiling number but not for estimating a lineup's chance of
    winning a tournament: that needs the shape of the distribution (variance,
    fat tails), not one quantile.
    """
    prior = prior_week_stats(week, season)
    player_scores = defaultdict(list)
    defense_weeks = defaultdict(lambda: defaultdict(float))
    for row in prior.itertuples(index=False):
        if row.position in OFFENSIVE_POSITIONS:
            player_scores[normalize_player_name(row.player_display_name)].append(
                compute_fd_points(row._asdict(), row.position))
        elif row.position_group in DEFENSIVE_GROUPS:
            team = normalize_team_abbr(row.team).lower()
            defense_weeks[team][int(row.week)] += compute_defense_fd_points(row._asdict())
    defense_scores = {team: list(weeks.values()) for team, weeks in defense_weeks.items()}
    return dict(player_scores), defense_scores


def compute_defense_upside_map(week, season=None):
    """Per-team DST ceiling uplift from the big-play tail, above its mean.

    Defense history here excludes points-allowed scoring, so return only the
    high-percentile surplus. The caller adds that surplus to its full-scoring
    FanDuel/Sleeper baseline rather than treating big plays as the whole DST
    projection.
    """
    _, defense_scores = compute_score_distributions(week, season)
    result = {}
    for team, values in defense_scores.items():
        if not values:
            continue
        scores = pd.Series(values, dtype=float)
        percentile = float(scores.quantile(config.GPP_UPSIDE_PERCENTILE))
        mean = float(scores.mean())
        result[team] = round(max(percentile - mean, 0.0), 3)
    return result


def compute_history(week, season=None):
    """
    Prior-week averages of FanDuel points per player (normalized name key) and
    per defense team (lowercase abbr key), matching draft.py's lookup keys.
    Recent games are weighted more (exponential decay) so current form matters
    more than early-season spikes. Defensive points are summed per game (not
    averaged per player row) so multi-defender rotations don't dilute a team's
    defensive scoring.
    """
    prior = prior_week_stats(week, season)
    player_fd = defaultdict(list)
    team_fd = defaultdict(lambda: defaultdict(float))
    for row in prior.itertuples(index=False):
        if row.position in OFFENSIVE_POSITIONS:
            player_fd[normalize_player_name(row.player_display_name)].append(
                (int(row.week), compute_fd_points(row._asdict(), row.position)))
        elif row.position_group in DEFENSIVE_GROUPS:
            team = normalize_team_abbr(row.team).lower()
            team_fd[team][row.week] += compute_defense_fd_points(row._asdict())

    def recency_weighted(values):
        total = sum(v * config.HISTORY_RECENCY_DECAY ** (week - wk) for wk, v in values)
        denom = sum(config.HISTORY_RECENCY_DECAY ** (week - wk) for wk, v in values)
        return total / denom if denom else 0.0

    history = {name: round(recency_weighted(v), 3) for name, v in player_fd.items() if v}
    for team, weeks in team_fd.items():
        if weeks:
            history[team] = round(sum(v * config.HISTORY_RECENCY_DECAY ** (week - wk) for wk, v in weeks.items())
                                  / sum(config.HISTORY_RECENCY_DECAY ** (week - wk) for wk in weeks), 3)
    return history


def compute_recent_form_map(week, recent_games=None, season=None):
    """Recent median scoring adjustment by normalized player name.

    Uses game appearances (not calendar weeks) so bye weeks do not make the
    recent sample stale. The adjustment is shrunk and capped in pure projection
    math before it can affect a full-slate recommendation.
    """
    recent_games = recent_games or config.RECENT_FORM_GAMES
    prior = prior_week_stats(week, season)
    scores_by_player = defaultdict(list)
    for row in prior.itertuples(index=False):
        if row.position not in ('QB', 'RB', 'WR', 'TE'):
            continue
        name = normalize_player_name(row.player_display_name)
        if not name:
            continue
        scores_by_player[name].append((
            int(row.week), compute_fd_points(row._asdict(), row.position)
        ))

    result = {}
    for name, records in scores_by_player.items():
        records.sort(key=lambda item: item[0])
        season_scores = [score for _, score in records]
        recent_scores = season_scores[-recent_games:]
        adjustment = compute_recent_form_adjustment(
            recent_scores,
            season_scores,
            config.RECENT_FORM_WEIGHT,
            config.RECENT_FORM_PRIOR_GAMES,
            config.MAX_RECENT_FORM_ADJUSTMENT,
            config.RECENT_FORM_MIN_GAMES,
            config.RECENT_FORM_MIN_PRIOR_GAMES,
        )
        enough_history = (
            len(recent_scores) >= config.RECENT_FORM_MIN_GAMES
            and len(season_scores) - len(recent_scores)
            >= config.RECENT_FORM_MIN_PRIOR_GAMES
        )
        if enough_history:
            result[name] = {
                'adjustment': adjustment,
                'recent_median': round(float(pd.Series(recent_scores).median()), 3),
                'season_median': round(float(pd.Series(season_scores).median()), 3),
                'recent_games': len(recent_scores),
                'total_games': len(season_scores),
            }
    return result


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
