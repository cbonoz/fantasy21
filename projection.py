# Pure projection math - no I/O, no module-level side effects.
# Shared by draft.py and the test suite.


import math
import statistics


def _finite_float(value):
    """Convert a value to a finite float, treating missing/invalid data as absent."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def calculate_wind_factor(wind_speed, pos):
    """Wind adjustment factor by position (<1 penalty, >1 bonus, 1 neutral).

    Onset is 10 mph: below that, NFL passing/running is effectively unaffected,
    so ordinary game-day wind no longer shifts projections.
    """
    wind_speed = _finite_float(wind_speed)
    if wind_speed is None or wind_speed < 2:
        return 1.0

    if pos == 'QB':
        if wind_speed < 10:
            return 1.0
        elif wind_speed < 15:
            return 0.97
        elif wind_speed < 20:
            return 0.93
        else:
            return 0.87
    elif pos == 'WR':
        if wind_speed < 10:
            return 1.0
        elif wind_speed < 15:
            return 0.95
        elif wind_speed < 20:
            return 0.90
        else:
            return 0.82
    elif pos == 'TE':
        if wind_speed < 10:
            return 1.0
        elif wind_speed < 15:
            return 0.97
        elif wind_speed < 20:
            return 0.92
        else:
            return 0.85
    elif pos == 'RB':
        if wind_speed < 10:
            return 1.0
        elif wind_speed < 15:
            return 1.03
        elif wind_speed < 20:
            return 1.05
        else:
            return 1.06
    elif pos == 'K':
        if wind_speed < 10:
            return 1.0
        elif wind_speed < 15:
            return 0.97
        elif wind_speed < 20:
            return 0.90
        else:
            return 0.78
    elif pos in ('D', 'MVP'):
        if wind_speed < 10:
            return 1.0
        elif wind_speed < 15:
            return 1.02
        elif wind_speed < 20:
            return 1.03
        else:
            return 1.04

    return 1.0


def calculate_temperature_factor(temp, pos):
    """Temperature adjustment factor by position."""
    temp = _finite_float(temp)
    if temp is None or temp < -10 or temp > 130:
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

    Chance of precipitation alone does not indicate how much rain falls, so
    use small, gradual adjustments rather than treating a moderate chance as
    sustained heavy rain. RB/DST effects are similarly modest.
    """
    precip_chance = _finite_float(precip_chance)
    if precip_chance is None or precip_chance < 5:
        return 1.0

    if pos == 'QB':
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.99
        elif precip_chance < 75:
            return 0.96
        else:
            return 0.92
    elif pos in ['WR', 'TE']:
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.98
        elif precip_chance < 75:
            return 0.94
        else:
            return 0.89
    elif pos == 'RB':
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.01
        elif precip_chance < 75:
            return 1.03
        else:
            return 1.04
    elif pos == 'K':
        # A forecast chance is not rain intensity, so keep the standalone
        # penalty modest; wind and cold are the better-supported kicker risks.
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.0
        elif precip_chance < 75:
            return 0.97
        else:
            return 0.94
    elif pos in ('D', 'MVP'):
        # The implied team total already prices most of a bad-weather scoring
        # environment; the direct DST edge is small (turnovers/sacks).
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.0
        elif precip_chance < 75:
            return 1.01
        else:
            return 1.02

    return 1.0


def combined_weather_factor(wind_speed, temp, precip_chance, pos,
                            max_adjustment=None):
    """Multiply a position's weather factors, optionally bounding the total.

    Wind, temperature, and precipitation are correlated (storms are windy and
    cold snaps are gusty), so the product can overstate a single forecast.
    `max_adjustment` caps the factor at 1 +/- that amount; pass None to leave
    the product unclamped.
    """
    factor = (calculate_wind_factor(wind_speed, pos)
              * calculate_temperature_factor(temp, pos)
              * calculate_precipitation_factor(precip_chance, pos))
    max_adjustment = _finite_float(max_adjustment)
    if max_adjustment is None or max_adjustment < 0:
        return factor
    limit = min(max_adjustment, 1.0)
    return min(max(factor, 1.0 - limit), 1.0 + limit)


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
        return _finite_float(stats.get(key)) or 0.0

    if position == 'QB':
        return (f('passing_yards') * 0.04 + f('passing_tds') * 4
                + f('rushing_yards') * 0.1 + f('rushing_tds') * 6
                + f('passing_2pt_conversions') * 2
                + f('rushing_2pt_conversions') * 2
                + f('special_teams_tds') * 6
                - f('passing_interceptions') - f('fumbles_lost_total') * 2)
    if position in ('RB', 'WR', 'TE'):
        return (f('rushing_yards') * 0.1 + f('rushing_tds') * 6
                + f('receiving_yards') * 0.1 + f('receiving_tds') * 6
                + f('receptions') * 0.5
                + (f('rushing_2pt_conversions') + f('receiving_2pt_conversions')) * 2
                + f('special_teams_tds') * 6
                - f('fumbles_lost_total') * 2)
    if position == 'K':
        return ((f('fg_made_0_19') + f('fg_made_20_29') + f('fg_made_30_39')) * 3
                + f('fg_made_40_49') * 4
                + (f('fg_made_50_59') + f('fg_made_60_')) * 5
                + f('pat_made'))
    return 0.0


def compute_defense_fd_points(stats):
    """nflverse defensive stat dict -> FanDuel DST points (big-play component)."""
    def f(key):
        return _finite_float(stats.get(key)) or 0.0

    return (f('def_tds') * 6 + f('def_interceptions') * 2
            + f('fumble_recovery_opp') * 2 + f('def_sacks')
            + f('def_safeties') * 2 + f('fumble_recovery_tds') * 6
            + (f('def_punt_blocks') + f('def_fg_blocks') + f('def_pat_blocks')) * 2
            + f('special_teams_tds') * 6)


def blend_projections(primary, secondary, secondary_weight=0.3):
    """Weighted blend of two projections; missing inputs fall back gracefully."""
    primary = _finite_float(primary)
    secondary = _finite_float(secondary)
    if secondary is None or secondary <= 0:
        return primary
    if primary is None or primary <= 0:
        return secondary
    weight = _finite_float(secondary_weight)
    if weight is None:
        weight = 0.3
    weight = min(max(weight, 0.0), 1.0)
    return (1 - weight) * primary + weight * secondary


def blend_available_projections(projections, weights):
    """Blend positive finite source projections, renormalizing missing inputs."""
    weighted_total = 0.0
    available_weight = 0.0
    for source, value in projections.items():
        value = _finite_float(value)
        weight = _finite_float(weights.get(source))
        if value is None or value <= 0 or weight is None or weight <= 0:
            continue
        weighted_total += value * weight
        available_weight += weight
    return weighted_total / available_weight if available_weight else 0.0


def scale_source_weight(weight, coverage):
    """Scale a partial source's blend weight by the fraction of the pool it covers.

    A scrape that only covers the top of the pool should not carry a full
    source weight, or the players it happens to include get an extra market
    signal the rest of the pool does not.
    """
    weight = _finite_float(weight)
    coverage = _finite_float(coverage)
    if weight is None or weight <= 0:
        return 0.0
    if coverage is None:
        return weight
    return weight * min(max(coverage, 0.0), 1.0)


def compute_role_trend_bonus(recent_share, earlier_share,
                             points_per_share=6.0, max_bonus=1.0):
    """Small capped point bonus for a sustained increase in target share."""
    recent_share = _finite_float(recent_share)
    earlier_share = _finite_float(earlier_share)
    points_per_share = _finite_float(points_per_share)
    max_bonus = _finite_float(max_bonus)
    if (recent_share is None or earlier_share is None
            or points_per_share is None or max_bonus is None
            or points_per_share <= 0 or max_bonus <= 0):
        return 0.0
    increase = max(recent_share - earlier_share, 0.0)
    return min(increase * points_per_share, max_bonus)


def compute_role_competition_penalty(
        recent_share, earlier_share, returning_teammate_share,
        min_share_drop=0.12, min_teammate_share=0.15,
        points_per_share=5.0, max_penalty=2.5):
    """Bounded projection penalty for a role lost to a meaningful teammate return."""
    recent_share = _finite_float(recent_share)
    earlier_share = _finite_float(earlier_share)
    returning_teammate_share = _finite_float(returning_teammate_share)
    min_share_drop = _finite_float(min_share_drop)
    min_teammate_share = _finite_float(min_teammate_share)
    points_per_share = _finite_float(points_per_share)
    max_penalty = _finite_float(max_penalty)
    if any(value is None for value in (
            recent_share, earlier_share, returning_teammate_share,
            min_share_drop, min_teammate_share, points_per_share, max_penalty)):
        return 0.0
    share_drop = earlier_share - recent_share
    if (share_drop < min_share_drop
            or returning_teammate_share < min_teammate_share
            or points_per_share <= 0 or max_penalty <= 0):
        return 0.0
    return min(share_drop * points_per_share, max_penalty)


def compute_recent_form_adjustment(
        recent_scores, season_scores, weight=0.35, prior_games=2.0,
        max_adjustment=1.5, min_recent_games=3, min_prior_games=2):
    """Return a bounded, shrinkage-adjusted recent median scoring trend.

    The recent sample must contain enough appearances and leave a minimum
    number of earlier games for comparison. Median scores reduce sensitivity
    to a single outlier; the reliability factor shrinks short recent samples
    toward no adjustment.
    """
    recent = [_finite_float(value) for value in recent_scores or []]
    season = [_finite_float(value) for value in season_scores or []]
    recent = [value for value in recent if value is not None]
    season = [value for value in season if value is not None]
    try:
        weight = float(weight)
        prior_games = float(prior_games)
        max_adjustment = float(max_adjustment)
        min_recent_games = int(min_recent_games)
        min_prior_games = int(min_prior_games)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if (not math.isfinite(weight) or not math.isfinite(prior_games)
            or not math.isfinite(max_adjustment) or prior_games < 0
            or max_adjustment <= 0 or weight <= 0
            or len(recent) < min_recent_games
            or len(season) - len(recent) < min_prior_games):
        return 0.0

    recent_median = statistics.median(recent)
    season_median = statistics.median(season)
    reliability = len(recent) / (len(recent) + prior_games)
    adjustment = (recent_median - season_median) * weight * reliability
    return max(min(adjustment, max_adjustment), -max_adjustment)


def allocate_vacated_role_bonus(bonus, active_weights):
    """Distribute an injured player's role bonus across active teammates.

    Weights should represent recent workload (opportunities first, snaps or
    production as fallbacks). Invalid/negative weights are ignored rather than
    turning a missing-data case into an equal bonus for every player.
    """
    bonus = _finite_float(bonus)
    if bonus is None or bonus <= 0 or not active_weights:
        return {}
    weights = {
        player: max(_finite_float(weight) or 0.0, 0.0)
        for player, weight in active_weights.items()
    }
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {player: bonus * weight / total for player, weight in weights.items() if weight > 0}


def adjust_ceiling_for_role_change(ceiling, role_bonus, weight=1.0, max_bonus=2.0):
    """Carry a capped current-role point adjustment into a player's ceiling."""
    ceiling = _finite_float(ceiling)
    role_bonus = _finite_float(role_bonus)
    weight = _finite_float(weight)
    max_bonus = _finite_float(max_bonus)
    if ceiling is None:
        return None
    if role_bonus is None or weight is None or max_bonus is None:
        return ceiling
    uplift = min(max(role_bonus, 0.0) * max(weight, 0.0), max(max_bonus, 0.0))
    return ceiling + uplift


def compute_kicker_total_bonus(team_total, average_total, weight=0.10, max_bonus=0.75):
    """Small bounded kicker opportunity adjustment from Vegas implied total."""
    team_total = _finite_float(team_total)
    average_total = _finite_float(average_total)
    weight = _finite_float(weight)
    max_bonus = _finite_float(max_bonus)
    if (team_total is None or average_total is None or weight is None
            or max_bonus is None or weight <= 0 or max_bonus <= 0):
        return 0.0
    deviation = team_total - average_total
    return max(min(deviation * weight, max_bonus), -max_bonus)


def adjust_ceiling_for_weather(ceiling, weather_factor, max_adjustment=0.40):
    """Apply a bounded positional weather factor to an empirical ceiling."""
    ceiling = _finite_float(ceiling)
    weather_factor = _finite_float(weather_factor)
    max_adjustment = _finite_float(max_adjustment)
    if ceiling is None:
        return None
    if weather_factor is None or max_adjustment is None or max_adjustment < 0:
        return ceiling
    factor = max(min(weather_factor, 1.0 + max_adjustment), 1.0 - max_adjustment)
    return ceiling * factor


def compute_same_team_overlap_penalty(team_counts, penalty_per_extra=2.0):
    """Penalize actual roster overlap, leaving standalone players intact."""
    penalty_per_extra = _finite_float(penalty_per_extra)
    if penalty_per_extra is None or penalty_per_extra <= 0:
        return 0.0
    penalty = 0.0
    for count in team_counts.values():
        try:
            extras = max(int(count) - 1, 0)
        except (TypeError, ValueError, OverflowError):
            continue
        penalty += extras * penalty_per_extra
    return penalty


def compute_same_team_wr_overlap_penalty(team_wr_counts, penalty_per_extra=2.0):
    """WR-specific alias for the shared same-team overlap penalty."""
    return compute_same_team_overlap_penalty(team_wr_counts, penalty_per_extra)


def compute_same_team_rb_overlap_penalty(team_rb_counts, penalty_per_extra=2.0):
    """Penalize two-plus same-team RBs competing for the same touches.

    Their projections are individually honest but not independent: a big game
    for one usually comes at the other's expense, so summing two ceilings
    overstates the backfield's combined upside.
    """
    return compute_same_team_overlap_penalty(team_rb_counts, penalty_per_extra)


def compute_same_team_wr_te_overlap_penalty(
        wr_target_share, te_target_share, points_per_share=3.0,
        max_penalty=1.0):
    """Small lineup-level penalty for a WR/TE target-sharing pair.

    Uses the smaller recent target share as the overlap signal, avoiding a
    blanket projection discount when the usage data is unavailable.
    """
    wr_target_share = _finite_float(wr_target_share)
    te_target_share = _finite_float(te_target_share)
    points_per_share = _finite_float(points_per_share)
    max_penalty = _finite_float(max_penalty)
    if (wr_target_share is None or te_target_share is None
            or points_per_share is None or max_penalty is None
            or points_per_share <= 0 or max_penalty <= 0):
        return 0.0
    overlap_share = min(
        max(wr_target_share, 0.0),
        max(te_target_share, 0.0),
    )
    return min(overlap_share * points_per_share, max_penalty)


def center_empirical_samples(samples, target_mean):
    """Shift an empirical sample to a current projection without changing shape."""
    target_mean = _finite_float(target_mean)
    values = [_finite_float(value) for value in samples]
    values = [value for value in values if value is not None]
    if not values or target_mean is None:
        return []
    sample_mean = sum(values) / len(values)
    return [value - sample_mean + target_mean for value in values]


def qb_quality_multiplier(qb_projection, league_average,
                          weight=0.5, min_multiplier=0.85, max_multiplier=1.15):
    """Bound an injury-workload adjustment by QB projection vs. league average."""
    try:
        qb_projection = float(qb_projection)
        league_average = float(league_average)
    except (TypeError, ValueError):
        return 1.0
    if (not math.isfinite(qb_projection) or not math.isfinite(league_average)
            or qb_projection <= 0 or league_average <= 0):
        return 1.0
    relative_deviation = (qb_projection - league_average) / league_average
    return max(min_multiplier, min(max_multiplier, 1.0 + weight * relative_deviation))


def compute_qb_health_drop(primary_projection, primary_unavailability,
                           replacements):
    """Expected QB projection loss from an uncertain primary and known backups.

    `replacements` is ordered best-to-worst, as (projection, unavailability)
    pairs. If no positive backup projection is available, return zero rather
    than inventing a replacement-level estimate from missing data.
    """
    primary_projection = _finite_float(primary_projection)
    primary_unavailability = _finite_float(primary_unavailability)
    if (primary_projection is None or primary_projection <= 0
            or primary_unavailability is None):
        return 0.0
    primary_unavailability = min(max(primary_unavailability, 0.0), 1.0)
    valid_replacements = []
    for projection, unavailability in replacements or []:
        projection = _finite_float(projection)
        unavailability = _finite_float(unavailability)
        if projection is None or projection <= 0 or unavailability is None:
            continue
        valid_replacements.append((projection, min(max(unavailability, 0.0), 1.0)))
    if not valid_replacements or primary_unavailability <= 0:
        return 0.0

    # Roll availability odds down the depth chart. The lowest known positive
    # projection is the fallback when no further QB projection is available.
    expected_replacement = valid_replacements[-1][0]
    for projection, unavailability in reversed(valid_replacements[:-1]):
        expected_replacement = (
            projection * (1.0 - unavailability)
            + expected_replacement * unavailability
        )
    expected_team_qb = (
        primary_projection * (1.0 - primary_unavailability)
        + expected_replacement * primary_unavailability
    )
    return max(primary_projection - expected_team_qb, 0.0)


def compute_qb_pass_catcher_penalty(qb_projection_drop, player_projection,
                                    sensitivity=0.15, projection_scale=15.0,
                                    max_penalty=1.25):
    """Bounded WR/TE deduction from an expected QB-quality drop."""
    qb_projection_drop = _finite_float(qb_projection_drop)
    player_projection = _finite_float(player_projection)
    sensitivity = _finite_float(sensitivity)
    projection_scale = _finite_float(projection_scale)
    max_penalty = _finite_float(max_penalty)
    if (qb_projection_drop is None or player_projection is None
            or sensitivity is None or projection_scale is None
            or max_penalty is None or projection_scale <= 0
            or sensitivity <= 0 or max_penalty <= 0):
        return 0.0
    penalty = max(qb_projection_drop, 0.0) * max(player_projection, 0.0)
    penalty *= sensitivity / projection_scale
    return min(penalty, max_penalty)


def compute_wr_injury_bonus(pool, production_share, qb_multiplier=1.0,
                            pool_cap=2.0, inheritance_weight=1.0):
    """Allocate a capped share of vacated WR production, adjusted for QB quality."""
    try:
        pool, production_share, qb_multiplier, pool_cap, inheritance_weight = map(
            float, (pool, production_share, qb_multiplier, pool_cap, inheritance_weight))
    except (TypeError, ValueError):
        return 0.0
    values = (pool, production_share, qb_multiplier, pool_cap, inheritance_weight)
    if not all(math.isfinite(value) for value in values):
        return 0.0
    capped_pool = min(max(pool, 0.0), max(pool_cap, 0.0))
    share = min(max(production_share, 0.0), 1.0)
    return capped_pool * share * max(qb_multiplier, 0.0) * max(inheritance_weight, 0.0)


def compute_fp_projection(position, stats):
    """Convert a FantasyPros weekly stat dict to FanDuel half-PPR points."""
    def f(key):
        return _finite_float(stats.get(key)) or 0.0

    if position == 'QB':
        return (f('pass_yds') * 0.04 + f('pass_tds') * 4
                + f('rush_yds') * 0.1 + f('rush_tds') * 6
                - f('pass_ints') - f('fl') * 2)
    if position in ('RB', 'WR'):
        return (f('rush_yds') * 0.1 + f('rush_tds') * 6
                + f('rec_yds') * 0.1 + f('rec_tds') * 6 + f('rec_rec') * 0.5
                - f('fl') * 2)
    if position == 'TE':
        return f('rec_yds') * 0.1 + f('rec_tds') * 6 + f('rec_rec') * 0.5 - f('fl') * 2
    if position == 'K':
        return f('fg') * 3 + f('xpt')
    return 0.0
