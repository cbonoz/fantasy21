# draft.py - FanDuel NFL lineup optimizer
# Generated from draft.ipynb. Run headless with: poetry run python draft.py

# ===== cell 0 =====
import sys
print(sys.version)

# ===== cell 1 =====
import sys
import os
import json
from pprint import pprint
from operator import itemgetter

from draftfast import rules
from draftfast.optimize import run
from draftfast.orm import Player
from draftfast.csv_parse import salary_download
from draftfast.settings import OptimizerSettings, CustomRule, PlayerPoolSettings
from draftfast.lineup_constraints import LineupConstraints
import pandas as pd
import numpy as np
import math
from datetime import date, datetime, timedelta
from scipy import stats
from nfl_teams import NFL_TEAM_MAP
from odds import get_metabet_spread, get_current_rankings, get_fantasy_def_points_against
from collections import defaultdict

def get_most_recently_created_file_with_extension(folder, extension):
    files = [f for f in os.listdir(folder) if f.endswith(extension)]
    return max(files, key=lambda x: os.path.getctime(f"{folder}/{x}"))

# https://www.pro-football-reference.com/years/2021/fantasy.htm
DATA_FOLDER = './data26'
SEASON_START = '09/13/2026'

ACTIVE_FOLDER = './active'
UPLOAD_FOLDER = './upload'
SALARY_FILE = f"{DATA_FOLDER}/{get_most_recently_created_file_with_extension(DATA_FOLDER, 'csv')}"

def get_week_relative_to_start(season_start=SEASON_START):
    start = datetime.strptime(season_start, '%m/%d/%Y')
    today = datetime.today()
    num_days = (today - start).days + 1
    return math.ceil(num_days / 7)


WEEK = max(get_week_relative_to_start(), 1)

WEIGHTED = True

ACTIVE_FILE = f"{ACTIVE_FOLDER}/data.csv"
UPLOAD_FILE = f"{UPLOAD_FOLDER}/upload.csv"
MAX_PLAYED = WEEK - 1

df = pd.read_csv(SALARY_FILE, na_values= '')

print('ready', SALARY_FILE, WEEK)
# print(df.head())

# ===== cell 2 =====
from weather import get_nfl_weather

# ===== cell 3 =====
# Check df immediately after loading
print(f"\nDataFrame loaded with {df.shape[0]} rows and {df.shape[1]} columns")
print(f"Positions: {df['Position'].unique()}")
print(f"Position value counts:\n{df['Position'].value_counts()}")

# ===== cell 4 =====
# Manual step: Load NFL team rankings from a local CSV or DataFrame instead of get_current_rankings.
# If you do not have a rankings file, this cell will safely skip and the rest of the notebook will work without rankings.
import os
import pandas as pd
RANKINGS_CSV = './ranking/defense_1.json'  # Update this path to your rankings file (CSV or JSON)
if os.path.isfile(RANKINGS_CSV):
    try:
        if RANKINGS_CSV.endswith('.csv'):
            ranking_df = pd.read_csv(RANKINGS_CSV)
        elif RANKINGS_CSV.endswith('.json'):
            ranking_df = pd.read_json(RANKINGS_CSV)
        else:
            raise ValueError('Unsupported file format for rankings.')
    except Exception as e:
        print('Error loading rankings:', e)
        ranking_df = pd.DataFrame()  # Empty fallback
else:
    print(f'Rankings file not found: {RANKINGS_CSV}. Proceeding without rankings.')
    ranking_df = pd.DataFrame()  # Empty fallback
    # You can also paste your DataFrame here manually if needed.

def get_abbr(x):
    try:
        short_team = NFL_TEAM_MAP[x]
        print(x, short_team)
        return short_team
    except Exception as e:
        print(e, x)

if not ranking_df.empty and 'team_fk__full_name' in ranking_df.columns:
    ranking_df['team'] = ranking_df['team_fk__full_name'].apply(get_abbr)
else:
    print('Ranking DataFrame is empty or missing expected columns.')
# DEF_KEYS = ['passing_interceptions_rank'
# ranking_df['def_rank'] = ranking_df.apply(lambda row: np.avg([row[k] for k in DEF_KEYS]))
try:
    if 'df' in globals() and 'Team' in df.columns:
        num_teams = df['Team'].nunique()
    else:
        num_teams = 0
except Exception as e:
    print('Error determining number of teams from data CSV:', e)
    num_teams = 0

ranking_df.shape
print(ranking_df.columns.values)

# ===== cell 5 =====
ranking_df.info()

# ===== cell 6 =====
# rankings = ranking_df.to_dict('records') if not ranking_df.empty else []
if ranking_df is not None and not ranking_df.empty and 'team' in ranking_df.columns:
    rankings = ranking_df.to_dict('records')
    print(rankings[0]['team'])
    rankings = {x['team']: x for x in  rankings}
    teams = set(rankings.keys())
    print(len(teams), teams)
else:
    print('No rankings data loaded. Proceeding without rankings.')
    rankings = {}
    teams = set()

# ===== cell 7 =====
spread_df = get_metabet_spread(WEEK)
favor_map = {}
z_map = {}
z_scores = {}
print(spread_df)

# ===== cell 8 =====
# Load weather data for the week
weather_df = get_nfl_weather(WEEK-1)
print(f"Weather data loaded: {weather_df.shape[0]} games")


if not weather_df.empty:
    # Create mapping from full team names to abbreviations
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

    # Convert team names to abbreviations in weather data (if needed)
    # Note: Action Network API already provides abbreviations, so we check first
    def maybe_map_team(team):
        """Map team name to abbr if it's a full name, otherwise keep as-is"""
        if team in team_name_to_abbr:
            return team_name_to_abbr[team]
        return team

    weather_df['away_team'] = weather_df['away_team'].apply(maybe_map_team)
    weather_df['home_team'] = weather_df['home_team'].apply(maybe_map_team)

    print(weather_df.head())

# ===== cell 9 =====


if 'OverUnder' in spread_df.columns.values:
    points = list(spread_df['OverUnder'])
    zs = stats.zscore(points)
    for i, p in enumerate(points):
        z_scores[p] = 0 if np.isnan(zs[i]) else zs[i]

# Vegas odds convention: PointSpread is from the home team's perspective
# Negative spread = home team is favored by that amount
# Positive spread = away team is favored by that amount
# For favor_map: positive = team is unfavored, negative = team is favored
for index, row in spread_df.iterrows():
    home, away = row['HomeTeam'], row['AwayTeam']
    # PointSpread represents home team's advantage
    # Direct mapping: home team gets the spread as-is, away team gets negated
    favor_map[home] = row['PointSpread']   # Home team (negative = favored, positive = unfavored)
    favor_map[away] = -row['PointSpread']  # Away team (opposite of home)
    if 'OverUnder' in row:
        z_map[home] = z_scores[row['OverUnder']]
        z_map[away] = z_scores[row['OverUnder']]



if 'JAX' in favor_map:
    favor_map['JAC'] = favor_map['JAX']
    z_map['JAC'] = z_map['JAX']

if 'LVS' in favor_map:
    favor_map['LV'] = favor_map['LVS']
    z_map['LV'] = z_map['LVS']

sort_key = itemgetter(1)
sorted(favor_map.items(), key=sort_key)

print(spread_df)
z_scores

# ===== cell 10 =====
# Debug: START OF FILTERING
print("\n=== START OF FILTERING CELL ===")
print(f"df shape at start: {df.shape}")
print(f"Positions in df: {df['Position'].unique()}")
print(f"Position counts at start:\n{df['Position'].value_counts()}")

set_teams = set(df['Team'])
# Automatically set SINGLE_GAME based on number of unique teams in the data
if len(set_teams) == 2:
    SINGLE_GAME = True
else:
    SINGLE_GAME = False

MIN_SALARY = 1100 if SINGLE_GAME else 4900
print('SINGLE_GAME', SINGLE_GAME, len(set_teams), set_teams, MIN_SALARY)
# df = df.fillna(df.median())
print(df.describe())

df['Name'] = df['First Name'] + " " + df['Last Name']
df['Salary/FPPG'] = df['FPPG'] / df['Salary']

# Players with injury status.
# for index, p in df[~df['Injury Indicator'].isna()].iterrows():
#     print(p['Injury Indicator'], p['Name'], p['Team'])

all_teams = favor_map.keys()
winning_teams = [x for x in all_teams if favor_map[x] < 0]

questionable_players = list(df[(~df['Injury Indicator'].isna()) | (~df['Injury Details'].isna())]['Name'])
low_salary_players = list(df[((df['Salary'] < MIN_SALARY)) & (df['Position'] != 'D')]['Name'])

excluded_players = set([*questionable_players, *low_salary_players])

# Let the optimizer decide which defenses to use based on value/merit
# The adjusted projections already incorporate team quality via favor_map weighting

readd = ['Justin Herbert', 'George Kittle', 'Patrick Taylor Jr.', 'Bailey Zappe', "D'Andre Swift", 'David Montgomery',]
for p in readd:
    if p in excluded_players:
        excluded_players.remove(p)

# excluded_players = ["Rob Gronkowski"]
print(f"Excluding: {len(excluded_players)}")

questionable_df = df[(df['Name'].isin(set(questionable_players)))]

excluded_df = df[(df['Name'].isin(excluded_players))]

df = df[(~df['Name'].isin(excluded_players))]# & (df['FPPG'] > 0) & (df['Played'] > 0)

# ===== cell 11 =====
# Debug: Check df after filtering
print("\n=== AFTER FILTERING ===")
print(f"df shape: {df.shape}")
print(f"Positions in df: {df['Position'].unique()}")
print(f"Position counts:\n{df['Position'].value_counts()}")
print(f"\nExcluded players: {len(excluded_players)}")
print(f"Sample excluded players: {list(excluded_players)[:10]}")

# ===== cell 12 =====
# print all in df
# for index, p in enumerate(low_salary_players):
#     print(p)

# ===== cell 13 =====
REPLACE_MAP = {
    'LA': 'Los Angeles',
    '.':'',
}

def name_map(x):
    result = ' '.join(x.split(', ')[::-1])
    for k in REPLACE_MAP:
        result = result.replace(k, REPLACE_MAP[k])
    return result

# ===== cell 14 =====
print(winning_teams)

# ===== cell 15 =====
# http://rotoguru1.com/cgi-bin/fyday.pl?week=1&game=fd&scsv=1

start_week = WEEK-6# take last few games for momentum weighting.

file_names = [f"./history/week{week_number}.csv" for week_number in range(start_week, WEEK+1) if week_number != 18 and os.path.isfile(f"./history/week{week_number}.csv")]
print(file_names, len(file_names))

history_dfs = [pd.read_csv(f, delimiter=";") for f in file_names]
print(f"Using {len(history_dfs)} weeks of history")
historic_averages = {}
if history_dfs:
    historic_data=pd.concat(history_dfs)



    historic_data['Name'] = historic_data['Name'].apply(name_map)
    team_data = historic_data[historic_data['Pos'] == 'Def']

    historic_data[:1]
    historic_averages = historic_data.groupby("Name").mean()['FD points'].to_dict()
    historic_averages['Patrick Mahomes'] = historic_averages['Patrick Mahomes II']
    historic_averages['Darrell Henderson Jr'] = historic_averages['Darrell Henderson']
    # historic_averages

    team_averages = team_data.groupby("Team").mean()['FD points'].to_dict()
    team_averages['gb'] = team_averages.get('gnb')
    team_averages['kc'] = team_averages.get('kan')
    team_averages['ne'] = team_averages.get('nwe')
    team_averages['tb'] = team_averages.get('tam')
    team_averages['lv'] = team_averages.get('lvr')
    team_averages['no'] = team_averages.get('nor')
    team_averages['sf'] = team_averages.get('sfo')
    # print(team_averages)

    for k,v in team_averages.items():
        historic_averages[k] = team_averages[k]
    print(len(historic_averages))

# ===== cell 16 =====
excluded_bonus = defaultdict(lambda: 0)
injured_qb = defaultdict(lambda: False)

INJURY_FACTOR = .12

for index, p in questionable_df.iterrows():
    pos = p['Position']
    if pos in ['TE', 'WR', 'RB', 'QB']:
        points = p['FPPG']
        if points > 7.5 and p['Played'] >= WEEK / 2:
            injury_offset = min(points * INJURY_FACTOR, INJURY_FACTOR*10)
            if pos == 'QB':
                # subtract for QB
                amt = -injury_offset*2
                injured_qb[p['Team']] = True
            elif pos in ('RB', 'WR', 'TE'):
                amt = injury_offset*1.2
            else:
                amt = injury_offset

            print('bonus injured', p['Team'], p['Name'], amt)

            excluded_bonus[p['Team']] += amt

print(excluded_bonus)

# ===== cell 20 =====
df.to_csv(ACTIVE_FILE)
print(f"wrote {ACTIVE_FILE}, {df.shape}")

# ===== cell 21 =====
# https://www.fanduel.com/nfl-guide
# Use min/max + roster_size to accomodate flex position. FD roster_size = 9
all_positions = set(df['Position'])
print(all_positions)


def get_nfl_positions():
    if SINGLE_GAME:
        positions = [[p, 0, 5] for p in all_positions]
        positions.append(['MVP', 1, 1])  # Exactly 1 MVP required
        return positions

    return [
        ['QB', 1, 1],
        ['RB', 2, 3],
        ['WR', 3, 4],
        ['TE', 1, 2],
        ['D', 1, 1]
    ]

print(get_nfl_positions())

# ===== cell 22 =====
m_score = df.groupby(['Position'])['FPPG'].mean().to_dict()
m_score['DEF'] = m_score['D']

# ===== cell 23 =====
# Debug: Check what's in df before groupby
print("DataFrame shape:", df.shape)
print("Positions in df:", df['Position'].unique())
print("Number of each position:", df['Position'].value_counts())
print("\nFirst few rows:")

# ===== cell 24 =====
max_salary = np.percentile(df[df['Salary'] >= MIN_SALARY]['Salary'], 99)
max_salary = 9900
print(max_salary)

# ===== cell 25 =====
qbs = df[df['Position'] == 'QB']

# ===== cell 26 =====
ACTIVE_RULE_SET = rules.FD_NFL_RULE_SET
# Overrides (position limits, salary, roster size, positions, etc.
ACTIVE_RULE_SET.salary_max = 60000

ACTIVE_RULE_SET.defensive_positions = ['D', 'DEF']
ACTIVE_RULE_SET.offensive_positions = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'WR/FLEX', 'K', 'MVP'] if SINGLE_GAME else ['QB', 'RB', 'WR', 'TE', 'FLEX', 'WR/FLEX', 'K']
ACTIVE_RULE_SET.position_limits = get_nfl_positions()
ACTIVE_RULE_SET.salary_min = ACTIVE_RULE_SET.salary_max - (200 if SINGLE_GAME else 100)

if not SINGLE_GAME:
  ACTIVE_RULE_SET.max_players_per_team = 9

ACTIVE_RULE_SET.roster_size = 9 if not SINGLE_GAME else 6
print(ACTIVE_RULE_SET.__dict__)

ALL_POSITIONS = [*ACTIVE_RULE_SET.defensive_positions, *ACTIVE_RULE_SET.offensive_positions]

# ===== cell 27 =====
print(z_map)

# ===== cell 28 =====
# Display weather conditions and their impact on positions
print("\n" + "="*120)
print("WEATHER CONDITIONS BY TEAM AND POSITION IMPACT")
print("="*120)

weather_summary = {}
for _, game in weather_df.iterrows():
    for team in [game.get('away_team'), game.get('home_team')]:
        # Only process if team is a valid string (not None, NaN, or numeric)
        if team and isinstance(team, str) and team not in weather_summary:
            weather_info = {
                'temp': game.get('temperature'),
                'wind': game.get('wind_speed'),
                'precip': game.get('precipitation_chance'),
                'condition': game.get('condition'),
                'is_home': team == game.get('home_team')
            }
            weather_summary[team] = weather_info

# Display weather by team with position impact
print(f"{'Team':<8} {'Temp':<8} {'Wind':<8} {'Precip':<10} {'Condition':<12} {'Home/Away':<10} {'Impact on Positions':<50}")
print("-"*120)

for team in sorted(weather_summary.keys()):
    info = weather_summary[team]
    temp = f"{info['temp']}°F" if info['temp'] else "N/A"
    wind = f"{info['wind']}mph" if info['wind'] else "N/A"
    precip = f"{info['precip']}%" if info['precip'] else "N/A"
    home_away = "HOME" if info['is_home'] else "AWAY"
    condition = info['condition'] or "N/A"

    # Position impact summary - prioritize primary weather factors to avoid double counting
    impacts = set()  # Use set to avoid duplicates

    # Cold weather (<35°F) is the dominant factor - hurts passing, benefits rushing
    if info['temp'] and info['temp'] < 35:
        impacts.add("QB↓")  # QB penalized in cold
        impacts.add("WR↓")  # WR penalized in cold
        impacts.add("RB↑")  # RB benefits from running-heavy game in cold
    # If not cold, check other factors
    else:
        # Moderate wind (>8mph) favors running game, hurts passing
        if info['wind'] and info['wind'] > 8:
            impacts.add("RB↑")  # RB benefits from wind
            impacts.add("QB/WR↓")  # QB/WR penalized

    # Any precipitation (>15%) hurts passing game - add only if not already cold and condition is not clear
    is_clear_condition = condition and 'clear' in str(condition).lower()
    if info['precip'] and info['precip'] > 15 and not (info['temp'] and info['temp'] < 35) and not is_clear_condition:
        impacts.add("QB↓")  # QB penalized by rain/snow
        impacts.add("WR↓")  # WR penalized by rain/snow

    # Warm/hot weather (>85°F) benefits passing game, increases scoring
    if info['temp'] and info['temp'] > 85:
        impacts.add("QB↑")  # QB benefits from warm
        impacts.add("WR↑")  # WR benefits from warm

    # Ideal passing conditions: low wind (<5mph), low precip, moderate temp (35-85°F)
    has_low_wind = info['wind'] is None or (isinstance(info['wind'], (int, float)) and info['wind'] <= 5)
    has_low_precip = info['precip'] is None or (isinstance(info['precip'], (int, float)) and info['precip'] <= 15) or (isinstance(info['precip'], float) and np.isnan(info['precip']))
    has_moderate_temp = info['temp'] is None or (isinstance(info['temp'], (int, float)) and 35 <= info['temp'] <= 85)

    if has_low_wind and has_low_precip and has_moderate_temp and not impacts:
        impacts.add("QB↑")  # QB benefits from clear conditions
        impacts.add("WR↑")  # WR benefits from clear conditions

    # Sort impacts for consistent display: positive first, then negative
    impact_list = sorted(impacts, key=lambda x: (x[-1] != '↑', x))
    impact_str = " ".join(impact_list) if impact_list else "Neutral"

    print(f"{team:<8} {temp:<8} {wind:<8} {precip:<10} {condition:<12} {home_away:<10} {impact_str:<50}")

print("="*120)
print("\nPosition Impact Legend:")
print("  RB↑ = Running Backs benefit (wind or cold favors rushing)")
print("  QB↑/WR↑ = Quarterbacks/Wide Receivers benefit (warm weather, clear conditions)")
print("  QB↓/WR↓ = Quarterbacks/Wide Receivers penalized (high wind, cold, or rain/snow hurts passing)")
print("="*120)

# ===== cell 29 =====
def get_weather_for_team(team, weather_df):
    """
    Find weather data for a given team's game.

    Returns dict with: temperature, wind_speed, precipitation_chance, condition
    or empty dict if not found.
    """
    if weather_df.empty:
        return {}

    game = weather_df[
        (weather_df['away_team'] == team) |
        (weather_df['home_team'] == team)
    ]

    if game.empty:
        return {}

    game_data = game.iloc[0]
    return {
        'temperature': game_data.get('temperature'),
        'wind_speed': game_data.get('wind_speed'),
        'precipitation_chance': game_data.get('precipitation_chance'),
        'condition': game_data.get('condition'),
        'away_team': game_data.get('away_team'),
        'home_team': game_data.get('home_team')
    }


def is_home_team(team, weather_info):
    """Check if team is home or away based on weather data."""
    if not weather_info:
        return False
    return team == weather_info.get('home_team')


def calculate_wind_factor(wind_speed, pos):
    """
    Calculate wind adjustment factor by position.

    Wind affects different positions differently:
    - QB/WR/TE: Negative impact (harder to throw/catch accurate passes)
    - RB: Minor positive impact (more carries due to game script, fewer passing plays)
    - K: Significant negative impact (field goals affected)
    - D: Slight positive impact (more disruption, turnovers)

    Args:
        wind_speed: Wind speed in mph (int or float)
        pos: Player position (str)

    Returns:
        float: Adjustment factor (< 1.0 = penalty, > 1.0 = bonus, 1.0 = no change)
    """
    if not wind_speed or wind_speed < 2:
        return 1.0

    if pos == 'QB':
        # QB heavily penalized by wind (affects accuracy)
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
        # WR significantly penalized (catching becomes difficult)
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
        # TE similar to WR but slightly more resilient
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
        # RB benefits slightly from wind (more carries, less passing)
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
        # Kicker heavily penalized by wind
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

    elif pos == 'D':
        # Defense slightly benefits from wind (more chaos, turnovers)
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

    # MVP: Use D adjustments (defensive value)
    elif pos == 'MVP':
        if wind_speed < 5:
            return 1.0
        elif wind_speed < 10:
            return 1.01
        elif wind_speed < 15:
            return 1.02
        elif wind_speed < 20:
            return 1.02
        else:
            return 1.03

    return 1.0


def calculate_temperature_factor(temp, pos):
    """
    Calculate temperature adjustment factor by position.

    Temperature affects positions differently:
    - Cold: Fewer passing plays, more rushing -> RB up, QB/WR down
    - Hot: More passing plays, higher scoring -> QB/WR up, D down
    - QB/WR/TE: Benefit from warm weather (better throwing/catching)
    - RB: Slightly benefits from cold (run-heavy)
    - D: Penalized by warm weather (higher scoring)

    Args:
        temp: Temperature in Fahrenheit (int or float)
        pos: Player position (str)

    Returns:
        float: Adjustment factor
    """
    if not temp or temp < -10 or temp > 130:
        return 1.0

    if pos == 'QB':
        # QB performs better in warm weather
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
        # WR/TE benefit from warm (easier catching, more plays)
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
        # RB slightly benefits from cold (more run-heavy)
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
        # Kicker relatively stable but slightly worse in extremes
        if temp < 0:
            return 0.98
        elif temp < 20:
            return 0.99
        elif temp < 100:
            return 1.0
        else:
            return 0.99

    elif pos == 'D':
        # Defense penalized in warm weather (higher scoring)
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

    elif pos == 'MVP':
        # MVP uses defense scaling for temperature
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
    """
    Calculate precipitation adjustment factor by position.

    Rain/snow affects:
    - QB/WR: Negative (harder to throw/catch accurately)
    - RB: Positive (more carries, worse weather -> running game)
    - K: Negative (field goals affected)
    - D: Positive (turnovers, more chaos)

    Args:
        precip_chance: Precipitation chance as percentage (0-100)
        pos: Player position (str)

    Returns:
        float: Adjustment factor
    """
    if not precip_chance or precip_chance < 5:
        return 1.0

    if pos == 'QB':
        # QB penalized by precipitation
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.97
        elif precip_chance < 75:
            return 0.94
        else:
            return 0.90

    elif pos in ['WR', 'TE']:
        # WR/TE penalized (catching harder)
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.96
        elif precip_chance < 75:
            return 0.92
        else:
            return 0.87

    elif pos == 'RB':
        # RB benefits from precipitation (running game)
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.03
        elif precip_chance < 75:
            return 1.05
        else:
            return 1.07

    elif pos == 'K':
        # Kicker penalized by precipitation
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 0.98
        elif precip_chance < 75:
            return 0.95
        else:
            return 0.90

    elif pos == 'D':
        # Defense benefits from precipitation (turnovers, chaos)
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.02
        elif precip_chance < 75:
            return 1.04
        else:
            return 1.06

    elif pos == 'MVP':
        # MVP uses defense scaling
        if precip_chance < 25:
            return 1.0
        elif precip_chance < 50:
            return 1.02
        elif precip_chance < 75:
            return 1.04
        else:
            return 1.06

    return 1.0

# ===== cell 30 =====
players = salary_download.generate_players_from_csvs(salary_file_location=ACTIVE_FILE, game=rules.FAN_DUEL)

FAVOR_DIVISION = 4
AVERAGE_WEIGHT = .5
MIN_PLAYED = min(int(WEEK * 0.4), 2)

defenses = []
qbs = []
mvps = []  # Track MVP players separately
mvp_base_names = set()  # Track base names of MVP players

MIN_QB_SALARY = 1000 if SINGLE_GAME else 6400

MIN_SCORE = 7
MAX_SCORE = 27
INJURED_QB_BONUS = 1.25
HOME_BONUS = .3

historic_data_used = 0

def build_starter_map(players, questionable_df):
    """
    Build a map of (team, position) -> (starter_name, starter_fppg, is_injured).

    Only tracks positions: QB, RB, WR, TE
    Identifies starters as the highest FPPG player for each position/team combo.
    Checks if starters are injured by looking them up in questionable_df.

    Args:
        players: List of Player objects
        questionable_df: DataFrame of players with injury indicators
        MIN_PLAYED: Minimum games played to be considered a starter

    Returns:
        dict: Mapping (team, pos) -> (starter_name, starter_fppg, is_injured)
    """
    starter_map = {}
    for p in players:
        if p.pos in ['QB', 'RB', 'WR', 'TE']:
            key = (p.team, p.pos)
            base_name = p.name.replace(' (MVP)', '')
            current_fppg = float(p.kv_store.get('FPPG') or 0)

            # Check if this player is injured
            is_injured = base_name in [q['Name'] for _, q in questionable_df.iterrows()] if not questionable_df.empty else False

            # Only store if this is the highest FPPG for this position/team (starter)
            if key not in starter_map or current_fppg > starter_map[key][1]:
                starter_map[key] = (base_name, current_fppg, is_injured)

    return starter_map


def filter_mvps(mvps, players, starter_map):
    """
    Filter MVP players based on games played and starter injury status.

    Includes a player if:
    - Player has played >= MIN_PLAYED games (is a starter), OR
    - Player is a backup AND their position's starter is injured

    Args:
        mvps: List of tuples (name, proj, cost, value, pos, base_fppg, opponent)
        players: List of Player objects (to look up games played and team info)
        starter_map: Dict mapping (team, pos) -> (starter_name, starter_fppg, is_injured)
        MIN_PLAYED: Minimum games played threshold for starters

    Returns:
        list: Filtered MVP tuples with added fields (player_games, opp, status)
              Format: (name, proj, cost, value, pos, base_fppg, player_games, opp, status)
    """
    filtered_mvps = []

    for name, proj, cost, value, pos, base_fppg, opponent in sorted(mvps, key=lambda x: x[3], reverse=True):
        # Extract the base player name (remove " (MVP)" suffix)
        base_name = name.replace(' (MVP)', '')

        # Find the player in the original players list to check games played and team
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

        # Check if this is a backup but their starter is injured
        starter_injured = False
        if is_backup and player_team and player_pos in ['QB', 'RB', 'WR', 'TE']:
            key = (player_team, player_pos)
            if key in starter_map:
                starter_name, starter_fppg, is_injured = starter_map[key]
                if is_injured:
                    starter_injured = True
                    status = f"(Starter {starter_name} injured)"

        # Include if starter, or if backup but starter is injured
        if player_games >= MIN_PLAYED or starter_injured:
            filtered_mvps.append((name, proj, cost, value, pos, base_fppg, player_games, opponent, status))

    return filtered_mvps

def calculate_home_bonus(p):
    """Calculate home/away bonus."""
    teams = p.matchup.split('@')
    is_home = p.team == teams[1]
    return HOME_BONUS if is_home else -HOME_BONUS

def calculate_overunder_bonus(p, point_bonus):
    """Calculate over/under bonus based on position.
    For defenses, heavily weight the over/under since they score better in low-scoring games.
    """
    if not point_bonus:
        return 0
    if p.pos in ['D', 'MVP']:
        # For defenses: heavily penalize high O/U games, heavily reward low O/U games
        # Multiply by larger factor (3x vs 1.5x for other positions)
        return -point_bonus * 3.0
    return point_bonus * 1.5

def calculate_ranking_bonus(p, opponent):
    """Calculate ranking-based bonus."""
    current_rank = rankings.get(p.team, {}).get('points_rank_def', 0) if 'rankings' in globals() else 0
    opp_rank = (num_teams - rankings.get(opponent, {}).get('offensive_yards_rank', 0)) if 'rankings' in globals() else 0
    overall_diff = opp_rank - current_rank
    return overall_diff / num_teams

def calculate_injury_bonuses(p, opponent):
    """Calculate all injury-related bonuses."""
    bonuses = 0

    # Opponent QB injury bonus
    if injured_qb.get(opponent, False):
        qb_bonus = INJURED_QB_BONUS if p.pos in ['D', 'MVP'] else INJURED_QB_BONUS / 2
        # print(f"hurt {opponent} QB, {qb_bonus} bonus to {p.pos} {p.team} {p.name}")
        bonuses += qb_bonus

    # Team injury bonus
    if p.pos == 'QB':
        bonuses += -excluded_bonus.get(p.team, 0)
    elif p.pos in ['D', 'MVP']:
        bonuses += excluded_bonus.get(p.team, 0) / 2
    else:
        bonuses += excluded_bonus.get(p.team, 0)

    # RB bonus for injured QB on own team
    if p.pos == 'RB' and injured_qb.get(p.team, False):
        # print(f"hurt {p.team} QB, {INJURED_QB_BONUS} bonus to {p.pos} {p.name}")
        bonuses += INJURED_QB_BONUS

    return bonuses

def get_blended_projection(p, history_key):
    """
    Helper function to blend current projection with historical average.
    Returns blended projection or original projection if no history available.
    Increments historic_data_used counter when blending occurs.
    """
    global historic_data_used
    history_value = historic_averages.get(history_key)
    if history_value:
        historic_data_used += 1
        return AVERAGE_WEIGHT * p.proj + (1 - AVERAGE_WEIGHT) * history_value
    return p.proj

# ===== cell 31 =====
# Build weather factor map for all teams and positions
weather_factor_map = {}

if not weather_df.empty:
    for team in set(list(weather_df['away_team'].dropna()) + list(weather_df['home_team'].dropna())):
        weather_info = get_weather_for_team(team, weather_df)

        if weather_info:
            temp = weather_info.get('temperature')
            wind = weather_info.get('wind_speed')
            precip = weather_info.get('precipitation_chance')

            # Calculate combined weather factor for each position
            for pos in ALL_POSITIONS:
                wind_factor = calculate_wind_factor(wind, pos)
                temp_factor = calculate_temperature_factor(temp, pos)
                precip_factor = calculate_precipitation_factor(precip, pos)

                # Combine factors multiplicatively: weather_factor_map[(team, pos)] = wind * temp * precip
                combined_factor = wind_factor * temp_factor * precip_factor
                weather_factor_map[(team, pos)] = combined_factor
else:
    # If no weather data, use neutral factors (1.0) for all teams/positions
    for team in set([p.team for p in salary_download.generate_players_from_csvs(salary_file_location=ACTIVE_FILE, game=rules.FAN_DUEL)]):
        for pos in ALL_POSITIONS:
            weather_factor_map[(team, pos)] = 1.0

print(f"Weather factor map built for {len(set([k[0] for k in weather_factor_map.keys()]))} teams")

# ===== cell 32 =====

def calculate_adjusted_projection(p):
    """
    Calculate the adjusted projection for a player based on matchup factors.

    Base score is blended with historical averages, then adjusted by:
    - Spread advantage: How favorable the matchup is (Vegas spread)
    - Game script: Over/under to identify high/low scoring games
    - Home field advantage: Home vs away boost/penalty
    - Opponent strength: How good opponent defense is against this position
    - Team health: Injuries affecting the team or position

    Kickers are excluded from matchup weighting and use simple historical average blending.

    Returns a projection capped between MIN_SCORE and MAX_SCORE.
    """
    if not WEIGHTED:
        return p.proj

    # Kickers skip detailed weighting - just blend with historical average
    if p.pos == 'K':
        return get_blended_projection(p, name_map(p.name))

    # Low-salary non-defense players skip detailed weighting - just blend with historical average
    # Defenses always get matchup weighting regardless of salary
    if p.cost <= 4200 and p.pos != 'D':
        history_key = name_map(p.name)
        return get_blended_projection(p, history_key)

    # Step 1: Blend current projection with historical average
    # Historical data provides longer-term expectations
    history_key = name_map(p.name) if p.pos not in ['D', 'MVP'] else p.team.lower()
    base_score = get_blended_projection(p, history_key)

    # Step 2: Identify opponent for this matchup
    teams = p.matchup.split('@')
    opponent = teams[0] if p.team == teams[1] else teams[1]

    # Step 3: Calculate matchup adjustments
    matchup_bonus = 0

    # GAME TOTAL ADJUSTMENT: Over/under indicates high/low scoring game
    # Calculate this first since it affects spread weighting
    # High O/U = more total points in game = higher individual scoring
    # For defenses, high O/U is bad (more opponent points)
    point_bonus = z_map.get(p.team, 0)
    ou_bonus = calculate_overunder_bonus(p, point_bonus)
    matchup_bonus += ou_bonus

    # SPREAD ADJUSTMENT: Vegas spread indicates how favorable the matchup is
    # For OFFENSIVE players: positive spread = unfavored = fewer scoring opportunities
    # For DEFENSES: positive spread = facing strong offense = worse matchup
    spread_bonus = -favor_map.get(p.team, 0) / FAVOR_DIVISION

    # For defenses, invert the spread bonus (they benefit from facing bad offenses)
    if p.pos in ['D', 'MVP']:
        spread_bonus = -favor_map.get(p.team, 0) / FAVOR_DIVISION
        # In high-scoring games, even strong defenses give up points
        # So heavily dampen spread advantage in high O/U games
        spread_weight = 1.0 - (point_bonus * 0.5)  # Stronger dampening for defenses
    else:
        # In high-scoring games, spread matters less (everyone scores more)
        # In low-scoring games, spread matters more (must be on favored side)
        spread_weight = 1.0 - (point_bonus * 0.4)  # Moderate dampening for offensive players

    spread_bonus *= spread_weight
    matchup_bonus += spread_bonus

    # HOME FIELD ADVANTAGE: Home teams score more, away teams score less
    # This applies to all positions
    home_bonus = calculate_home_bonus(p)
    matchup_bonus += home_bonus

    # OPPONENT STRENGTH: How good is opponent defense?
    # (Uses rankings if available)
    ranking_bonus = calculate_ranking_bonus(p, opponent)
    matchup_bonus += ranking_bonus

    # TEAM HEALTH ADJUSTMENTS: How are injuries affecting this team/position?
    injury_bonus = calculate_injury_bonuses(p, opponent)
    matchup_bonus += injury_bonus

    # DEFENSIVE MATCHUP: For defenses and MVPs, account for opponent offense strength
    if p.pos in ['D', 'MVP']:
        matchup_bonus += excluded_bonus.get(opponent, 0) / 4

    # WEATHER ADJUSTMENTS: Use pre-computed weather factor map
    # Look up combined weather factor for this team/position
    weather_bonus = 0
    combined_weather_factor = weather_factor_map.get((p.team, p.pos), 1.0)

    if combined_weather_factor != 1.0:
        # Store weather factor on player for later display
        p.kv_store['weather_factor'] = combined_weather_factor

        # Convert multiplicative factor to additive bonus (e.g., 1.05 -> 0.05 bonus)
        # Scale by base_score to make impact proportional to player value
        weather_bonus = base_score * (combined_weather_factor - 1.0)

        # Cap weather bonus to avoid extreme adjustments
        # Use 0.20 (20%) cap to allow reasonable weather impact influence
        max_weather_bonus = base_score * 0.20
        weather_bonus = max(min(weather_bonus, max_weather_bonus), -max_weather_bonus)

        matchup_bonus += weather_bonus

    # Step 4: Apply adjustments with safeguards
    # Only apply if player has minimum baseline or is a defense/MVP
    if p.pos in ['D', 'MVP'] or base_score >= MIN_SCORE:
        adjusted_proj = min(base_score + matchup_bonus, MAX_SCORE)
        # CRITICAL: Never project negative or too low below base score.
        adjusted_proj = max(adjusted_proj, base_score * .5)

        # For defenses, cap the upside boost to 2x of base score
        # Prevents massive overestimation for favored defenses
        if p.pos == 'D':
            adjusted_proj = min(adjusted_proj, base_score * 2)

        return adjusted_proj

    return base_score

# ===== cell 33 =====

# Initialize roster position/team count map for weather scaling
# This will be populated after best_roster is determined
roster_position_team_count = {}

# First, adjust all regular players
for p in players:
    p.average_score = m_score[p.pos if p.pos in m_score else p.pos.replace('MVP','D')]
    name = p.name.replace('.', '')

    # Calculate adjusted projection for regular players
    original_proj = p.proj
    p.proj = calculate_adjusted_projection(p)

    # Store the adjusted projection for MVP creation
    p.kv_store['adjusted_proj'] = p.proj

# For single game, create MVP slot with 1.5x salary and 1.5x ADJUSTED projection
if SINGLE_GAME:
    mvp_players = []
    regular_players = []

    for p in players:
        import copy
        mvp = copy.deepcopy(p)
        mvp.pos = 'MVP'
        mvp.cost = int(round(p.cost * 1.5))
        # Use the adjusted projection for the base, then apply MVP boost
        adjusted_base = p.kv_store.get('adjusted_proj', p.proj)
        mvp.proj = adjusted_base * 1.5
        mvp.name = p.name + ' (MVP)'
        mvp.kv_store['base_name'] = p.name  # Store original name
        mvp_players.append(mvp)
        mvp_base_names.add(p.name)

        # Keep regular players - CustomRule will prevent both versions from being selected
        regular_players.append(p)

    # For single game, include both regular and MVP versions
    # The optimizer constraint will enforce that only one version per player can be selected
    players.extend(mvp_players)

# Validate that all player teams are in spread data
teams_in_data = set(favor_map.keys())
missing_teams = set()

for p in players:
    if p.team not in teams_in_data:
        missing_teams.add(p.team)

if missing_teams:
    error_msg = "The following teams are missing from spread/matchup data:\n"
    for team in sorted(missing_teams):
        player_count = sum(1 for p in players if p.team == team)
        error_msg += f"  {team} ({player_count} players affected)\n"
    raise ValueError(error_msg)

# Now sort players into appropriate lists (including MVPs)
for p in players:
    # Sort players into appropriate lists
    base_fppg = float(p.kv_store.get('FPPG') or 0)

    # Extract opponent from matchup
    if p.matchup:
        teams = p.matchup.split('@')
        opponent = teams[0].strip() if p.team == teams[1].strip() else teams[1].strip()
    else:
        opponent = 'N/A'

    if p.pos == 'MVP':
        mvps.append((p.name, p.proj, p.cost, p.proj / p.cost, p.pos, base_fppg, opponent))
    elif p.pos == 'D':
        spread = favor_map.get(p.team, 0)
        defenses.append((p.team, p.proj, p.cost, p.proj / p.cost, base_fppg, spread, opponent))
    elif p.pos == 'QB' and p.cost >= MIN_QB_SALARY:
        # print('QB', p.name, p.cost, p.proj)
        point_bonus = z_map.get(p.team, 0)
        favor_bonus = -favor_map.get(p.team, 0) / FAVOR_DIVISION
        base_fppg = float(p.kv_store.get('FPPG') or 0)
        qbs.append((name_map(p.name), p.proj, p.cost, p.proj / p.cost, point_bonus, favor_bonus, base_fppg, opponent))

print(f"players {len(players)}")
print(f"historic data used {historic_data_used} of {len(players)}")

# Build the starter map and filter MVPs using extracted functions
starter_map = build_starter_map(players, questionable_df)
filtered_mvps = filter_mvps(mvps, players, starter_map)

# ===== cell 35 =====

# Best picks

print("\n" + "="*100)
print("SORTED DEFENSES")
print("="*100)
print(f"{'Team':<8} {'Proj':>8} {'Salary':>10} {'Base':>8} {'Spread':>8} {'Opp':>5} {'Value':>8}")
print("-"*100)
for team, proj, cost, value, base_fppg, spread, opp in sorted(defenses, key=lambda x: x[3], reverse=True):
    print(f"{team:<8} {proj:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {spread:>8.2f} {opp:>5} {(value*1000):>7.1f}x")

if SINGLE_GAME:
    print("\n" + "="*100)
    print("SORTED MVPs (Top 10 - Backups Shown Only if Starter Injured)")
    print("="*100)
    print(f"{'Name':<35} {'Proj':>8} {'Salary':>10} {'Base':>8} {'Value':>8} {'Games':>7} {'Opp':>5} {'Status':<10}")
    print("-"*100)

    # filtered_mvps and starter_map are already computed from the previous cell
    # Display the filtered MVPs
    for name, proj, cost, value, pos, base_fppg, games_played, opp, status in filtered_mvps[:10]:
        print(f"{name:<35} {proj:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {(value*1000):>7.1f}x {games_played:>7.0f} {opp:>5} {status:<10}")

# ===== cell 36 =====
print("\n" + "="*105)
print("SORTED QBs")
print("="*105)
print(f"{'Name':<35} {'Proj':>8} {'Salary':>10} {'Value':>8} {'O/U':>8} {'Spread':>8} {'Opp':>5} {'Base':>8}")
print("-"*105)
for name, proj, cost, value, ou_bonus, spread_bonus, base_fppg, opp in sorted(qbs, key=lambda x: x[3], reverse=True):
    print(f"{name:<35} {proj:>8.2f} ${cost:>9,.0f} {(value*1000):>7.1f}x {ou_bonus:>8.2f} {spread_bonus:>8.2f} {opp:>5} {base_fppg:>8.2f}")

# ===== cell 37 =====
# Display weather conditions and their impact on positions
import importlib
import weather
importlib.reload(weather)
from weather import display_weather_summary
display_weather_summary(weather_df)

# ===== cell 39 =====
# Verify players are in the list and have weather-applicable data
print(f"Total players: {len(players)}")
print(f"Sample of positions in players: {set(p.pos for p in players[:20])}")
if not weather_df.empty:
  print(f"\nTeams in weather data: {set(weather_df['away_team'].tolist() + weather_df['home_team'].tolist())}")
print(f"Teams in players: {set(p.team for p in players[:30])}")

# ===== cell 40 =====
# Pre-compute weather factor map: (team, position) -> combined_weather_factor
# This allows O(1) lookup during projection calculations instead of recalculating each time
weather_factor_map = {}

# All positions that weather affects
weather_positions = ['QB', 'RB', 'WR', 'TE', 'K', 'D', 'MVP']

for _, game in weather_df.iterrows():
    for team in [game.get('away_team'), game.get('home_team')]:
        if team and isinstance(team, str):
            weather_info = {
                'wind_speed': game.get('wind_speed'),
                'temperature': game.get('temperature'),
                'precipitation_chance': game.get('precipitation_chance')
            }

            # Calculate weather factors for each position at this team
            for pos in weather_positions:
                wind_factor = calculate_wind_factor(weather_info.get('wind_speed'), pos)
                temp_factor = calculate_temperature_factor(weather_info.get('temperature'), pos)
                precip_factor = calculate_precipitation_factor(weather_info.get('precipitation_chance'), pos)

                combined_factor = wind_factor * temp_factor * precip_factor
                weather_factor_map[(team, pos)] = combined_factor

print(f"\nWeather factor map created for {len(weather_factor_map)} team/position combinations")
print(f"Sample: {list(weather_factor_map.items())[:3]}")

# ===== cell 41 =====
# resets
LOCKED = []
BANNED = []
BLOCKED_TEAMS = []

player_settings = PlayerPoolSettings()

MIN_PROJ = 0

# Positive value means allow teams that are unfavored to win.
min_favored = 10
MIN_LIMIT = min_favored - 2

roster = None
best_roster = None
best_score = 0

get_score = lambda roster: sum([p.proj for p in roster.players])

def block_function(p):
    store = p.kv_store
    played = int(float(store.get('Played') or 0))
    if SINGLE_GAME:
        # require min number of games played
        return played < MIN_PLAYED
        # return False
    name = p.name if p.pos != 'D' else p.team

    if p.team in BLOCKED_TEAMS:
        return True

    if p.pos == 'D' and (p.cost > 5000):
        return True

    if p.pos == 'QB' and p.cost < MIN_QB_SALARY or p.cost > max_salary:
        return True

    # print(favor_map[name], p.__dict__, min_favored)
    cost_filter = p.pos != 'QB' and (p.cost > max_salary or played <1)# or played > WEEK+1)

    should_skip = (p.proj < MIN_PROJ and p.pos != 'D') or (p.proj < 10 and p.pos == 'QB')  or cost_filter

    #print(p.name, played, MAX_PLAYED)
    return should_skip

# ===== cell 42 =====

def print_optimized_roster(roster, min_favored_factor):
    """
    Print an optimized roster in table format with weighting factor info.
    Sorted by position order: QB, RBs, WRs, FLEX, TE, D

    Args:
        roster: The optimized NFL roster
        min_favored_factor: The current min_favored weighting factor
        iteration_num: Which iteration of the optimization loop this is
    """
    # Count unique teams in roster
    teams_in_roster = set(p.team for p in roster.players)
    num_teams_in_roster = len(teams_in_roster)
    max_teams_possible = len(set_teams)

    print("\n" + "="*120)
    print(f"OPTIMIZED LINEUP (weighting_factor={min_favored_factor}, total_score={get_score(roster):.2f}, teams={num_teams_in_roster}/{max_teams_possible})\n---")

    # Define position order
    position_order = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'D': 4, 'MVP': 5, 'FLEX': 6}

    # Create table data and sort by position
    roster_data = []
    total_salary = 0
    for i, p in enumerate(roster.players, 1):
        # Get base projection (original FPPG) and adjusted projection
        base_fppg = float(p.kv_store.get('FPPG', 0))
        adjusted_proj = p.proj
        salary = int(p.cost)  # Convert to int to avoid decimal point formatting issues

        # Extract opponent from matchup (format: "AwayTeam@HomeTeam")
        if p.matchup:
            teams = p.matchup.split('@')
            if len(teams) == 2:
                opponent = teams[0].strip() if p.team == teams[1].strip() else teams[1].strip()
            else:
                opponent = 'N/A'
        else:
            opponent = 'N/A'

        # Get spread from favor_map (already properly signed: + for favorites, - for underdogs)
        spread = favor_map.get(p.team, 0)

        total_salary += salary

        # Get position order for sorting (use the mapped value, default to 99 if not found)
        pos_order = position_order.get(p.pos, 99)

        # Get weather factor for this player
        weather_factor = p.kv_store.get('weather_factor', 1.0)
        weather_str = f"{weather_factor:.3f}"

        roster_data.append({
            'Slot': f"{p.pos:5}",
            'Name': p.name[:25],
            'Team': p.team,
            'Opp': opponent,
            'Salary': f"${salary:,}",
            'Weather Factor': weather_str,
            'Base Proj': f"{base_fppg:6.2f}",
            'Weighted Proj': f"{adjusted_proj:6.2f}",
            'Value': f"{(adjusted_proj/salary)*1000:6.1f}x",
            'Spread': f"{spread:+.1f}",
            'pos_order': pos_order
        })

    # Sort by position order
    roster_data.sort(key=lambda x: x['pos_order'])

    # Print table header
    print(f"{'Slot':<6} {'Name':<27} {'Team':<5} {'Opp':<5} {'Salary':<10} {'Weather':<8} {'Base Proj':<12} {'Weighted Proj':<15} {'Value':<10} {'Spread':<8}")
    print("-"*128)

    # Print rows
    for row in roster_data:
        weather_factor = row.get('Weather Factor', '1.000')
        print(f"{row['Slot']:<6} {row['Name']:<27} {row['Team']:<5} {row['Opp']:<5} {row['Salary']:<10} {weather_factor:<8} {row['Base Proj']:<12} {row['Weighted Proj']:<15} {row['Value']:<10} {row['Spread']:<8}")

    # Print summary
    print("-"*120)
    total_salary_formatted = f"{total_salary:,}"
    print(f"{'TOTAL':<6} {'':<27} {'':<5} ${total_salary_formatted:<9} {'':<12} {get_score(roster):<15.2f}")
    print("-"*3)

# ===== cell 43 =====

def build_optimizer_settings(players, block_function):
    """
    Build optimizer settings with custom rules to enforce position constraints.

    Rules created:
    - Block players according to block_function
    - At most 1 version (regular or MVP) per player
    - At most 1 RB per team
    - At most 1 WR per team
    - At most 1 QB per team
    - At most 1 TE per team

    Args:
        players: List of Player objects
        block_function: Function to determine which players to exclude

    Returns:
        OptimizerSettings object configured with all custom rules
    """
    custom_rules = [
        CustomRule(
            group_a=lambda p: p,
            group_b=block_function,
            comparison=lambda sum, a, b: sum(b) == 0
        )
    ]

    # For single game: enforce that only ONE version (regular or MVP) of each player can be selected
    if SINGLE_GAME:
        player_versions = {}
        for p in players:
            base_name = p.name.replace(' (MVP)', '')
            if base_name not in player_versions:
                player_versions[base_name] = []
            player_versions[base_name].append(p)

        # Add constraint: at most 1 version per player
        for base_name, player_group in player_versions.items():
            if len(player_group) > 1:
                custom_rules.append(
                    CustomRule(
                        group_a=lambda p, base_name=base_name: p.name == base_name or p.name == f"{base_name} (MVP)",
                        group_b=lambda p: False,
                        comparison=lambda sum, a, b: sum(a) <= 1
                    )
                )

        return OptimizerSettings(
            custom_rules=custom_rules,
            min_teams=2
        )

    # Group RBs by team and enforce max 1 per team
    rb_teams = {}
    for p in players:
        if p.pos == 'RB':
            if p.team not in rb_teams:
                rb_teams[p.team] = []
            rb_teams[p.team].append(p)

    # Add constraint: at most 1 RB per team
    for team, team_rbs in rb_teams.items():
        if len(team_rbs) > 1:
            custom_rules.append(
                CustomRule(
                    group_a=lambda p, team=team: p.pos == 'RB' and p.team == team,
                    group_b=lambda p: False,
                    comparison=lambda sum, a, b: sum(a) <= 1
                )
            )

    # Group WRs by team and enforce max 1 per team
    wr_teams = {}
    for p in players:
        if p.pos == 'WR':
            if p.team not in wr_teams:
                wr_teams[p.team] = []
            wr_teams[p.team].append(p)

    # Add constraint: at most 1 WR per team
    for team, team_wrs in wr_teams.items():
        if len(team_wrs) > 1:
            custom_rules.append(
                CustomRule(
                    group_a=lambda p, team=team: p.pos == 'WR' and p.team == team,
                    group_b=lambda p: False,
                    comparison=lambda sum, a, b: sum(a) <= 1
                )
            )

    # Group QBs by team and enforce max 1 per team
    qb_teams = {}
    for p in players:
        if p.pos == 'QB':
            if p.team not in qb_teams:
                qb_teams[p.team] = []
            qb_teams[p.team].append(p)

    # Add constraint: at most 1 QB per team
    for team, team_qbs in qb_teams.items():
        if len(team_qbs) > 1:
            custom_rules.append(
                CustomRule(
                    group_a=lambda p, team=team: p.pos == 'QB' and p.team == team,
                    group_b=lambda p: False,
                    comparison=lambda sum, a, b: sum(a) <= 1
                )
            )

    # Group TEs by team and enforce max 1 per team
    te_teams = {}
    for p in players:
        if p.pos == 'TE':
            if p.team not in te_teams:
                te_teams[p.team] = []
            te_teams[p.team].append(p)

    # Add constraint: at most 1 TE per team
    for team, team_tes in te_teams.items():
        if len(team_tes) > 1:
            custom_rules.append(
                CustomRule(
                    group_a=lambda p, team=team: p.pos == 'TE' and p.team == team,
                    group_b=lambda p: False,
                    comparison=lambda sum, a, b: sum(a) <= 1
                )
            )

    return OptimizerSettings(
        custom_rules=custom_rules,
        min_teams=3
    )

# ===== cell 44 =====
BLOCKED_TEAMS = []

if SINGLE_GAME:
    # LOCKED = ["Lil'Jordan Humphrey", 'Jason Myers (MVP)']
    BANNED = ['Seattle Seahawks (MVP)', 'DeMario Douglas', 'Seattle Seahawks', 'New England Patriots']
    pass
else:
    BANNED = ['Sam Darnold', 'Tyler Higbee', 'AJ Barner', "Lil'Jordan Humphrey" , "Stefon Diggs"]
    pass

constraints = LineupConstraints(locked=LOCKED, banned=BANNED)

# ===== cell 46 =====

# Build optimizer settings with custom rules
opt_settings = build_optimizer_settings(players, block_function)

roster = run(
    rule_set=ACTIVE_RULE_SET,
    player_pool=players,
    verbose=False,
    optimizer_settings=opt_settings,
    constraints=constraints,
    player_settings=player_settings
)

if roster:
    # Print optimized roster
    print_optimized_roster(roster, min_favored)

    current_score = get_score(roster)
    if not best_score or current_score > best_score:
        best_score = current_score
        best_roster = roster

        # Update roster position/team count for weather scaling
        # This populates the map used in calculate_adjusted_projection
        roster_position_team_count = {}
        for p in best_roster.players:
            if p.pos not in ['MVP', 'D']:
                key = (p.pos, p.team)
                roster_position_team_count[key] = roster_position_team_count.get(key, 0) + 1
else:
    print("No solution")

# ===== cell 47 =====
def calculate_diversity_score(roster):
    """
    Calculate a diversity score for a roster based on:
    1. Team diversification (penalize stacking)
    2. Position diversification (spread across positions)
    3. Correlation risk (players from same team tend to correlate)

    Defense is excluded from diversity score calculation.

    Higher score = more diverse roster (less correlation risk)
    Lower score = more stacked (higher correlation but higher ceiling)
    """
    if not roster or not roster.players:
        return {'overall_score': 0, 'team_diversity': 0, 'position_diversity': 0}

    # Filter out defense players
    non_defense_players = [p for p in roster.players if p.pos != 'DEF']

    if not non_defense_players:
        return {'overall_score': 0, 'team_diversity': 0, 'position_diversity': 0}

    # Count players per team
    team_counts = defaultdict(int)
    position_counts = defaultdict(int)
    team_position_pairs = defaultdict(int)

    for p in non_defense_players:
        team_counts[p.team] += 1
        position_counts[p.pos] += 1
        team_position_pairs[(p.team, p.pos)] += 1

    roster_size = len(non_defense_players)
    num_teams = len(team_counts)
    num_positions = len(position_counts)

    # 1. TEAM DIVERSITY SCORE (0-40 points)
    # Ideal: 3+ teams, evenly distributed
    # Worst: 1 team (all players same team)
    ideal_teams = min(roster_size, 4)
    team_diversity = min((num_teams / ideal_teams) * 40, 40)

    # 2. POSITION DIVERSITY SCORE (0-30 points)
    # Penalize if too many of same position
    position_variance = np.var(list(position_counts.values())) if position_counts else 0
    max_position_variance = roster_size
    position_diversity = 30 * (1 - min(position_variance / max_position_variance, 1))

    # 3. STACK PENALTY (0-30 points)
    # Penalize when multiple players from same team at same position (correlation risk)
    stack_penalty_points = 0
    for (team, pos), count in team_position_pairs.items():
        if count > 1:
            stack_penalty_points += (count - 1) * 5

    # Penalize heavy team stacks (3+ players from same team)
    for team, count in team_counts.items():
        if count >= 3:
            stack_penalty_points += (count - 2) * 5

    stack_penalty = 30 - min(stack_penalty_points, 30)

    # OVERALL SCORE (0-100)
    overall_score = min(team_diversity + position_diversity + stack_penalty, 100)

    return {
        'overall_score': round(overall_score, 1),
        'team_diversity': round(team_diversity, 1),
        'position_diversity': round(position_diversity, 1),
        'stack_penalty': round(stack_penalty, 1),
        'team_breakdown': dict(team_counts),
        'position_breakdown': dict(position_counts),
        'num_teams': num_teams,
        'num_unique_positions': num_positions
    }

# ===== cell 48 =====

# Calculate and print diversity score for best roster
if best_roster and not SINGLE_GAME:
    diversity = calculate_diversity_score(best_roster)
    opt_score = get_score(best_roster)

    print("\nROSTER DIVERSITY ANALYSIS")
    print("-"*8)
    print(f"Overall Diversity Score: {diversity['overall_score']}/100")
    print(f"  - Team Diversity:      {diversity['team_diversity']}/40")
    print(f"  - Position Diversity:  {diversity['position_diversity']}/30")
    print(f"  - Stack Penalty:       {diversity['stack_penalty']}/30")
    print(f"\nTeams Used: {diversity['num_teams']} - {diversity['team_breakdown']}")
    print(f"Positions: {diversity['position_breakdown']}")

    # FLAG: Check if diversity is concerning despite high optimization score
    TARGET_DIVERSITY_SCORE = 70
    THRESHOLD_LOW_DIVERSITY = 50
    HIGH_OPT_THRESHOLD = 45  # Optimization score considered "high"

    if diversity['overall_score'] < THRESHOLD_LOW_DIVERSITY and opt_score >= HIGH_OPT_THRESHOLD:
        print("\n" + "⚠️ "*40)
        print("WARNING: HIGH CORRELATION RISK DETECTED")
        print("⚠️ "*40)
        print(f"Optimization Score: {opt_score:.2f} (good)")
        print(f"Diversity Score: {diversity['overall_score']}/100 (LOW - target {TARGET_DIVERSITY_SCORE}+)")
        print("\nThis lineup has high potential but HIGH VARIANCE due to:")

        if diversity['team_diversity'] < 20:
            most_common_team = max(diversity['team_breakdown'].items(), key=lambda x: x[1])
            print(f"  • Team concentration: {most_common_team[1]} players from {most_common_team[0]} (too concentrated)")

        if diversity['position_diversity'] < 15:
            most_common_pos = max(diversity['position_breakdown'].items(), key=lambda x: x[1])
            print(f"  • Position imbalance: {most_common_pos[1]} {most_common_pos[0]}s (unbalanced)")

        if diversity['stack_penalty'] < 15:
            print(f"  • Team stacking: Multiple players from same team in same position (correlation)")

        print(f"\nConsider adjusting constraints to improve diversity if you want:")
        print(f"  • Lower variance/safer lineups")
        print(f"  • Better hedge against team-specific outcomes")
        print(f"  • More balanced exposure")
        print("="*80 + "\n")

    elif diversity['overall_score'] < TARGET_DIVERSITY_SCORE:
        print(f"\n💡 Diversity score ({diversity['overall_score']}) could be improved (target: {TARGET_DIVERSITY_SCORE}+)\n")

# ===== cell 50 =====
# Show weather impact on specific position groups
print("WEATHER FACTOR ANALYSIS BY POSITION - BEST ROSTER")
print("="*120)

# Group players by position and team to show aggregate weather impact
position_weather_impact = {}
if best_roster:
  for p in best_roster.players:
      if p.pos not in ['MVP', 'D']:
          weather_info = get_weather_for_team(p.team, weather_df)
          if weather_info:
              wind_factor = calculate_wind_factor(weather_info.get('wind_speed'), p.pos)
              temp_factor = calculate_temperature_factor(weather_info.get('temperature'), p.pos)
              precip_factor = calculate_precipitation_factor(weather_info.get('precipitation_chance'), p.pos)
              combined = wind_factor * temp_factor * precip_factor

              key = (p.pos, p.team)
              if key not in position_weather_impact:
                  position_weather_impact[key] = {
                      'wind': wind_factor,
                      'temp': temp_factor,
                      'precip': precip_factor,
                      'combined': combined,
                      'weather': weather_info,
                      'count': 0
                  }
              position_weather_impact[key]['count'] += 1

  # Display weather factors by position and team
  print(f"{'Pos':<6} {'Team':<6} {'Wind':<8} {'Temp':<8} {'Precip':<8} {'Combined':<10} {'Weather':<40} {'Count':<6}")
  print("-"*120)

  for (pos, team), data in sorted(position_weather_impact.items(), key=lambda x: x[1]['count'], reverse=True):
      weather = data['weather']
      temp_str = f"{weather['temperature']}°F" if weather['temperature'] else "N/A"
      wind_str = f"{weather['wind_speed']}mph" if weather['wind_speed'] else "N/A"
      precip_val = weather['precipitation_chance']
      condition_str = f"{weather['condition']} ({precip_val}%)" if precip_val else weather['condition'] or "Clear"

      print(f"{pos:<6} {team:<6} {data['wind']:<8.3f} {data['temp']:<8.3f} {data['precip']:<8.3f} {data['combined']:<10.4f} {condition_str:<40} {data['count']:<6}")

  print("="*120)
  print("\nWeather Factor Interpretation:")
  print("  > 1.0 = Position benefits from weather (boost to projection)")
  print("  < 1.0 = Position penalized by weather (penalty to projection)")
  print("  = 1.0 = Neutral weather impact")
  print("\nExamples:")
  print("  RB 1.07 = Running backs get +7% boost (worse weather → more carries)")
  print("  QB 0.93 = Quarterbacks get -7% penalty (high wind/cold → harder to throw)")
  print("="*120)

# ===== cell 51 =====
# Create upload CSV by reordering columns to match template order.

headers = []
players = []
ORDERED_COLS = ['QB','RB','RB','WR','WR','WR','TE','FLEX','DEF'] # template order.

def get_match_names(col):
    if col == 'DEF':
        return ['D']
    elif col == 'FLEX' :
        return ['RB', 'WR']
    return [col]

roster_copy = roster.players.copy()
for c in ORDERED_COLS:
    headers.append(c)
    match_names = get_match_names(c)
    for r in roster_copy:
        if r.pos in match_names:
            p = f"{r.kv_store['Id']}:{r.name}"
            players.append(p)
            roster_copy.remove(r)
            break

with open(UPLOAD_FILE, 'w') as f:
    f.write(','.join(headers))
    f.write('\n')
    f.write(','.join(players))

print('done')

# ===== cell 52 =====
output = pd.read_csv(UPLOAD_FILE)
output

# ===== cell 53 =====
m_score = df[(df['FPPG'] > 0) & (df['Played'] > 0)].groupby(['Position'])['FPPG'].mean().to_dict()
m_score['DEF'] = m_score['D']
m_score['FLEX'] = (m_score['WR']+m_score['RB'])/2
m_score
