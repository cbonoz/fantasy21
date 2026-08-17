# Pure projection math - no I/O, no module-level side effects.
# Shared by draft.py and the test suite.


def calculate_wind_factor(wind_speed, pos):
    """Wind adjustment factor by position (<1 penalty, >1 bonus, 1 neutral)."""
    if not wind_speed or wind_speed < 2:
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
    if not temp or temp < -10 or temp > 130:
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
    """Precipitation adjustment factor by position."""
    if not precip_chance or precip_chance < 5:
        return 1.0

    if pos == 'QB':
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.97
        elif precip_chance < 75:
            return 0.94
        else:
            return 0.90
    elif pos in ['WR', 'TE']:
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.96
        elif precip_chance < 75:
            return 0.92
        else:
            return 0.87
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
            return 0.98
        elif precip_chance < 75:
            return 0.95
        else:
            return 0.90
    elif pos in ('D', 'MVP'):
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.02
        elif precip_chance < 75:
            return 1.04
        else:
            return 1.06

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
                   min_proj_multiplier=0.5, max_def_multiplier=2.0):
    """Apply the safeguard caps to an adjusted projection."""
    adjusted_proj = min(adjusted_proj, max_score)
    adjusted_proj = max(adjusted_proj, base_score * min_proj_multiplier)
    if pos == 'D':
        adjusted_proj = min(adjusted_proj, base_score * max_def_multiplier)
    return adjusted_proj
