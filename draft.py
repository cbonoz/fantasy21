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
from draftfast.settings import OptimizerSettings, CustomRule, PlayerPoolSettings
from nfl_teams import NFL_TEAM_MAP
import numpy as np
import pandas as pd
from odds import get_fantasy_def_points_against, get_metabet_spread
from projection import (
    calculate_precipitation_factor,
    calculate_temperature_factor,
    calculate_wind_factor,
    cap_projection,
    compute_team_totals,
)
from weather import display_weather_summary, get_nfl_weather

import config


def get_most_recently_created_file_with_extension(folder, extension):
    files = [f for f in os.listdir(folder) if f.endswith(extension)]
    return max(files, key=lambda x: os.path.getctime(f"{folder}/{x}"))


def get_week_relative_to_start(season_start=config.SEASON_START):
    start = datetime.strptime(season_start, '%m/%d/%Y')
    today = datetime.today()
    num_days = (today - start).days + 1
    return math.ceil(num_days / 7)


WEEK = max(get_week_relative_to_start(), 1)
SALARY_FILE = f"{config.DATA_FOLDER}/{get_most_recently_created_file_with_extension(config.DATA_FOLDER, 'csv')}"
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
    fpa_map = get_fantasy_def_points_against(WEEK) or {}
except Exception as e:
    print('Error loading FPA data:', e)
    fpa_map = {}

num_teams = df['Team'].nunique()

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
# Historical averages (momentum weighting)
# ============================================================

REPLACE_MAP = {
    'LA': 'Los Angeles',
    '.': '',
}


def name_map(x):
    result = ' '.join(x.split(', ')[::-1])
    for k in REPLACE_MAP:
        result = result.replace(k, REPLACE_MAP[k])
    return result


start_week = WEEK - 6
file_names = [f"{config.HISTORY_FOLDER}/week{week_number}.csv" for week_number in range(start_week, WEEK + 1)
              if week_number != 18 and os.path.isfile(f"{config.HISTORY_FOLDER}/week{week_number}.csv")]
history_dfs = [pd.read_csv(f, delimiter=";") for f in file_names]
print(f"Using {len(history_dfs)} weeks of history")

historic_averages = {}
if history_dfs:
    historic_data = pd.concat(history_dfs)
    historic_data['Name'] = historic_data['Name'].apply(name_map)
    team_data = historic_data[historic_data['Pos'] == 'Def']

    historic_averages = historic_data.groupby("Name").mean()['FD points'].to_dict()
    historic_averages['Patrick Mahomes'] = historic_averages['Patrick Mahomes II']
    historic_averages['Darrell Henderson Jr'] = historic_averages['Darrell Henderson']

    team_averages = team_data.groupby("Team").mean()['FD points'].to_dict()
    for short, full in [('gb', 'gnb'), ('kc', 'kan'), ('ne', 'nwe'), ('tb', 'tam'),
                        ('lv', 'lvr'), ('no', 'nor'), ('sf', 'sfo')]:
        team_averages[short] = team_averages.get(full)
    historic_averages.update(team_averages)

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
ACTIVE_RULE_SET.salary_min = ACTIVE_RULE_SET.salary_max - config.SALARY_MIN_OFFSET
if not SINGLE_GAME:
    ACTIVE_RULE_SET.max_players_per_team = config.MAX_PLAYERS_PER_TEAM_CLASSIC
ACTIVE_RULE_SET.roster_size = config.ROSTER_SIZE_CLASSIC if not SINGLE_GAME else config.ROSTER_SIZE_SINGLE

ALL_POSITIONS = [*ACTIVE_RULE_SET.defensive_positions, *ACTIVE_RULE_SET.offensive_positions]

m_score = df.groupby(['Position'])['FPPG'].mean().to_dict()
m_score['DEF'] = m_score['D']

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

historic_data_used = 0


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


def filter_mvps(mvps, players, starter_map):
    """Include MVP candidates who are starters, or backups whose starter is injured."""
    filtered_mvps = []
    for name, proj, cost, value, pos, base_fppg, opponent in sorted(mvps, key=lambda x: x[3], reverse=True):
        base_name = name.replace(' (MVP)', '')
        player_games = 0
        player_team = None
        player_pos = None
        for p in players:
            if p.name == base_name:
                player_games = int(float(p.kv_store.get('Played', 0)))
                player_team = p.team
                player_pos = p.pos
                break

        status = "Starter"
        is_backup = player_games < MIN_PLAYED
        starter_injured = False
        if is_backup and player_team and player_pos in ['QB', 'RB', 'WR', 'TE']:
            starter_name, _, is_injured = starter_map.get((player_team, player_pos), (None, None, False))
            if is_injured:
                starter_injured = True
                status = f"(Starter {starter_name} injured)"

        if player_games >= MIN_PLAYED or starter_injured:
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
    allowed = entry.get('allowed') if isinstance(entry, dict) else None
    if not allowed:
        return 0
    return (float(allowed) - 20.0) * config.FPA_WEIGHT  # 20 = rough league-average FD points allowed


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
    global historic_data_used
    history_value = historic_averages.get(history_key)
    if history_value:
        historic_data_used += 1
        return AVERAGE_WEIGHT * p.proj + (1 - AVERAGE_WEIGHT) * history_value
    return p.proj


weather_factor_map = {}
if not weather_df.empty:
    for team in set(list(weather_df['away_team'].dropna()) + list(weather_df['home_team'].dropna())):
        weather_info = get_weather_for_team(team, weather_df)
        if weather_info:
            wind = weather_info.get('wind_speed')
            temp = weather_info.get('temperature')
            precip = weather_info.get('precipitation_chance')
            for pos in ALL_POSITIONS:
                combined_factor = (calculate_wind_factor(wind, pos)
                                   * calculate_temperature_factor(temp, pos)
                                   * calculate_precipitation_factor(precip, pos))
                weather_factor_map[(team, pos)] = combined_factor


def calculate_adjusted_projection(p):
    """Weighted projection: base blended with history, adjusted by matchup factors."""
    if not WEIGHTED:
        return p.proj

    # Kickers and cheap non-defense players just blend with historical average
    if p.pos == 'K' or (p.cost <= config.LOW_SALARY_SKIP and p.pos != 'D'):
        return get_blended_projection(p, name_map(p.name))

    base_score = get_blended_projection(p, name_map(p.name) if p.pos not in ['D', 'MVP'] else p.team.lower())

    teams = p.matchup.split('@')
    opponent = teams[0] if p.team == teams[1] else teams[1]

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
    p.average_score = m_score[p.pos if p.pos in m_score else p.pos.replace('MVP', 'D')]
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
    if p.matchup:
        teams = p.matchup.split('@')
        opponent = teams[0].strip() if p.team == teams[1].strip() else teams[1].strip()
    else:
        opponent = 'N/A'

    if p.pos == 'MVP':
        mvps.append((p.name, p.proj, p.cost, p.proj / p.cost, p.pos, base_fppg, opponent))
    elif p.pos == 'D':
        defenses.append((p.team, p.proj, p.cost, p.proj / p.cost, base_fppg, favor_map.get(p.team, 0), opponent))
    elif p.pos == 'QB' and p.cost >= MIN_QB_SALARY:
        team_total = team_totals.get(p.team, avg_team_total)
        total_deviation = team_total - avg_team_total
        qbs.append((name_map(p.name), p.proj, p.cost, p.proj / p.cost, team_total, total_deviation, base_fppg, opponent))

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
print(f"{'Name':<35} {'Proj':>8} {'Salary':>10} {'Value':>8} {'TeamTotal':>10} {'Dev':>8} {'Opp':>5} {'Base':>8}")
print("-" * 105)
for name, proj, cost, value, team_total, total_deviation, base_fppg, opp in sorted(qbs, key=lambda x: x[3], reverse=True):
    print(f"{name:<35} {proj:>8.2f} ${cost:>9,.0f} {(value * 1000):>7.1f}x {team_total:>10.2f} {total_deviation:>8.2f} {opp:>5} {base_fppg:>8.2f}")

display_weather_summary(weather_df)

# ============================================================
# Optimizer
# ============================================================

if SINGLE_GAME:
    BANNED = config.BANNED_SINGLE
else:
    BANNED = config.BANNED_CLASSIC
BLOCKED_TEAMS = config.BLOCKED_TEAMS

player_settings = PlayerPoolSettings()
MIN_PROJ = 0
constraints = LineupConstraints(locked=config.LOCKED, banned=BANNED)


def block_function(p):
    store = p.kv_store
    played = int(float(store.get('Played') or 0))
    if SINGLE_GAME:
        return played < MIN_PLAYED
    if p.team in BLOCKED_TEAMS:
        return True
    if p.pos == 'D' and p.cost > 5000:
        return True
    if p.pos == 'QB' and p.cost < MIN_QB_SALARY or p.cost > MAX_SALARY:
        return True
    cost_filter = p.pos != 'QB' and (p.cost > MAX_SALARY or played < 1)
    return (p.proj < MIN_PROJ and p.pos != 'D') or (p.proj < 10 and p.pos == 'QB') or cost_filter


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
        player_versions = {}
        for p in players:
            base_name = p.name.replace(' (MVP)', '')
            player_versions.setdefault(base_name, []).append(p)
        for base_name, player_group in player_versions.items():
            if len(player_group) > 1:
                custom_rules.append(
                    CustomRule(
                        group_a=lambda p, base_name=base_name: p.name == base_name or p.name == f"{base_name} (MVP)",
                        group_b=lambda p: False,
                        comparison=lambda sum, a, b: sum(a) <= 1,
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


def get_score(roster):
    return sum([p.proj for p in roster.players])


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
        base_fppg = float(p.kv_store.get('FPPG', 0))
        salary = int(p.cost)
        if p.matchup:
            teams = p.matchup.split('@')
            opponent = teams[0].strip() if p.team == teams[1].strip() else teams[1].strip()
        else:
            opponent = 'N/A'
        spread = favor_map.get(p.team, 0)
        total_salary += salary
        weather_factor = p.kv_store.get('weather_factor', 1.0)

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

    print(f"{'Slot':<6} {'Name':<27} {'Team':<5} {'Opp':<5} {'Salary':<10} {'Weather':<8} {'Base Proj':<12} {'Weighted Proj':<15} {'Value':<10} {'Spread':<8}")
    print("-" * 128)
    for row in roster_data:
        print(f"{row['Slot']:<6} {row['Name']:<27} {row['Team']:<5} {row['Opp']:<5} {row['Salary']:<10} {row['Weather']:<8} {row['Base Proj']:<12} {row['Weighted Proj']:<15} {row['Value']:<10} {row['Spread']:<8}")

    print("-" * 120)
    print(f"{'TOTAL':<6} {'':<27} {'':<5} ${total_salary:,} {'':<12} {get_score(roster):<15.2f}")
    print("-" * 3)


best_roster = None
best_score = 0

opt_settings = build_optimizer_settings(players, block_function)
roster = run(
    rule_set=ACTIVE_RULE_SET,
    player_pool=players,
    verbose=False,
    optimizer_settings=opt_settings,
    constraints=constraints,
    player_settings=player_settings,
)

if roster:
    print_optimized_roster(roster)
    current_score = get_score(roster)
    if not best_score or current_score > best_score:
        best_score = current_score
        best_roster = roster
else:
    print("No solution")

# ============================================================
# Diversity analysis (classic slates only)
# ============================================================


def calculate_diversity_score(roster):
    """Score a roster on team/position diversification and stacking risk (0-100)."""
    if not roster or not roster.players:
        return {'overall_score': 0, 'team_diversity': 0, 'position_diversity': 0}

    non_defense_players = [p for p in roster.players if p.pos != 'DEF']
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


if best_roster and not SINGLE_GAME:
    diversity = calculate_diversity_score(best_roster)
    opt_score = get_score(best_roster)

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
