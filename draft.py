# FanDuel NFL lineup optimizer
# Run headless with: uv run python draft.py

import copy
import math
import os
from collections import defaultdict
from datetime import datetime

from draftfast import rules
from draftfast.csv_parse import salary_download
from draftfast.lineup_constraints import LineupConstraints
from draftfast.optimize import run
from draftfast.settings import OptimizerSettings, CustomRule
from fantasypros import build_projection_map as build_fp_projection_map
from nfl_teams import normalize_player_name, normalize_team_abbr
import numpy as np
import pandas as pd
from odds import get_metabet_spread
from projection import (
    calculate_precipitation_factor,
    calculate_temperature_factor,
    calculate_wind_factor,
    blend_projections,
    cap_projection,
    compute_team_totals,
)
from nflverse import compute_fpa_map, compute_history, compute_qb_snap_share_map
from sleeper import build_projection_map
from weather import display_weather_summary, get_nfl_weather

import config


def get_most_recently_created_file_with_extension(folder, extension):
    files = [f for f in os.listdir(folder) if f.endswith(extension)]
    return max(files, key=lambda x: os.path.getmtime(f"{folder}/{x}"))


def get_week_relative_to_start(season_start=config.SEASON_START):
    start = datetime.strptime(season_start, '%m/%d/%Y')
    today = datetime.today()
    num_days = (today - start).days + 1
    return math.ceil(num_days / 7)


def get_slate_week_from_file(filepath, season_start=config.SEASON_START):
    """Derive slate week from the FanDuel filename, e.g.
    'FanDuel-NFL-2026 EDT-09 EDT-20 EDT-134251-players-list.csv'
    encodes year=2026, month=09, slate day=20. Returns None if unparseable."""
    import re
    m = re.search(r'NFL-(\d{4}) EDT-(\d{2}) EDT-(\d{2})', os.path.basename(filepath))
    if not m:
        return None
    year, month, day = map(int, m.groups())
    slate = datetime(year, month, day)
    start = datetime.strptime(season_start, '%m/%d/%Y')
    if slate < start:
        return None
    return ((slate - start).days // 7) + 1


SALARY_FILE = f"{config.DATA_FOLDER}/{get_most_recently_created_file_with_extension(config.DATA_FOLDER, 'csv')}"
WEEK = get_slate_week_from_file(SALARY_FILE) or max(get_week_relative_to_start(), 1)
ACTIVE_FILE = f"{config.ACTIVE_FOLDER}/data.csv"
UPLOAD_FILE = f"{config.UPLOAD_FOLDER}/upload.csv"

WEIGHTED = config.WEIGHTED
MAX_SALARY = config.MAX_SALARY

# ============================================================
# Load salary data
# ============================================================

df = pd.read_csv(SALARY_FILE, na_values='')
print('ready', SALARY_FILE, WEEK)

# ============================================================
# Defense strength: fantasy points against (FPA)
# Optional - proceeds with zeros if data is unavailable.
# ============================================================

fpa_map = {}
try:
    fpa_map = compute_fpa_map(WEEK) or {}
    print(f"Using {len(fpa_map)} FPA entries")
except Exception as e:
    print('Error loading FPA data:', e)
    fpa_map = {}

# Per-position league-average points allowed per game, used as the neutral
# baseline for FPA adjustments (a defense allowing average = 0 bonus).
fpa_baselines = {}
for _entry in fpa_map.values():
    for _pos, _value in _entry.items():
        fpa_baselines.setdefault(_pos, []).append(_value)
fpa_baselines = {pos: sum(v) / len(v) for pos, v in fpa_baselines.items()}

# ============================================================
# Vegas spreads / over-unders -> favor_map, team_totals
# ============================================================

spread_df = get_metabet_spread(WEEK)

# Vegas convention: PointSpread is from the home team's perspective.
# Negative = home favored, positive = home unfavored.
# favor_map: positive = team is unfavored, negative = team is favored.
favor_map = {}
for index, row in spread_df.iterrows():
    home, away = row['HomeTeam'], row['AwayTeam']
    favor_map[home] = row['PointSpread']
    favor_map[away] = -row['PointSpread']

# Team abbreviation aliases used by odds data
if 'JAX' in favor_map:
    favor_map['JAC'] = favor_map['JAX']
if 'LVS' in favor_map:
    favor_map['LV'] = favor_map['LVS']

# Implied team totals from Vegas: home team total = (O/U - spread) / 2,
# away team total = (O/U + spread) / 2. favor_map[home] = spread as-is.
team_totals, avg_team_total = compute_team_totals(spread_df)

# ============================================================
# Weather
# ============================================================

weather_df = get_nfl_weather(WEEK)

if not weather_df.empty:
    team_name_to_abbr = {
        'Rams': 'LAR', 'Cardinals': 'ARI', 'Falcons': 'ATL', 'Saints': 'NO', 'Panthers': 'CAR',
        'Bears': 'CHI', 'Lions': 'DET', 'Packers': 'GB', 'Vikings': 'MIN',
        'Cowboys': 'DAL', 'Eagles': 'PHI', 'Commanders': 'WAS', 'Giants': 'NYG',
        '49ers': 'SF', 'Seahawks': 'SEA', 'Buccaneers': 'TB',
        'Bills': 'BUF', 'Dolphins': 'MIA', 'Patriots': 'NE', 'Jets': 'NYJ',
        'Steelers': 'PIT', 'Browns': 'CLE', 'Ravens': 'BAL', 'Bengals': 'CIN',
        'Colts': 'IND', 'Texans': 'HOU', 'Jaguars': 'JAC', 'Titans': 'TEN',
        'Broncos': 'DEN', 'Chiefs': 'KC', 'Chargers': 'LAC', 'Raiders': 'LV'
    }
    weather_df['away_team'] = weather_df['away_team'].apply(lambda t: team_name_to_abbr.get(t, t))
    weather_df['home_team'] = weather_df['home_team'].apply(lambda t: team_name_to_abbr.get(t, t))

# ============================================================
# Filter player pool
# ============================================================

set_teams = set(df['Team'])
SINGLE_GAME = len(set_teams) == 2

MIN_SALARY = config.MIN_SALARY_SINGLE if SINGLE_GAME else config.MIN_SALARY_CLASSIC

df['Name'] = df['First Name'] + " " + df['Last Name']
df['Salary/FPPG'] = df['FPPG'] / df['Salary']

questionable_players = list(df[(~df['Injury Indicator'].isna()) | (~df['Injury Details'].isna())]['Name'])
low_salary_players = list(df[((df['Salary'] < MIN_SALARY)) & (df['Position'] != 'D')]['Name'])
excluded_players = set([*questionable_players, *low_salary_players])

# Manual re-additions (weekly tuned)
for p in config.READD:
    excluded_players.discard(p)

questionable_df = df[df['Name'].isin(questionable_players)]
df = df[~df['Name'].isin(excluded_players)]

# ============================================================
# Historical averages (nflverse prior-week FanDuel points)
# ============================================================

historic_averages = {}
try:
    historic_averages = compute_history(WEEK) or {}
    print(f"Using {len(historic_averages)} nflverse history entries")
except Exception as e:
    print('Error loading nflverse history:', e)
    historic_averages = {}

# ============================================================
# Sleeper projections (independent weekly projection source)
# ============================================================

sleeper_proj_map = {}
try:
    sleeper_proj_map = build_projection_map(WEEK) or {}
    print(f"Loaded {len(sleeper_proj_map)} Sleeper projections")
except Exception as e:
    print('Error loading Sleeper projections:', e)
    sleeper_proj_map = {}

# ============================================================
# FantasyPros projections (independent weekly projection source)
# ============================================================

fp_proj_map = {}
try:
    fp_proj_map = build_fp_projection_map(WEEK) or {}
    print(f"Loaded {len(fp_proj_map)} FantasyPros projections")
except Exception as e:
    print('Error loading FantasyPros projections:', e)
    fp_proj_map = {}

# ============================================================
# QB snap-share map (backup detection)
# ============================================================

qb_snap_share_map = {}
if config.USE_SNAP_BACKUP:
    try:
        qb_snap_share_map = compute_qb_snap_share_map(WEEK) or {}
    except Exception as e:
        print('Error loading snap counts:', e)
        qb_snap_share_map = {}

# ============================================================
# Injury bonuses
# ============================================================

INJURY_FACTOR = config.INJURY_FACTOR
excluded_bonus = defaultdict(lambda: 0)
injured_qb = defaultdict(lambda: False)

for index, p in questionable_df.iterrows():
    pos = p['Position']
    if pos in ['TE', 'WR', 'RB', 'QB']:
        points = p['FPPG']
        if points > 7.5 and p['Played'] >= WEEK / 2:
            injury_offset = min(points * INJURY_FACTOR, INJURY_FACTOR * 10)
            if pos == 'QB':
                amt = -injury_offset * 2
                injured_qb[p['Team']] = True
            elif pos in ('RB', 'WR', 'TE'):
                amt = injury_offset * 1.2
            else:
                amt = injury_offset
            excluded_bonus[p['Team']] += amt

# ============================================================
# Rules / roster structure
# ============================================================

df.to_csv(ACTIVE_FILE)


def get_nfl_positions():
    if SINGLE_GAME:
        positions = [[p, 0, 5] for p in set(df['Position'])]
        positions.append(['MVP', 1, 1])  # Exactly 1 MVP required
        return positions
    return [
        ['QB', 1, 1],
        ['RB', 2, 3],
        ['WR', 3, 4],
        ['TE', 1, 2],
        ['D', 1, 1],
    ]


ACTIVE_RULE_SET = rules.FD_NFL_RULE_SET
ACTIVE_RULE_SET.salary_max = config.SALARY_MAX
ACTIVE_RULE_SET.defensive_positions = ['D', 'DEF']
ACTIVE_RULE_SET.offensive_positions = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'WR/FLEX', 'K', 'MVP'] if SINGLE_GAME else ['QB', 'RB', 'WR', 'TE', 'FLEX', 'WR/FLEX', 'K']
ACTIVE_RULE_SET.position_limits = get_nfl_positions()
if SINGLE_GAME:
    ACTIVE_RULE_SET.salary_min = ACTIVE_RULE_SET.salary_max - config.SALARY_MIN_OFFSET
else:
    ACTIVE_RULE_SET.salary_min = ACTIVE_RULE_SET.salary_max - config.SALARY_MIN_OFFSET
    ACTIVE_RULE_SET.max_players_per_team = config.MAX_PLAYERS_PER_TEAM_CLASSIC
ACTIVE_RULE_SET.roster_size = config.ROSTER_SIZE_CLASSIC if not SINGLE_GAME else config.ROSTER_SIZE_SINGLE

ALL_POSITIONS = [*ACTIVE_RULE_SET.defensive_positions, *ACTIVE_RULE_SET.offensive_positions]

# ============================================================
# Weather adjustment factors
# ============================================================


def get_weather_for_team(team, weather_df):
    """Find weather data for a given team's game, or empty dict if not found."""
    if weather_df.empty:
        return {}
    game = weather_df[(weather_df['away_team'] == team) | (weather_df['home_team'] == team)]
    if game.empty:
        return {}
    game_data = game.iloc[0]
    return {
        'temperature': game_data.get('temperature'),
        'wind_speed': game_data.get('wind_speed'),
        'precipitation_chance': game_data.get('precipitation_chance'),
        'condition': game_data.get('condition'),
        'away_team': game_data.get('away_team'),
        'home_team': game_data.get('home_team'),
    }


# ============================================================
# Build player pool + adjusted projections
# ============================================================

AVERAGE_WEIGHT = config.AVERAGE_WEIGHT
MIN_PLAYED = min(int(WEEK * 0.4), 2)
MIN_QB_SALARY = config.MIN_QB_SALARY_SINGLE if SINGLE_GAME else config.MIN_QB_SALARY_CLASSIC
MIN_SCORE = config.MIN_SCORE
MAX_SCORE = config.MAX_SCORE
INJURED_QB_BONUS = config.INJURED_QB_BONUS


def build_starter_map(players, questionable_df):
    """Map (team, pos) -> (starter_name, starter_fppg, is_injured) for QB/RB/WR/TE."""
    injured_names = set(questionable_df['Name']) if not questionable_df.empty else set()
    starter_map = {}
    for p in players:
        if p.pos in ['QB', 'RB', 'WR', 'TE']:
            key = (p.team, p.pos)
            base_name = p.name.replace(' (MVP)', '')
            current_fppg = float(p.kv_store.get('FPPG') or 0)
            is_injured = base_name in injured_names
            if key not in starter_map or current_fppg > starter_map[key][1]:
                starter_map[key] = (base_name, current_fppg, is_injured)
    return starter_map


def base_position(p):
    """Underlying position of a player; MVP variants report their base position."""
    if p.pos != 'MVP':
        return p.pos
    return p.kv_store.get('base_pos')


def filter_mvps(mvps, players, starter_map):
    """Include MVP candidates who are starters, or backups whose starter is injured."""
    players_by_name = {p.name: p for p in players}
    filtered_mvps = []
    for name, proj, cost, value, pos, base_fppg, opponent in sorted(mvps, key=lambda x: x[3], reverse=True):
        base_name = name.replace(' (MVP)', '')
        p = players_by_name.get(base_name)
        player_games = int(float(p.kv_store.get('Played', 0))) if p else 0
        player_team = p.team if p else None
        player_pos = p.pos if p else None

        status = "Starter"
        starter_name = None
        starter_injured = False
        if player_team and player_pos in ['QB', 'RB', 'WR', 'TE']:
            starter_name, _, is_injured = starter_map.get((player_team, player_pos), (None, None, False))
            if starter_name and starter_name != base_name:
                status = "Backup"
                if is_injured:
                    starter_injured = True
                    status = f"(Starter {starter_name} injured)"

        is_starter = not starter_name or starter_name == base_name
        if is_starter or starter_injured:
            filtered_mvps.append((name, proj, cost, value, pos, base_fppg, player_games, opponent, status))

    return filtered_mvps


def calculate_team_total_bonus(p, opponent):
    """
    Vegas implied team-total bonus. A player's projection scales with how
    far their team's implied total deviates from the slate average. Defenses
    and MVPs use the opponent's total (they score better against weak offenses).
    """
    if p.pos in ['D', 'MVP']:
        total = team_totals.get(opponent, avg_team_total)
        deviation = avg_team_total - total  # low opponent total = good for D
        return deviation * config.DEFENSE_TOTAL_WEIGHT
    total = team_totals.get(p.team, avg_team_total)
    deviation = total - avg_team_total
    return deviation * config.OFFENSE_TOTAL_WEIGHT


def calculate_fpa_bonus(p, opponent):
    """Fantasy-points-against bonus: boost players facing weak defenses."""
    if p.pos in ['D', 'MVP']:
        return 0
    entry = fpa_map.get(opponent, {})
    allowed = entry.get(p.pos) if isinstance(entry, dict) else None
    if not allowed:
        return 0
    baseline = fpa_baselines.get(p.pos, 0.0)
    if baseline <= 0:
        return 0
    return (float(allowed) - baseline) * config.FPA_WEIGHT


def calculate_injury_bonuses(p, opponent):
    """All injury-related bonuses."""
    bonuses = 0
    if injured_qb.get(opponent, False):
        bonuses += INJURED_QB_BONUS if p.pos in ['D', 'MVP'] else INJURED_QB_BONUS / 2
    if p.pos == 'QB':
        bonuses += -excluded_bonus.get(p.team, 0)
    elif p.pos in ['D', 'MVP']:
        bonuses += excluded_bonus.get(p.team, 0) / 2
    else:
        bonuses += excluded_bonus.get(p.team, 0)
    if p.pos == 'RB' and injured_qb.get(p.team, False):
        bonuses += INJURED_QB_BONUS
    return bonuses


def get_blended_projection(p, history_key):
    """Blend current projection with historical average when available."""
    history_value = historic_averages.get(history_key)
    if history_value:
        return AVERAGE_WEIGHT * p.proj + (1 - AVERAGE_WEIGHT) * history_value
    return p.proj


def history_key_for(p):
    """Normalized lookup key into nflverse history averages."""
    if p.pos in ['D', 'MVP']:
        return normalize_team_abbr(p.team).lower()
    return normalize_player_name(p.name)


def sleeper_projection_for(p):
    """Sleeper pts_std for a player, keyed by base position/name."""
    pos = base_position(p)
    if pos == 'D':
        return None
    return sleeper_proj_map.get((p.team, pos, normalize_player_name(p.name.replace(' (MVP)', ''))))


def fp_projection_for(p):
    """FantasyPros FanDuel-adjusted projection; defenses keyed by team."""
    pos = base_position(p)
    if pos == 'D':
        return fp_proj_map.get((p.team, 'D', ''))
    return fp_proj_map.get((p.team, pos, normalize_player_name(p.name.replace(' (MVP)', ''))))


weather_factor_map = {}
if not weather_df.empty:
    for team in set(list(weather_df['away_team'].dropna()) + list(weather_df['home_team'].dropna())):
        weather_info = get_weather_for_team(team, weather_df)
        if weather_info:
            if normalize_team_abbr(team) in config.ROOFED_TEAMS:
                continue
            wind = weather_info.get('wind_speed')
            temp = weather_info.get('temperature')
            precip = weather_info.get('precipitation_chance')
            for pos in ALL_POSITIONS:
                combined_factor = (calculate_wind_factor(wind, pos)
                                   * calculate_temperature_factor(temp, pos)
                                   * calculate_precipitation_factor(precip, pos))
                weather_factor_map[(team, pos)] = combined_factor


def get_opponent(p):
    """Opponent team abbreviation from a player's 'AWAY@HOME' matchup."""
    if not p.matchup:
        return 'N/A'
    teams = p.matchup.split('@')
    return teams[0].strip() if p.team == teams[1].strip() else teams[1].strip()


def calculate_adjusted_projection(p):
    """Weighted projection: base blended with history/Sleeper, adjusted by matchup factors."""
    if not WEIGHTED:
        return p.proj

    # Kickers and cheap non-defense players just blend with history + projections
    if p.pos == 'K' or (p.cost <= config.LOW_SALARY_SKIP and p.pos != 'D'):
        base = get_blended_projection(p, history_key_for(p))
        for source in (sleeper_projection_for, fp_projection_for):
            secondary = source(p)
            if secondary is not None:
                weight = config.SLEEPER_WEIGHT if source is sleeper_projection_for else config.FANTASYPROS_WEIGHT
                base = blend_projections(base, secondary, weight)
        return base

    base_score = get_blended_projection(p, history_key_for(p))
    for source in (sleeper_projection_for, fp_projection_for):
        secondary = source(p)
        if secondary is not None:
            weight = config.SLEEPER_WEIGHT if source is sleeper_projection_for else config.FANTASYPROS_WEIGHT
            base_score = blend_projections(base_score, secondary, weight)

    opponent = get_opponent(p)

    matchup_bonus = 0

    # Vegas implied team total (encodes both spread and game total)
    matchup_bonus += calculate_team_total_bonus(p, opponent)

    # Fantasy points allowed by opponent's defense
    matchup_bonus += calculate_fpa_bonus(p, opponent)

    # Injury adjustments
    matchup_bonus += calculate_injury_bonuses(p, opponent)

    # Defenses/MVPs also account for opponent offense weakness
    if p.pos in ['D', 'MVP']:
        matchup_bonus += excluded_bonus.get(opponent, 0) / 4

    # Weather adjustment (additive, capped at 20% of base)
    weather_bonus = 0
    combined_weather_factor = weather_factor_map.get((p.team, p.pos), 1.0)
    if combined_weather_factor != 1.0:
        p.kv_store['weather_factor'] = combined_weather_factor
        weather_bonus = base_score * (combined_weather_factor - 1.0)
        max_weather_bonus = base_score * config.MAX_WEATHER_BONUS
        weather_bonus = max(min(weather_bonus, max_weather_bonus), -max_weather_bonus)
        matchup_bonus += weather_bonus

    # Apply with safeguards
    if p.pos in ['D', 'MVP'] or base_score >= MIN_SCORE:
        return cap_projection(
            base_score + matchup_bonus,
            base_score,
            p.pos,
            max_score=MAX_SCORE,
            min_proj_multiplier=config.MIN_PROJ_MULTIPLIER,
            max_def_multiplier=config.MAX_DEF_MULTIPLIER,
        )

    return base_score


players = salary_download.generate_players_from_csvs(salary_file_location=ACTIVE_FILE, game=rules.FAN_DUEL)

for p in players:
    p.proj = calculate_adjusted_projection(p)
    p.kv_store['adjusted_proj'] = p.proj

# Single-game slates: create 1.5x-salary/1.5x-projection MVP variants
if SINGLE_GAME:
    mvp_players = []
    for p in players:
        mvp = copy.deepcopy(p)
        mvp.pos = 'MVP'
        mvp.cost = int(round(p.cost * 1.5))
        mvp.proj = p.kv_store.get('adjusted_proj', p.proj) * 1.5
        mvp.name = p.name + ' (MVP)'
        mvp.kv_store['base_name'] = p.name
        mvp.kv_store['base_pos'] = p.pos
        mvp_players.append(mvp)
    players.extend(mvp_players)

# Validate all player teams exist in spread data
missing_teams = {p.team for p in players} - set(favor_map.keys())
if missing_teams:
    error_msg = "The following teams are missing from spread/matchup data:\n"
    for team in sorted(missing_teams):
        error_msg += f"  {team} ({sum(1 for p in players if p.team == team)} players affected)\n"
    raise ValueError(error_msg)

# Sort players into display lists
defenses = []
qbs = []
mvps = []

for p in players:
    base_fppg = float(p.kv_store.get('FPPG') or 0)
    opponent = get_opponent(p)

    if p.pos == 'MVP':
        mvps.append((p.name, p.proj, p.cost, p.proj / p.cost, p.pos, base_fppg, opponent))
    elif p.pos == 'D':
        defenses.append((p.team, p.proj, p.cost, p.proj / p.cost, base_fppg, favor_map.get(p.team, 0), opponent))
    elif p.pos == 'QB' and p.cost >= MIN_QB_SALARY:
        team_total = team_totals.get(p.team, avg_team_total)
        total_deviation = team_total - avg_team_total
        qbs.append((p.name, p.proj, p.cost, p.proj / p.cost, team_total, total_deviation, base_fppg, opponent, p.team))

starter_map = build_starter_map(players, questionable_df)
filtered_mvps = filter_mvps(mvps, players, starter_map)

# ============================================================
# Display: sorted defenses / QBs / MVPs
# ============================================================

print("\n" + "=" * 100)
print("SORTED DEFENSES")
print("=" * 100)
print(f"{'Team':<8} {'Proj':>8} {'Salary':>10} {'Base':>8} {'Spread':>8} {'Opp':>5} {'Value':>8}")
print("-" * 100)
for team, proj, cost, value, base_fppg, spread, opp in sorted(defenses, key=lambda x: x[3], reverse=True):
    print(f"{team:<8} {proj:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {spread:>8.2f} {opp:>5} {(value * 1000):>7.1f}x")

if SINGLE_GAME:
    print("\n" + "=" * 100)
    print("SORTED MVPs (Top 10 - Backups Shown Only if Starter Injured)")
    print("=" * 100)
    print(f"{'Name':<35} {'Proj':>8} {'Salary':>10} {'Base':>8} {'Value':>8} {'Games':>7} {'Opp':>5} {'Status':<10}")
    print("-" * 100)
    for name, proj, cost, value, pos, base_fppg, games_played, opp, status in filtered_mvps[:10]:
        print(f"{name:<35} {proj:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {(value * 1000):>7.1f}x {games_played:>7.0f} {opp:>5} {status:<10}")

print("\n" + "=" * 105)
print("SORTED QBs")
print("=" * 105)
print(f"{'Name':<35} {'Proj':>8} {'Salary':>10} {'Value':>8} {'TeamTotal':>10} {'Dev':>8} {'Opp':>5} {'Base':>8} {'Status':<10}")
print("-" * 115)
for name, proj, cost, value, team_total, total_deviation, base_fppg, opp, team in sorted(qbs, key=lambda x: x[3], reverse=True):
    starter_name, _, starter_injured = starter_map.get((team, 'QB'), (None, None, False))
    status = "Starter"
    if starter_name and starter_name != name:
        status = "Backup" if not starter_injured else f"Starter {starter_name} injured"
    print(f"{name:<35} {proj:>8.2f} ${cost:>9,.0f} {(value * 1000):>7.1f}x {team_total:>10.2f} {total_deviation:>8.2f} {opp:>5} {base_fppg:>8.2f} {status:<10}")

display_weather_summary(weather_df)

# ============================================================
# Optimizer: draftfast weighted optimization
# ============================================================

BANNED = config.BANNED_SINGLE if SINGLE_GAME else config.BANNED_CLASSIC
BLOCKED_TEAMS = config.BLOCKED_TEAMS
constraints = LineupConstraints(locked=config.LOCKED, banned=BANNED)


def block_function(p):
    store = p.kv_store
    played = int(float(store.get('Played') or 0))
    if SINGLE_GAME:
        if played < MIN_PLAYED:
            return True
        # Block backup QBs (incl. MVP variants) whose starter is healthy.
        if base_position(p) == 'QB':
            base_name = p.name.replace(' (MVP)', '')
            starter_name, _, starter_injured = starter_map.get((p.team, 'QB'), (None, None, False))
            if starter_name is not None and starter_name == base_name:
                is_backup = False
            else:
                is_backup = starter_name is not None
                if config.USE_SNAP_BACKUP and not is_backup:
                    share = qb_snap_share_map.get((p.team, normalize_player_name(base_name)))
                    is_backup = share is not None and share < config.BACKUP_SNAP_THRESHOLD
            if is_backup and not starter_injured:
                return True
        return False
    if p.team in BLOCKED_TEAMS:
        return True
    if p.pos == 'D' and p.cost > 5000:
        return True
    if (p.pos == 'QB' and p.cost < MIN_QB_SALARY) or p.cost > MAX_SALARY:
        return True
    cost_filter = p.pos != 'QB' and (p.cost > MAX_SALARY or played < 1)
    return (p.proj < 0 and p.pos != 'D') or (p.proj < 10 and p.pos == 'QB') or cost_filter


def build_optimizer_settings(players, block_function):
    """Custom rules: block players, and at most 1 per position per team."""
    custom_rules = [
        CustomRule(
            group_a=lambda p: p,
            group_b=block_function,
            comparison=lambda sum, a, b: sum(b) == 0,
        )
    ]

    # Single game: only one version (regular or MVP) of each player
    if SINGLE_GAME:
        player_versions = defaultdict(list)
        for p in players:
            player_versions[p.name.replace(' (MVP)', '')].append(p)
        for base_name, player_group in player_versions.items():
            if len(player_group) > 1:
                custom_rules.append(
                    CustomRule(
                        group_a=lambda p, base_name=base_name: p.name == base_name or p.name == f"{base_name} (MVP)",
                        group_b=lambda p: False,
                        comparison=lambda sum, a, b: sum(a) <= 1,
                    )
                )

        # At most 2 QBs total in a single-game lineup, including the MVP.
        custom_rules.append(
            CustomRule(
                group_a=lambda p: base_position(p) == 'QB',
                group_b=lambda p: False,
                comparison=lambda sum, a, b: sum(a) <= 2,
            )
        )
        return OptimizerSettings(custom_rules=custom_rules, min_teams=2)

    # Non-single: at most 1 player per (position, team)
    for pos in ['RB', 'WR', 'QB', 'TE']:
        grouped = defaultdict(list)
        for p in players:
            if p.pos == pos:
                grouped[p.team].append(p)
        for team, group in grouped.items():
            if len(group) > 1:
                custom_rules.append(
                    CustomRule(
                        group_a=lambda p, team=team, pos=pos: p.pos == pos and p.team == team,
                        group_b=lambda p: False,
                        comparison=lambda sum, a, b: sum(a) <= 1,
                    )
                )

    return OptimizerSettings(custom_rules=custom_rules, min_teams=3)


def optimize_lineup(players):
    """Run draftfast's weighted optimizer and return the optimal roster."""
    return run(
        rule_set=ACTIVE_RULE_SET,
        player_pool=players,
        verbose=False,
        optimizer_settings=build_optimizer_settings(players, block_function),
        constraints=constraints,
    )


def get_score(roster):
    return sum(p.proj for p in roster.players)


def _safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == '':
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def print_optimized_roster(roster):
    """Print the optimized roster as a formatted table."""
    teams_in_roster = {p.team for p in roster.players}
    num_teams_in_roster = len(teams_in_roster)
    max_teams_possible = len(set_teams)

    print("\n" + "=" * 120)
    print(f"OPTIMIZED LINEUP (total_score={get_score(roster):.2f}, teams={num_teams_in_roster}/{max_teams_possible})\n---")

    position_order = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'D': 4, 'MVP': 5, 'FLEX': 6}

    roster_data = []
    total_salary = 0
    for p in roster.players:
        base_fppg = _safe_float(p.kv_store.get('FPPG'))
        salary = int(p.cost)
        opponent = get_opponent(p)
        spread = favor_map.get(p.team, 0)
        total_salary += salary
        weather_factor = _safe_float(p.kv_store.get('weather_factor'), 1.0)

        roster_data.append({
            'Slot': f"{p.pos:5}",
            'Name': p.name[:25],
            'Team': p.team,
            'Opp': opponent,
            'Salary': f"${salary:,}",
            'Weather': f"{weather_factor:.3f}",
            'Base Proj': f"{base_fppg:6.2f}",
            'Weighted Proj': f"{p.proj:6.2f}",
            'Value': f"{(p.proj / salary) * 1000:6.1f}x",
            'Spread': f"{spread:+.1f}",
            'pos_order': position_order.get(p.pos, 99),
        })

    roster_data.sort(key=lambda x: x['pos_order'])

    print(f"{'Slot':<6} {'Name':<24} {'Team':<5} {'Opp':<5} {'Salary':<10} {'Weather':<8} {'Base Proj':<12} {'Weighted Proj':<15} {'Value':<10} {'Spread':<8}")
    print("-" * 125)
    for row in roster_data:
        print(f"{row['Slot']:<6} {row['Name']:<24} {row['Team']:<5} {row['Opp']:<5} {row['Salary']:<10} {row['Weather']:<8} {row['Base Proj']:<12} {row['Weighted Proj']:<15} {row['Value']:<10} {row['Spread']:<8}")

    print("-" * 120)
    print(f"{'TOTAL':<6} {'':<24} {'':<5} ${total_salary:,} {'':<12} {get_score(roster):<15.2f}")
    print("-" * 3)


roster = optimize_lineup(players)
if roster:
    print_optimized_roster(roster)
else:
    print("No solution")

# ============================================================
# Diversity analysis (classic slates only)
# ============================================================


def calculate_diversity_score(roster):
    """Score a roster on team/position diversification and stacking risk (0-100)."""
    if not roster or not roster.players:
        return {'overall_score': 0, 'team_diversity': 0, 'position_diversity': 0}

    non_defense_players = [p for p in roster.players if p.pos not in ('D', 'DEF')]
    if not non_defense_players:
        return {'overall_score': 0, 'team_diversity': 0, 'position_diversity': 0}

    team_counts = defaultdict(int)
    position_counts = defaultdict(int)
    team_position_pairs = defaultdict(int)
    for p in non_defense_players:
        team_counts[p.team] += 1
        position_counts[p.pos] += 1
        team_position_pairs[(p.team, p.pos)] += 1

    roster_size = len(non_defense_players)
    num_teams = len(team_counts)

    # Team diversity (0-40)
    ideal_teams = min(roster_size, 4)
    team_diversity = min((num_teams / ideal_teams) * 40, 40)

    # Position diversity (0-30)
    position_variance = np.var(list(position_counts.values())) if position_counts else 0
    position_diversity = 30 * (1 - min(position_variance / roster_size, 1))

    # Stack penalty (0-30)
    stack_penalty_points = 0
    for (team, pos), count in team_position_pairs.items():
        if count > 1:
            stack_penalty_points += (count - 1) * 5
    for team, count in team_counts.items():
        if count >= 3:
            stack_penalty_points += (count - 2) * 5
    stack_penalty = 30 - min(stack_penalty_points, 30)

    overall_score = min(team_diversity + position_diversity + stack_penalty, 100)

    return {
        'overall_score': round(overall_score, 1),
        'team_diversity': round(team_diversity, 1),
        'position_diversity': round(position_diversity, 1),
        'stack_penalty': round(stack_penalty, 1),
        'team_breakdown': dict(team_counts),
        'position_breakdown': dict(position_counts),
        'num_teams': num_teams,
        'num_unique_positions': len(position_counts),
    }


if roster and not SINGLE_GAME:
    diversity = calculate_diversity_score(roster)
    opt_score = get_score(roster)

    print("\nROSTER DIVERSITY ANALYSIS")
    print("-" * 8)
    print(f"Overall Diversity Score: {diversity['overall_score']}/100")
    print(f"  - Team Diversity:      {diversity['team_diversity']}/40")
    print(f"  - Position Diversity:  {diversity['position_diversity']}/30")
    print(f"  - Stack Penalty:       {diversity['stack_penalty']}/30")
    print(f"\nTeams Used: {diversity['num_teams']} - {diversity['team_breakdown']}")
    print(f"Positions: {diversity['position_breakdown']}")

    if diversity['overall_score'] < 50 and opt_score >= 45:
        print("\nWARNING: HIGH CORRELATION RISK DETECTED")
        print(f"Optimization Score: {opt_score:.2f} (good)")
        print(f"Diversity Score: {diversity['overall_score']}/100 (LOW - target 70+)")
        print("\nThis lineup has high potential but HIGH VARIANCE due to:")
        if diversity['team_diversity'] < 20:
            most_common_team = max(diversity['team_breakdown'].items(), key=lambda x: x[1])
            print(f"  • Team concentration: {most_common_team[1]} players from {most_common_team[0]} (too concentrated)")
        if diversity['position_diversity'] < 15:
            most_common_pos = max(diversity['position_breakdown'].items(), key=lambda x: x[1])
            print(f"  • Position imbalance: {most_common_pos[1]} {most_common_pos[0]}s (unbalanced)")
        if diversity['stack_penalty'] < 15:
            print("  • Team stacking: Multiple players from same team in same position (correlation)")
        print("\nConsider adjusting constraints to improve diversity if you want:")
        print("  • Lower variance/safer lineups")
        print("  • Better hedge against team-specific outcomes")
        print("  • More balanced exposure")
        print("=" * 80 + "\n")
    elif diversity['overall_score'] < 70:
        print(f"\nDiversity score ({diversity['overall_score']}) could be improved (target: 70+)\n")

# ============================================================
# Write upload CSV in FanDuel template column order
# ============================================================

if roster:
    ORDERED_COLS = ['QB', 'RB', 'RB', 'WR', 'WR', 'WR', 'TE', 'FLEX', 'DEF']

    def get_match_names(col):
        if col == 'DEF':
            return ['D']
        elif col == 'FLEX':
            return ['RB', 'WR']
        return [col]

    headers = []
    player_names = []
    roster_copy = roster.players.copy()
    for c in ORDERED_COLS:
        headers.append(c)
        match_names = get_match_names(c)
        for r in roster_copy:
            if r.pos in match_names:
                player_names.append(f"{r.kv_store['Id']}:{r.name}")
                roster_copy.remove(r)
                break

    with open(UPLOAD_FILE, 'w') as f:
        f.write(','.join(headers))
        f.write('\n')
        f.write(','.join(player_names))

print('done')
