# Pure projection math - no I/O, no module-level side effects.
# Shared by draft.py and the test suite.


def calculate_wind_factor(wind_speed, pos):
    """Wind adjustment factor by position (<1 penalty, >1 bonus, 1 neutral)."""
    if not wind_speed or wind_speed != wind_speed or wind_speed < 2:
        return 1.0

    if pos == 'QB':
        if wind_speed < 5:
            return 1.0
        elif wind_speed < 10:
            return 0.97
        elif wind_speed < 15:
            return 0.94
        elif wind_speed < 20:
            return 0.90
        else:
            return 0.85
    elif pos == 'WR':
        if wind_speed < 5:
            return 1.0
        elif wind_speed < 10:
            return 0.96
        elif wind_speed < 15:
            return 0.92
        elif wind_speed < 20:
            return 0.87
        else:
            return 0.80
    elif pos == 'TE':
        if wind_speed < 5:
            return 1.0
        elif wind_speed < 10:
            return 0.97
        elif wind_speed < 15:
            return 0.94
        elif wind_speed < 20:
            return 0.89
        else:
            return 0.83
    elif pos == 'RB':
        if wind_speed < 5:
            return 1.0
        elif wind_speed < 10:
            return 1.02
        elif wind_speed < 15:
            return 1.04
        elif wind_speed < 20:
            return 1.05
        else:
            return 1.06
    elif pos == 'K':
        if wind_speed < 5:
            return 1.0
        elif wind_speed < 10:
            return 0.98
        elif wind_speed < 15:
            return 0.95
        elif wind_speed < 20:
            return 0.88
        else:
            return 0.75
    elif pos in ('D', 'MVP'):
        if wind_speed < 5:
            return 1.0
        elif wind_speed < 10:
            return 1.01
        elif wind_speed < 15:
            return 1.02
        elif wind_speed < 20:
            return 1.03
        else:
            return 1.04

    return 1.0


def calculate_temperature_factor(temp, pos):
    """Temperature adjustment factor by position."""
    if not temp or temp != temp or temp < -10 or temp > 130:
        return 1.0

    if pos == 'QB':
        if temp < 20:
            return 0.96
        elif temp < 35:
            return 0.98
        elif temp < 55:
            return 0.99
        elif temp < 75:
            return 1.0
        elif temp < 90:
            return 1.02
        else:
            return 1.03
    elif pos in ['WR', 'TE']:
        if temp < 20:
            return 0.97
        elif temp < 35:
            return 0.99
        elif temp < 55:
            return 0.99
        elif temp < 75:
            return 1.0
        elif temp < 90:
            return 1.02
        else:
            return 1.01
    elif pos == 'RB':
        if temp < 20:
            return 1.02
        elif temp < 35:
            return 1.01
        elif temp < 55:
            return 1.0
        elif temp < 75:
            return 0.99
        elif temp < 90:
            return 0.98
        else:
            return 0.97
    elif pos == 'K':
        if temp < 0:
            return 0.98
        elif temp < 20:
            return 0.99
        elif temp < 100:
            return 1.0
        else:
            return 0.99
    elif pos in ('D', 'MVP'):
        if temp < 35:
            return 1.02
        elif temp < 55:
            return 1.01
        elif temp < 75:
            return 1.0
        elif temp < 90:
            return 0.98
        else:
            return 0.96

    return 1.0


def calculate_precipitation_factor(precip_chance, pos):
    """Precipitation adjustment factor by position.

    Deliberately steep for passers: rain is a strong QB/WR fade, so even a
    modest rain chance should drop a passer's projection enough that a
    neutral-weather option wins. RBs gain a larger carry share.
    """
    if not precip_chance or precip_chance != precip_chance or precip_chance < 5:
        return 1.0

    if pos == 'QB':
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.80
        elif precip_chance < 75:
            return 0.70
        else:
            return 0.60
    elif pos in ['WR', 'TE']:
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.78
        elif precip_chance < 75:
            return 0.68
        else:
            return 0.55
    elif pos == 'RB':
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.03
        elif precip_chance < 75:
            return 1.05
        else:
            return 1.07
    elif pos == 'K':
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.82
        elif precip_chance < 75:
            return 0.72
        else:
            return 0.62
    elif pos in ('D', 'MVP'):
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.05
        elif precip_chance < 75:
            return 1.09
        else:
            return 1.12

    return 1.0


def compute_team_totals(spread_df):
    """
    Compute per-team implied Vegas totals from a spread DataFrame.

    Home team total = (O/U - spread) / 2, away = (O/U + spread) / 2,
    matching the PointSpread convention (positive = home unfavored).
    Also applies JAX->JAC / LVS->LV abbreviation aliases.
    Returns (team_totals, avg_team_total).
    """
    team_totals = {}
    for _, row in spread_df.iterrows():
        home, away = row['HomeTeam'], row['AwayTeam']
        ou = row['OverUnder']
        team_totals[home] = (ou - row['PointSpread']) / 2
        team_totals[away] = (ou + row['PointSpread']) / 2
    if 'JAX' in team_totals:
        team_totals['JAC'] = team_totals['JAX']
    if 'LVS' in team_totals:
        team_totals['LV'] = team_totals['LVS']
    avg = sum(team_totals.values()) / len(team_totals) if team_totals else 0
    return team_totals, avg


def cap_projection(adjusted_proj, base_score, pos, max_score=27.0,
                   min_proj_multiplier=0.5, max_def_multiplier=2.0,
                   max_proj_multiplier=2.0):
    """Apply the safeguard caps to an adjusted projection."""
    adjusted_proj = min(adjusted_proj, max_score)
    adjusted_proj = max(adjusted_proj, base_score * min_proj_multiplier)
    if pos == 'D':
        adjusted_proj = min(adjusted_proj, base_score * max_def_multiplier)
    else:
        adjusted_proj = min(adjusted_proj, base_score * max_proj_multiplier)
    return adjusted_proj


def compute_fd_points(stats, position):
    """Convert an nflverse weekly stat dict to FanDuel fantasy points."""
    def f(key):
        try:
            return float(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    if position == 'QB':
        return (f('passing_yards') * 0.04 + f('passing_tds') * 4
                + f('rushing_yards') * 0.1 + f('rushing_tds') * 6
                - f('passing_interceptions') - f('fumbles_lost_total'))
    if position in ('RB', 'WR', 'TE'):
        return (f('rushing_yards') * 0.1 + f('rushing_tds') * 6
                + f('receiving_yards') * 0.1 + f('receiving_tds') * 6
                - f('fumbles_lost_total'))
    if position == 'K':
        return ((f('fg_made_0_19') + f('fg_made_20_29') + f('fg_made_30_39')) * 3
                + f('fg_made_40_49') * 4
                + (f('fg_made_50_59') + f('fg_made_60_')) * 5
                + f('pat_made'))
    return 0.0


def compute_defense_fd_points(stats):
    """nflverse defensive stat dict -> FanDuel DST points (big-play component)."""
    def f(key):
        try:
            return float(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return (f('def_tds') * 6 + f('def_interceptions') * 2
            + f('def_fumbles_forced') * 2 + f('def_sacks')
            + f('def_safeties') * 2 + f('fumble_recovery_tds') * 6)


def blend_projections(primary, secondary, secondary_weight=0.3):
    """Weighted blend of two projections; missing inputs fall back gracefully."""
    if secondary is None or secondary <= 0:
        return primary
    if primary is None or primary <= 0:
        return secondary
    return (1 - secondary_weight) * primary + secondary_weight * secondary


def compute_fp_projection(position, stats):
    """Convert a FantasyPros weekly stat dict to FanDuel points (non-PPR)."""
    def f(key):
        try:
            return float(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    if position == 'QB':
        return (f('pass_yds') * 0.04 + f('pass_tds') * 4
                + f('rush_yds') * 0.1 + f('rush_tds') * 6
                - f('pass_ints') - f('fl'))
    if position in ('RB', 'WR'):
        return (f('rush_yds') * 0.1 + f('rush_tds') * 6
                + f('rec_yds') * 0.1 + f('rec_tds') * 6
                - f('fl'))
    if position == 'TE':
        return f('rec_yds') * 0.1 + f('rec_tds') * 6 - f('fl')
    if position == 'K':
        return f('fg') * 3 + f('xpt')
    return 0.0
