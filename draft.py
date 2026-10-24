# FanDuel NFL lineup optimizer
# Run headless with: uv run python draft.py

import copy
import itertools
import os
from collections import defaultdict
from datetime import datetime

from draftfast import rules
from draftfast.csv_parse import salary_download
from draftfast.lineup_constraints import LineupConstraints
from draftfast.optimize import run
from draftfast.settings import OptimizerSettings, CustomRule
from fantasypros import build_projection_map as build_fp_projection_map
from nfl_teams import NFL_TEAM_MAP, normalize_player_name, normalize_team_abbr
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
    compute_wr_injury_bonus,
    qb_quality_multiplier,
)
from nflverse import compute_fpa_map, compute_giveaway_map, compute_history, compute_qb_snap_share_map, compute_score_distributions, compute_upside_map
from sleeper import build_projection_map
from weather import display_weather_summary, get_nfl_weather, team_name_to_abbr

import config


def get_most_recently_created_file_with_extension(folder, extension):
    files = [f for f in os.listdir(folder) if f.endswith(extension)]
    return max(files, key=lambda x: os.path.getmtime(f"{folder}/{x}"))


def get_week_relative_to_start(season_start=config.SEASON_START):
    start = datetime.strptime(season_start, '%m/%d/%Y')
    today = datetime.today()
    # Weeks run Tuesday through Monday; a new week triggers on Tuesday.
    return ((today - start).days + 5) // 7 + 1


def get_slate_week_from_file(filepath, season_start=config.SEASON_START):
    """Derive slate week from the FanDuel filename, e.g.
    'FanDuel-NFL-2026 EDT-09 EDT-20 EDT-134251-players-list.csv'
    encodes year=2026, month=09, slate day=20. Returns None if unparseable.

    NFL/fantasy weeks run Tuesday through Monday and trigger on Tuesday, so
    the slate date is shifted to align with that boundary."""
    import re
    m = re.search(r'NFL-(\d{4}) EDT-(\d{2}) EDT-(\d{2})', os.path.basename(filepath))
    if not m:
        return None
    year, month, day = map(int, m.groups())
    slate = datetime(year, month, day)
    start = datetime.strptime(season_start, '%m/%d/%Y')
    if slate < start:
        return None
    return ((slate - start).days + 5) // 7 + 1


SALARY_FILE = (os.environ.get('SALARY_FILE') or
               f"{config.DATA_FOLDER}/{get_most_recently_created_file_with_extension(config.DATA_FOLDER, 'csv')}")
WEEK = get_slate_week_from_file(SALARY_FILE) or max(get_week_relative_to_start(), 1)
ACTIVE_FILE = f"{config.ACTIVE_FOLDER}/data.csv"
UPLOAD_FILE = f"{config.UPLOAD_FOLDER}/upload.csv"

WEIGHTED = config.WEIGHTED
MAX_SALARY = config.MAX_SALARY

# Data-source resilience: every external pull records its status here and the
# run degrades gracefully (missing data -> neutral), then reports what failed.
DATA_STATUS = {}


def _record_source(name, ok, detail=''):
    """Record whether an external data source loaded OK or failed/missing."""
    DATA_STATUS[name] = ('OK' if ok else 'MISSING / DEGRADED', detail)


def print_banner(title, width):
    """Print a '='-delimited section banner."""
    print("\n" + "=" * width)
    print(title)
    print("=" * width)


def print_table(title, header, rows, width):
    """Print a banner, a column header, a rule, then preformatted rows."""
    print_banner(title, width)
    print(header)
    print("-" * width)
    for row in rows:
        print(row)


def print_data_status():
    """Print a summary of which external data sources loaded OK vs failed."""
    print_banner("DATA SOURCES", 60)
    if not DATA_STATUS:
        print("  (no sources tracked)")
    for name in sorted(DATA_STATUS):
        status, detail = DATA_STATUS[name]
        print(f"  {name:<20} {status:<18} {detail}")
    print("=" * 60)

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
    _record_source('FPA', bool(fpa_map), f"{len(fpa_map)} entries")
    print(f"Using {len(fpa_map)} FPA entries")
except Exception as e:
    _record_source('FPA', False, str(e))
    print('Error loading FPA data:', e)
    fpa_map = {}

# Per-position league-average points allowed per game, used as the neutral
# baseline for FPA adjustments (a defense allowing average = 0 bonus).
fpa_baselines = {}
for _entry in fpa_map.values():
    for _pos, _value in _entry.items():
        fpa_baselines.setdefault(_pos, []).append(_value)
fpa_baselines = {pos: sum(v) / len(v) for pos, v in fpa_baselines.items()}

# Per-offense giveaways/sacks (expected DST big-play points), used to boost a
# defense facing a turnover-prone offense beyond what the implied total says.
giveaway_map = {}
try:
    giveaway_map = compute_giveaway_map(WEEK) or {}
    _record_source('Giveaways', bool(giveaway_map), f"{len(giveaway_map)} entries")
    print(f"Using {len(giveaway_map)} matchup giveaway entries")
except Exception as e:
    _record_source('Giveaways', False, str(e))
    print('Error loading giveaway data:', e)
    giveaway_map = {}
giveaway_baseline = (sum(giveaway_map.values()) / len(giveaway_map)) if giveaway_map else 0.0

# ============================================================
# Vegas spreads / over-unders -> favor_map, team_totals
# ============================================================

spread_df = pd.DataFrame()
try:
    spread_df = get_metabet_spread(WEEK)
    _record_source('Spreads', spread_df is not None and not spread_df.empty, f"{len(spread_df)} games")
except Exception as e:
    _record_source('Spreads', False, str(e))
    print('Error loading spread data:', e)
    spread_df = pd.DataFrame()

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
    def weather_team_abbr(team):
        name = str(team).strip()
        return team_name_to_abbr.get(name, NFL_TEAM_MAP.get(name, normalize_team_abbr(name)))

    weather_df = weather_df.copy()
    weather_df['away_team'] = weather_df['away_team'].apply(weather_team_abbr)
    weather_df['home_team'] = weather_df['home_team'].apply(weather_team_abbr)
    valid_teams = set(NFL_TEAM_MAP.values())
    invalid_weather = (~weather_df['away_team'].isin(valid_teams)
                       | ~weather_df['home_team'].isin(valid_teams))
    invalid_count = int(invalid_weather.sum())
    if invalid_count:
        invalid_matchups = weather_df.loc[invalid_weather, 'matchup'].astype(str).tolist()
        print(f"WARNING: ignoring {invalid_count} weather rows with unknown teams: {invalid_matchups}")
        weather_df = weather_df.loc[~invalid_weather].copy()
    if weather_df.empty:
        _record_source('Weather', False, 'no valid team matchups for this week')
    else:
        detail = f"{len(weather_df)} valid games"
        if invalid_count:
            detail += f"; ignored {invalid_count} invalid rows"
        _record_source('Weather', invalid_count == 0, detail)
else:
    _record_source('Weather', False, 'no data for this week')

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
    _record_source('History (nflverse)', bool(historic_averages), f"{len(historic_averages)} entries")
    print(f"Using {len(historic_averages)} nflverse history entries")
except Exception as e:
    _record_source('History (nflverse)', False, str(e))
    print('Error loading nflverse history:', e)
    historic_averages = {}

# ============================================================
# Sleeper projections (independent weekly projection source)
# ============================================================

sleeper_proj_map = {}
try:
    sleeper_proj_map = build_projection_map(WEEK) or {}
    _record_source('Sleeper', bool(sleeper_proj_map), f"{len(sleeper_proj_map)} projections")
    print(f"Loaded {len(sleeper_proj_map)} Sleeper projections")
except Exception as e:
    _record_source('Sleeper', False, str(e))
    print('Error loading Sleeper projections:', e)
    sleeper_proj_map = {}

# ============================================================
# FantasyPros projections (independent weekly projection source)
# ============================================================

fp_proj_map = {}
try:
    fp_proj_map = build_fp_projection_map(WEEK) or {}
    _record_source('FantasyPros', bool(fp_proj_map), f"{len(fp_proj_map)} projections")
    print(f"Loaded {len(fp_proj_map)} FantasyPros projections")
except Exception as e:
    _record_source('FantasyPros', False, str(e))
    print('Error loading FantasyPros projections:', e)
    fp_proj_map = {}

# ============================================================
# QB snap-share map (backup detection)
# ============================================================

qb_snap_share_map = {}
if config.USE_SNAP_BACKUP:
    try:
        qb_snap_share_map = compute_qb_snap_share_map(WEEK) or {}
        _record_source('Snap counts', bool(qb_snap_share_map), f"{len(qb_snap_share_map)} QBs")
    except Exception as e:
        _record_source('Snap counts', False, str(e))
        print('Error loading snap counts:', e)
        qb_snap_share_map = {}
else:
    _record_source('Snap counts', False, 'disabled (USE_SNAP_BACKUP=False)')

# ============================================================
# Injury bonuses
# ============================================================

INJURY_FACTOR = config.INJURY_FACTOR
excluded_bonus = defaultdict(lambda: 0)
# Vacated production routed to the SAME position group: when a starter is out,
# the remaining players at that position inherit the role (unlike the old flat
# team pool, which leaked an injured RB's workload to tight ends too).
pos_bonus = defaultdict(lambda: 0)
injured_qb = defaultdict(lambda: False)
# When a team's WR is out, the remaining WRs inherit the targets: extra pool
# that only WRs on the team receive (on top of the shared excluded_bonus).
wr_bonus = defaultdict(lambda: 0)

# Identify each team's available QB and project their quality from the same
# season/history/weekly sources used for player projections. Prefer prior snap
# share to distinguish the starter from backups; fall back to games played.
qb_candidates = pd.concat([
    df[df['Position'] == 'QB'],
    questionable_df[questionable_df['Position'] == 'QB'],
], ignore_index=True)
qb_quality_candidates = defaultdict(list)
for _, qb in qb_candidates.iterrows():
    indicator = str(qb.get('Injury Indicator') or '').strip().upper()
    if indicator in ('O', 'IR', 'D'):
        continue
    team = normalize_team_abbr(qb['Team'])
    name = normalize_player_name(qb['Name'])
    fppg = float(qb['FPPG']) if pd.notna(qb['FPPG']) else 0.0
    history = historic_averages.get(name)
    base = (config.AVERAGE_WEIGHT * fppg + (1 - config.AVERAGE_WEIGHT) * history
            if history else fppg)
    sleeper_qb = sleeper_proj_map.get((team, 'QB', name))
    fp_qb = fp_proj_map.get((team, 'QB', name))
    if sleeper_qb is not None:
        base = blend_projections(base, sleeper_qb, config.SLEEPER_WEIGHT)
    if fp_qb is not None:
        base = blend_projections(base, fp_qb, config.FANTASYPROS_WEIGHT)
    snap_share = qb_snap_share_map.get((team, name))
    played = float(qb['Played']) if pd.notna(qb['Played']) else 0.0
    # Candidates with snap-share data outrank those without it; otherwise use
    # games played, then FPPG, as a starter/quality fallback.
    starter_key = (snap_share is not None, snap_share or 0.0, played, fppg)
    qb_quality_candidates[team].append((starter_key, base))

team_qb_projections = {
    team: max(candidates, key=lambda item: item[0])[1]
    for team, candidates in qb_quality_candidates.items()
}
positive_qb_projections = [p for p in team_qb_projections.values() if p > 0]
league_qb_projection = (sum(positive_qb_projections) / len(positive_qb_projections)
                        if positive_qb_projections else 0.0)
qb_quality_factors = {
    team: qb_quality_multiplier(
        projection, league_qb_projection,
        config.INJURY_QB_QUALITY_WEIGHT,
        config.INJURY_QB_QUALITY_MIN,
        config.INJURY_QB_QUALITY_MAX,
    )
    for team, projection in team_qb_projections.items()
}

for index, p in questionable_df.iterrows():
    pos = p['Position']
    if pos in ['TE', 'WR', 'RB', 'QB']:
        # Out (O) / Injured Reserve (IR) fund full bonuses. Doubtful (D) and
        # Questionable (Q) statuses receive progressively smaller transfers.
        indicator = str(p['Injury Indicator']).strip().upper()
        if indicator not in ('O', 'IR', 'Q', 'D'):
            continue
        points = p['FPPG']
        if points >= config.MIN_INJURY_FPPG and p['Played'] >= WEEK / 2:
            # A confirmed Out/IR is a known, full-workload vacancy; a
            # Questionable is speculative, so it transfers only partial credit.
            confirmed = indicator in ('O', 'IR')
            if confirmed:
                weight = 1.0
                injury_offset = min(points * config.INJURY_OUT_TRANSFER, config.INJURY_POOL_CAP)
            elif indicator == 'D':
                weight = config.INJURY_DOUBTFUL_WEIGHT
                injury_offset = min(points * config.INJURY_OUT_TRANSFER, config.INJURY_POOL_CAP)
            else:
                weight = config.INJURY_QUESTIONABLE_WEIGHT
                injury_offset = min(points * INJURY_FACTOR, INJURY_FACTOR * 10)
            if pos == 'QB':
                amt = -injury_offset * 2 * weight
                if confirmed:
                    injured_qb[p['Team']] = True
            elif pos in ('RB', 'WR', 'TE'):
                amt = injury_offset * config.INJURY_SKILL_MULTIPLIER * weight
                pos_bonus[(p['Team'], pos)] += amt
                if pos == 'WR':
                    wr_bonus[p['Team']] += amt
            else:
                amt = injury_offset * weight
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
    ACTIVE_RULE_SET.salary_min = ACTIVE_RULE_SET.salary_max - config.SALARY_MIN_OFFSET_SINGLE
else:
    ACTIVE_RULE_SET.salary_min = ACTIVE_RULE_SET.salary_max - config.SALARY_MIN_OFFSET
ACTIVE_RULE_SET.roster_size = config.ROSTER_SIZE_CLASSIC if not SINGLE_GAME else config.ROSTER_SIZE_SINGLE

ALL_POSITIONS = [*ACTIVE_RULE_SET.defensive_positions, *ACTIVE_RULE_SET.offensive_positions]

# ============================================================
# Weather adjustment factors
# ============================================================


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
    elif p.pos == 'MVP':
        # MVP is a boosted offensive player: own-team skill injuries still help.
        bonuses += excluded_bonus.get(p.team, 0) / 2
    # Defenses get no own-team injury bonus (a team's own offensive injuries
    # don't help its defense); the opponent's injuries are handled separately
    # in calculate_adjusted_projection.
    # WRs on a team with an injured WR inherit the vacated targets INSTEAD of
    # the shared pool (both would double-count the same vacated production),
    # split by each WR's share of the team's production.
    if p.pos == 'WR':
        bonuses += compute_wr_injury_bonus(
            wr_bonus.get(p.team, 0),
            wr_base_shares.get(p.team, {}).get(p.name, 0),
            qb_quality_factors.get(p.team, 1.0),
            config.WR_INHERITANCE_CAP,
            config.WR_INHERITANCE_WEIGHT,
        )
    elif p.pos in ('RB', 'TE'):
        bonuses += pos_bonus.get((p.team, p.pos), 0) * qb_quality_factors.get(p.team, 1.0)
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
    """Normalized lookup key into nflverse history averages.

    Defenses return None: nflverse defensive history only covers big plays
    (sacks/INTs/TDs) and is not comparable to FanDuel DST scoring, so defense
    projections are anchored on FanDuel FPPG + Sleeper/FantasyPros DST instead.
    """
    if p.pos == 'D':
        return None
    if p.pos == 'MVP':
        return normalize_team_abbr(p.team).lower()
    return normalize_player_name(p.name)


def sleeper_projection_for(p):
    """Sleeper pts_std for a player, keyed by base position/name."""
    pos = base_position(p)
    if pos == 'D':
        return sleeper_proj_map.get((p.team, 'DEF', ''))
    return sleeper_proj_map.get((p.team, pos, normalize_player_name(p.name.replace(' (MVP)', ''))))


def fp_projection_for(p):
    """FantasyPros FanDuel-adjusted projection; defenses keyed by team."""
    pos = base_position(p)
    if pos == 'D':
        return fp_proj_map.get((p.team, 'D', ''))
    return fp_proj_map.get((p.team, pos, normalize_player_name(p.name.replace(' (MVP)', ''))))


weather_factor_map = {}
if not weather_df.empty:
    for _, game in weather_df.iterrows():
        away = normalize_team_abbr(str(game['away_team']).strip())
        home = normalize_team_abbr(str(game['home_team']).strip())
        # Weather immunity belongs to the VENUE, not the player's team:
        # when the home team plays in a dome/retractable-roof stadium the
        # whole game is weather-immune, so both teams get factor 1.0.
        if home in config.ROOFED_TEAMS:
            continue
        wind = game.get('wind_speed')
        temp = game.get('temperature')
        precip = game.get('precipitation_chance')
        for team in (away, home):
            for pos in ALL_POSITIONS:
                combined_factor = (calculate_wind_factor(wind, pos)
                                   * calculate_temperature_factor(temp, pos)
                                   * calculate_precipitation_factor(precip, pos))
                weather_factor_map[(team, pos)] = combined_factor

# Flag any slate team that got no weather factor despite playing at an
# outdoor venue: the weather cache is stale, missing, or corrupted (e.g. a
# row whose matchup doesn't match the salary file, or a cache for the wrong
# week). Teams playing in a roofed stadium are weather-immune and correctly
# have no factor.
if not SINGLE_GAME:
    missing_weather = []
    outdoor_teams = 0
    for t in set(df['Team']):
        game = df.loc[df['Team'] == t, 'Game'].iloc[0]
        home = str(game).split('@')[-1].strip()
        if home in config.ROOFED_TEAMS:
            continue
        outdoor_teams += 1
        if (t, 'QB') not in weather_factor_map:
            missing_weather.append(t)
    if missing_weather:
        frac = len(missing_weather) / max(outdoor_teams, 1)
        diagnose = "weather cache likely for a DIFFERENT week" if frac > 0.4 else "weather cache likely stale/mismatched"
        print(f"WARNING: no weather factor for {len(missing_weather)}/{outdoor_teams} outdoor teams: {sorted(missing_weather)} — {diagnose}")


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
    p.kv_store['base_proj'] = base_score

    opponent = get_opponent(p)

    matchup_bonus = 0

    # Vegas implied team total (encodes both spread and game total)
    matchup_bonus += calculate_team_total_bonus(p, opponent)

    # Defenses also collect sacks/turnovers: tilt by how giveaway-prone the
    # opposing offense has been relative to the league average.
    if p.pos == 'D':
        opp_giveaway = giveaway_map.get(opponent)
        if opp_giveaway is not None and giveaway_baseline > 0:
            matchup_bonus += (opp_giveaway - giveaway_baseline) * config.DST_GIVEAWAY_WEIGHT

    # Fantasy points allowed by opponent's defense
    matchup_bonus += calculate_fpa_bonus(p, opponent)

    # Injury adjustments
    matchup_bonus += calculate_injury_bonuses(p, opponent)

    # Defenses/MVPs also account for opponent offense weakness.
    # excluded_bonus is negative when the opponent's QB is out (and positive
    # for skill-position injuries); either way a weakened offense helps the D.
    if p.pos in ['D', 'MVP']:
        matchup_bonus += abs(excluded_bonus.get(opponent, 0)) * config.OPPONENT_INJURY_WEIGHT

    # Weather adjustment (additive, capped at 40% of base)
    weather_bonus = 0
    combined_weather_factor = weather_factor_map.get((p.team, p.pos), 1.0)
    if combined_weather_factor != 1.0:
        p.kv_store['weather_factor'] = combined_weather_factor
        weather_bonus = base_score * (combined_weather_factor - 1.0)
        max_weather_bonus = base_score * config.MAX_WEATHER_BONUS
        weather_bonus = max(min(weather_bonus, max_weather_bonus), -max_weather_bonus)
        matchup_bonus += weather_bonus

# Apply with safeguards (cap_projection floors low-base players at
    # MIN_PROJ_MULTIPLIER).
    return cap_projection(
        base_score + matchup_bonus,
        base_score,
        p.pos,
        max_score=MAX_SCORE,
        min_proj_multiplier=config.MIN_PROJ_MULTIPLIER,
        max_def_multiplier=config.MAX_DEF_MULTIPLIER,
        max_proj_multiplier=config.MAX_PROJ_MULTIPLIER,
    )


players = salary_download.generate_players_from_csvs(salary_file_location=ACTIVE_FILE, game=rules.FAN_DUEL)

# Each remaining WR's share of the team's WR production: vacated targets flow
# mostly to the WR who already produces (best proxy for who runs the routes),
# not equally to every depth WR on the roster.
wr_base_shares = defaultdict(dict)
wr_base_totals = defaultdict(float)
for p in players:
    if p.pos == 'WR':
        wr_base_totals[p.team] += float(p.kv_store.get('FPPG') or 0)
for p in players:
    if p.pos == 'WR' and wr_base_totals[p.team] > 0:
        wr_base_shares[p.team][p.name] = (float(p.kv_store.get('FPPG') or 0)) / wr_base_totals[p.team]

for p in players:
    p.proj = calculate_adjusted_projection(p)
    p.kv_store['adjusted_proj'] = p.proj

if fp_proj_map:
    fp_matched = sum(fp_projection_for(p) is not None for p in players)
    _record_source(
        'FantasyPros',
        fp_matched == len(players),
        f"{len(fp_proj_map)} projections; matched {fp_matched}/{len(players)} active players",
    )
    if fp_matched < len(players):
        print(f"WARNING: FantasyPros has no projection for {len(players) - fp_matched}/{len(players)} active slate players")

# GPP upside tilt: blend projections toward each player's ceiling so lineups
# target boom scores (single-game winners / top-10% GPP finishes) instead of
# point-expectation averages. Also surfaces cheap high-ceiling plays.
upside_weight = config.SHOWDOWN_UPSIDE_WEIGHT if SINGLE_GAME else config.GPP_UPSIDE_WEIGHT
sneaky_upside_plays = []
if upside_weight > 0:
    try:
        upside_map = compute_upside_map(WEEK) or {}
        _record_source('Upside/ceiling', bool(upside_map), f"{len(upside_map)} players")
    except Exception as e:
        _record_source('Upside/ceiling', False, str(e))
        print('Error loading upside data:', e)
        upside_map = {}
    w = upside_weight
    for p in players:
        ceiling = upside_map.get(normalize_player_name(p.name))
        if not ceiling:
            continue
        # Preserve each player's best available ceiling for tournament (WTA)
        # lineup ranking, even when the current projection is already higher.
        p.kv_store['ceiling'] = max(ceiling, p.proj)
        if SINGLE_GAME:
            # A historical ceiling should only add upside; it must not drag a
            # stronger current projection down, especially for small samples.
            if ceiling > p.proj:
                p.proj = min((1 - w) * p.proj + w * ceiling, config.SHOWDOWN_MAX_SCORE)
        else:
            base = p.kv_store.get('base_proj') or p.proj
            cap = min(config.MAX_SCORE, base * config.MAX_PROJ_MULTIPLIER)
            if ceiling > p.proj:
                p.proj = min((1 - w) * p.proj + w * ceiling, max(cap, p.proj))
    if SINGLE_GAME:
        for p in players:
            if p.cost > config.SHOWDOWN_SNEAKY_MAX_COST or p.cost <= 0:
                continue
            ceiling = upside_map.get(normalize_player_name(p.name))
            if not ceiling:
                continue
            if ceiling < p.proj * config.SHOWDOWN_SNEAKY_MIN_UPSIDE:
                continue
            sneaky_upside_plays.append((
                p.name, p.pos, int(p.cost), round(p.proj, 2), round(ceiling, 2),
                round(ceiling / p.cost * 1000, 1),
            ))
        sneaky_upside_plays.sort(key=lambda x: -x[5])
    print(f"Applied {len(upside_map)} upside adjustments (weight {w})")

# Single-game WRs from the same team compete for the same target pool. Keep
# the team's WR1 intact, but apply a soft penalty to additional WRs rather than
# banning correlated QB/WR stacks outright. Salary is a more stable role proxy
# than a small-sample projection; explicit overrides win when supplied.
if SINGLE_GAME and config.SINGLE_GAME_SAME_TEAM_WR_PENALTY > 0:
    wrs_by_team = defaultdict(list)
    for p in players:
        if p.pos == 'WR':
            wrs_by_team[p.team].append(p)
    for team, team_players in wrs_by_team.items():
        override = config.SINGLE_GAME_WR1_OVERRIDES.get(team)
        names = {p.name for p in team_players}
        if override in names:
            wr1_name = override
        else:
            wr1_name = max(team_players, key=lambda player: player.proj).name
        others = sorted(
            (p for p in team_players if p.name != wr1_name),
            key=lambda player: player.proj,
            reverse=True,
        )
        for rank, p in enumerate(others, start=1):
            penalty = config.SINGLE_GAME_SAME_TEAM_WR_PENALTY * rank
            p.proj = max(0.0, p.proj - penalty)
            if 'ceiling' in p.kv_store:
                p.kv_store['ceiling'] = max(0.0, p.kv_store['ceiling'] - penalty)
            p.kv_store['same_team_wr_penalty'] = penalty

# Single-game slates: create 1.5x-salary/1.5x-projection MVP variants
if SINGLE_GAME:
    mvp_players = []
    for p in players:
        mvp = copy.deepcopy(p)
        mvp.pos = 'MVP'
        mvp.cost = int(round(p.cost * 1.5))
        mvp.proj = p.proj * 1.5
        mvp.name = p.name + ' (MVP)'
        mvp.kv_store['base_name'] = p.name
        mvp.kv_store['base_pos'] = p.pos
        # The MVP slot pays 1.5x, so its tournament ceiling must scale too.
        mvp.kv_store['ceiling'] = (p.kv_store.get('ceiling') or p.proj) * 1.5
        mvp_players.append(mvp)
    players.extend(mvp_players)

# Validate all player teams exist in spread data. If the whole spread source
# failed we degrade to neutral totals instead of crashing (already logged).
missing_teams = {p.team for p in players} - set(favor_map.keys())
if missing_teams and favor_map:
    error_msg = "The following teams are missing from spread/matchup data:\n"
    for team in sorted(missing_teams):
        error_msg += f"  {team} ({sum(1 for p in players if p.team == team)} players affected)\n"
    raise ValueError(error_msg)

# Sort players into display lists
defenses = []
qbs = []
skills = []
mvps = []

for p in players:
    base_fppg = float(p.kv_store.get('base_proj') or p.kv_store.get('FPPG') or 0)
    opponent = get_opponent(p)

    if p.pos == 'MVP':
        mvps.append((p.name, p.proj, p.cost, p.proj / p.cost, p.pos, base_fppg, opponent))
    elif p.pos == 'D':
        defenses.append((p.team, p.proj, p.cost, p.proj / p.cost, base_fppg, favor_map.get(p.team, 0), opponent))
    elif p.pos in ('RB', 'WR', 'TE') and p.proj > 0:
        team_total = team_totals.get(p.team, avg_team_total)
        total_deviation = team_total - avg_team_total
        weather = float(p.kv_store.get('weather_factor', 1.0))
        skills.append((p.name, p.pos, p.proj, p.cost, p.proj / p.cost, team_total, total_deviation, base_fppg, opponent, weather))
    elif p.pos == 'QB' and p.cost >= MIN_QB_SALARY and p.proj > 0:
        team_total = team_totals.get(p.team, avg_team_total)
        total_deviation = team_total - avg_team_total
        weather = float(p.kv_store.get('weather_factor', 1.0))
        qbs.append((p.name, p.pos, p.proj, p.cost, p.proj / p.cost, team_total, total_deviation, base_fppg, opponent, weather))

starter_map = build_starter_map(players, questionable_df)
filtered_mvps = filter_mvps(mvps, players, starter_map)

# ============================================================
# Display: sorted defenses / QBs / MVPs
# ============================================================

PROJ_TABLE_HEADER = (f"{'Name':<35} {'Proj':>8} {'Salary':>10} {'Value':>8} "
                     f"{'TeamTotal':>10} {'Dev':>8} {'Opp':>5} {'Base':>8} {'Weather':>8}")


def _proj_row(name, proj, cost, value, team_total, total_deviation, base_fppg, opp, weather):
    arrow = '↓' if weather < 0.85 else ''
    return (f"{name:<35} {proj:>8.2f} ${cost:>9,.0f} {(value * 1000):>7.1f}x "
            f"{team_total:>10.2f} {total_deviation:>8.2f} {opp:>5} {base_fppg:>8.2f} {weather:>7.3f}{arrow}")


def print_sorted_proj_table(title, rows, width=108):
    """Print a QB/skill-position table (shared header and row format)."""
    print_table(title, PROJ_TABLE_HEADER, [_proj_row(r[0], *r[2:]) for r in rows], width)


print_table(
    "SORTED DEFENSES",
    f"{'Team':<8} {'Proj':>8} {'Salary':>10} {'Base':>8} {'Spread':>8} {'Opp':>5} {'Value':>8}",
    [f"{team:<8} {proj:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {spread:>8.2f} {opp:>5} {(value * 1000):>7.1f}x"
     for team, proj, cost, value, base_fppg, spread, opp in sorted(defenses, key=lambda x: x[3], reverse=True)[:config.TOP_N_DISPLAY]],
    61,
)

if SINGLE_GAME:
    print_table(
        f"SORTED MVPs (Top {config.TOP_N_DISPLAY} - Backups Shown Only if Starter Injured)",
        f"{'Name':<35} {'Proj':>8} {'Salary':>10} {'Base':>8} {'Value':>8} {'Games':>7} {'Opp':>5} {'Status':<10}",
        [f"{name:<35} {proj:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {(value * 1000):>7.1f}x {games_played:>7.0f} {opp:>5} {status:<10}"
         for name, proj, cost, value, pos, base_fppg, games_played, opp, status in filtered_mvps[:config.TOP_N_DISPLAY]],
        98,
    )

print_sorted_proj_table("SORTED QBs", sorted(qbs, key=lambda x: x[4], reverse=True)[:config.TOP_N_DISPLAY])
for _pos in ('RB', 'WR', 'TE'):
    print_sorted_proj_table(f"SORTED {_pos}s", sorted((s for s in skills if s[1] == _pos), key=lambda x: x[4], reverse=True)[:config.TOP_N_DISPLAY])

if SINGLE_GAME:
    # A showdown slate is one game; showing the rest of the week's forecast is
    # noise, so restrict the table to games involving both slate teams.
    slate_weather = weather_df[
        weather_df['away_team'].isin(set_teams) & weather_df['home_team'].isin(set_teams)
    ]
    display_weather_summary(slate_weather)
else:
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

        # A defense scores when the opposing passing game fails, so starting it
        # alongside that team's QB bets both sides of one outcome. draftfast
        # ignores no_offense_against_defense for showdown, and a full
        # no-opposing-offense rule is impossible here anyway: the 2-team minimum
        # would force both sides onto every roster and ban every defense.
        for d in (p for p in players if p.pos == 'D'):
            opponent = get_opponent(d)
            for q in players:
                if q.team != opponent or base_position(q) != 'QB':
                    continue
                base = q.name.replace(' (MVP)', '')
                custom_rules.append(
                    CustomRule(
                        group_a=lambda p, team=d.team: p.pos == 'D' and p.team == team,
                        group_b=lambda p, base=base: p.name.replace(' (MVP)', '') == base,
                        comparison=lambda sum, a, b: sum(a) + sum(b) <= 1,
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

    # Never pair a QB with an RB from the same team (diversifies the stack).
    for team in set(p.team for p in players if p.pos in ('QB', 'RB')):
        custom_rules.append(
            CustomRule(
                group_a=lambda p, team=team: p.pos == 'QB' and p.team == team,
                group_b=lambda p, team=team: p.pos == 'RB' and p.team == team,
                comparison=lambda sum, a, b: sum(a) + sum(b) <= 1,
            )
        )

    # Never pair a WR with a TE from the same team.
    for team in set(p.team for p in players if p.pos in ('WR', 'TE')):
        custom_rules.append(
            CustomRule(
                group_a=lambda p, team=team: p.pos == 'WR' and p.team == team,
                group_b=lambda p, team=team: p.pos == 'TE' and p.team == team,
                comparison=lambda sum, a, b: sum(a) + sum(b) <= 1,
            )
        )

    # Never pair a defense with offensive players from the team it faces:
    # both bet on opposite outcomes of the same game.
    #
    # Use draftfast's built-in setting, which adds a pairwise constraint
    # (D <= 1 - each opposing offensive player). A single CustomRule with
    # `sum(a) + sum(b) <= 1` would instead cap the opponent's ENTIRE offense
    # at one player, making same-team stacks (e.g. QB + WR) impossible to
    # build and therefore impossible to lock.
    return OptimizerSettings(
        custom_rules=custom_rules,
        min_teams=3,
        no_offense_against_defense=True,
    )


def optimize_lineup(players, score_by='proj', extra_locked=None, extra_banned=None):
    """Run draftfast's weighted optimizer and return the optimal roster.

    score_by='ceiling' temporarily optimizes on each player's tournament
    ceiling instead of the blended projection. That is the direct WTA search.
    """
    locked = list(dict.fromkeys(list(config.LOCKED) + list(extra_locked or [])))
    banned = [
        name for name in dict.fromkeys(list(BANNED) + list(extra_banned or []))
        if name not in locked
    ]
    active_constraints = LineupConstraints(locked=locked, banned=banned)

    original_proj = {p.name: p.proj for p in players}
    if score_by == 'ceiling':
        for p in players:
            p.proj = p.kv_store.get('ceiling') or p.proj

    try:
        roster = run(
            rule_set=ACTIVE_RULE_SET,
            player_pool=players,
            verbose=False,
            optimizer_settings=build_optimizer_settings(players, block_function),
            constraints=active_constraints,
        )
    finally:
        for p in players:
            p.proj = original_proj[p.name]

    # draftfast deep-copies the pool when constraints exist, so restore the
    # returned roster's blended projections before scoring/displaying it.
    if roster and score_by == 'ceiling':
        for p in roster.players:
            p.proj = original_proj.get(p.name, p.proj)
    return roster


def get_score(roster):
    return sum(p.proj for p in roster.players)


def get_wta_score(roster):
    """Tournament ceiling: sum each player's best available ceiling.

    Reported alongside total_score as an upside diagnostic. It is deliberately
    NOT the selection objective: the ceiling is already blended into each
    player's weighted proj at SHOWDOWN_UPSIDE_WEIGHT, and ranking on raw ceiling
    would override that weight (effectively pinning it to 1.0).
    """
    return sum(p.kv_store.get('ceiling') or p.proj for p in roster.players)


def _roster_key(roster):
    return tuple(sorted(p.name for p in roster.players))


class _EnumeratedRoster:
    """Minimal roster wrapper for lineups built by the exact single-game search."""
    __slots__ = ('players',)

    def __init__(self, players):
        self.players = players


def enumerate_single_game_lineups(players, limit=None):
    """Exhaustively enumerate legal single-game rosters, ranked by weighted proj.

    The showdown pool is small (two teams), so an exact search is practical and
    guarantees we do not miss a higher-scoring construction that the local
    optimizer/diversification loop would skip.

    Ranking uses each player's blended projection, which already includes that
    player's ceiling at config.SHOWDOWN_UPSIDE_WEIGHT. So the selected roster is
    the roster with the highest displayed total_score, and the ceiling is a
    reported diagnostic rather than a second competing objective.
    """
    def base_name(name):
        return name.replace(' (MVP)', '')

    locked = list(dict.fromkeys(config.LOCKED))
    banned = set(BANNED) - set(locked)
    locked_bases = {base_name(name) for name in locked}

    regular_pool = [
        p for p in players
        if p.pos != 'MVP'
        and p.name not in banned
        and base_name(p.name) not in locked_bases
        and not block_function(p)
    ]
    mvp_pool = [
        p for p in players
        if p.pos == 'MVP'
        and p.name not in banned
        and base_name(p.name) not in locked_bases
        and not block_function(p)
    ]

    locked_regulars = []
    locked_mvps = []
    for name in locked:
        if name.endswith(' (MVP)'):
            p = next((x for x in players if x.name == name), None)
            if p is None or p.name in banned or block_function(p):
                return []
            locked_mvps.append(p)
        else:
            p = next((x for x in players if x.name == name and x.pos != 'MVP'), None)
            if p is None or p.name in banned or block_function(p):
                return []
            locked_regulars.append(p)

    if len(locked_mvps) > 1 or len(locked_regulars) > 5:
        return []
    locked_regular_bases = {base_name(p.name) for p in locked_regulars}
    if any(base_name(p.name) in locked_regular_bases for p in locked_mvps):
        return []

    need = 5 - len(locked_regulars)
    if need < 0:
        return []
    if locked_mvps:
        mvp_pool = locked_mvps
    if not mvp_pool:
        return []

    salary_min = ACTIVE_RULE_SET.salary_min
    salary_max = ACTIVE_RULE_SET.salary_max
    pos_max = {
        pos: max_count
        for pos, _, max_count in ACTIVE_RULE_SET.position_limits
        if pos != 'MVP'
    }
    max_team_players = ACTIVE_RULE_SET.max_players_per_team or 99
    min_mvp_cost = min(p.cost for p in mvp_pool)
    max_mvp_cost = max(p.cost for p in mvp_pool)

    results = []
    for combo in itertools.combinations(regular_pool, need):
        parts = list(combo) + locked_regulars
        salary5 = sum(p.cost for p in parts)
        if salary5 + min_mvp_cost > salary_max:
            continue
        if salary5 + max_mvp_cost < salary_min:
            continue

        position_counts = defaultdict(int)
        team_counts = defaultdict(int)
        for p in parts:
            position_counts[p.pos] += 1
            team_counts[p.team] += 1
        if any(position_counts[pos] > cap for pos, cap in pos_max.items()):
            continue
        if any(count > max_team_players for count in team_counts.values()):
            continue
        qb_count = position_counts.get('QB', 0)
        if qb_count > 2:
            continue
        bases = {base_name(p.name) for p in parts}

        for m in mvp_pool:
            if base_name(m.name) in bases:
                continue
            salary = salary5 + m.cost
            if salary < salary_min or salary > salary_max:
                continue
            if team_counts.get(m.team, 0) + 1 > max_team_players:
                continue
            if len(team_counts) + (0 if m.team in team_counts else 1) < 2:
                continue
            mvp_is_qb = m.kv_store.get('base_pos') == 'QB'
            if qb_count + (1 if mvp_is_qb else 0) > 2:
                continue

            roster_players = parts + [m]
            # A defense scores when the opposing passing game fails, so starting
            # it alongside that team's QB bets both sides of one outcome. A full
            # "no opposing offense" rule is impossible in a two-team showdown --
            # the 2-team minimum forces both sides onto every roster, which would
            # ban every defense -- so only the QB pairing is excluded.
            if any(
                (q.kv_store.get('base_pos') or q.pos) == 'QB'
                and q.team == get_opponent(d)
                for d in roster_players if d.pos == 'D'
                for q in roster_players
            ):
                continue
            wta = sum(p.kv_store.get('ceiling') or p.proj for p in roster_players)
            proj = sum(p.proj for p in roster_players)
            results.append((wta, proj, salary, roster_players))

    # Weighted proj is the objective: the ceiling is already baked into it via
    # SHOWDOWN_UPSIDE_WEIGHT, so ranking on raw ceiling would silently override
    # that weight (effectively w=1.0). Ceiling only breaks exact ties.
    results.sort(key=lambda x: (x[1], x[0]), reverse=True)
    unique = []
    seen = set()
    for _, _, _, roster_players in results:
        key = tuple(sorted(p.name for p in roster_players))
        if key in seen:
            continue
        seen.add(key)
        unique.append(_EnumeratedRoster(roster_players))
        if limit and len(unique) >= limit:
            break
    return unique


# Monte Carlo draws used to rank showdown portfolios. Implementation detail
# rather than a strategy knob: 6k keeps the ordering stable and the run ~1s.
WTA_SIMS = 6000


def select_wta_portfolio(rosters, players, count):
    """Pick `count` entries that maximize the chance at least one wins the slate.

    For each legal roster we simulate its score distribution from prior game
    logs, then greedily add the entry that covers the most still-uncovered
    simulated weeks. "Winning" is being the best available score that week, so
    the target is the per-simulation maximum across the pool rather than a fixed
    score -- that keeps the selection criterion identical to the one being
    optimized.

    This targets the actual winner-take-all question -- "will one of my entries
    top the slate?" -- instead of summing per-player 75th percentiles, which
    assumes every player booms in the same week and ignores variance.

    Falls back to weighted-proj order whenever the simulation is unavailable.
    """
    ordered = sorted(rosters, key=get_score, reverse=True)
    if count <= 1 or len(rosters) <= count:
        return ordered[:count]

    try:
        player_scores, defense_scores = compute_score_distributions(WEEK)
    except Exception as e:
        print(f'WTA portfolio simulation unavailable ({e}); using projection order')
        return ordered[:count]
    if not player_scores:
        return ordered[:count]

    rng = np.random.default_rng(0)
    samples = {}

    def sample_vector(p):
        """Empirical score samples for one roster entry (cached per name)."""
        if p.name in samples:
            return samples[p.name]
        values = None
        if p.pos == 'MVP':
            base = p.kv_store.get('base_name', p.name.replace(' (MVP)', ''))
            raw = player_scores.get(normalize_player_name(base))
            values = [v * 1.5 for v in raw] if raw else None
        elif p.pos == 'D':
            values = defense_scores.get(p.team.lower())
        elif p.pos != 'K':
            values = player_scores.get(normalize_player_name(p.name))
        # No history (kickers) -> hold at projection: no simulated variance.
        vec = (rng.choice(np.asarray(values, dtype=float), size=WTA_SIMS)
               if values else np.full(WTA_SIMS, p.proj))
        samples[p.name] = vec
        return vec

    scores = np.zeros((len(rosters), WTA_SIMS), dtype=np.float32)
    for i, roster in enumerate(rosters):
        for p in roster.players:
            scores[i] += sample_vector(p)

    wins = scores >= scores.max(axis=0)

    # Seed with the highest weighted proj: that is the entry the header
    # justifies, and forcing it costs no portfolio value (validated cross-model).
    seed = int(np.argmax([get_score(r) for r in rosters]))
    picked = [seed]
    covered = wins[seed].copy()
    while len(picked) < min(count, len(rosters)):
        gains = (covered | wins).mean(axis=1)
        gains[picked] = -1.0
        best = int(np.argmax(gains))
        if gains[best] <= 0:
            break
        picked.append(best)
        covered |= wins[best]

    chosen = [rosters[i] for i in picked]
    # Lead with the highest weighted proj so the headline entry keeps a
    # total_score justification; the portfolio as a set is what optimizes WTA.
    chosen.sort(key=get_score, reverse=True)
    return chosen


def generate_lineups(players, target_count):
    """Explore blended, pure-ceiling, sneaky-play, and diversified builds.

    Every candidate is ranked by its weighted projection (total_score), which
    already includes each player's ceiling through SHOWDOWN_UPSIDE_WEIGHT. That
    keeps the selection objective identical to the number printed in the lineup
    header, so the top lineup can be justified from the displayed total.
    """
    # The two-team showdown pool is small enough for an exact search. That
    # guarantees we see every legal construction, including sneaky-play builds
    # that a local rotation would otherwise miss, before the WTA portfolio
    # selector chooses which entries to actually play.
    regular_count = sum(1 for p in players if p.pos != 'MVP')
    mvp_count = sum(1 for p in players if p.pos == 'MVP')
    if regular_count <= 30 and mvp_count <= 30:
        enumerated = enumerate_single_game_lineups(players, limit=None)
        if enumerated:
            return select_wta_portfolio(enumerated, players, target_count)

    candidates = []
    seen = set()

    def add(roster):
        if not roster:
            return
        key = _roster_key(roster)
        if key not in seen:
            seen.add(key)
            candidates.append(roster)

    # 1. The normal blended-projection optimum.
    add(optimize_lineup(players))

    # 2. A direct ceiling optimum: this is the purest WTA search.
    add(optimize_lineup(players, score_by='ceiling'))

    locked_bases = {name.replace(' (MVP)', '') for name in config.LOCKED}
    banned_bases = {name.replace(' (MVP)', '') for name in BANNED}

    # 3. Force each of the best cheap, high-ceiling plays into its own build.
    for name, *_ in sneaky_upside_plays[:config.SHOWDOWN_SNEAKY_LINEUP_SEEDS]:
        base = name.replace(' (MVP)', '')
        if base in locked_bases or base in banned_bases:
            continue
        add(optimize_lineup(players, score_by='ceiling', extra_locked=[name]))

    if not candidates:
        return []

    # 4. Diversify from the current best by rotating out its lowest-ceiling
    #    non-locked player. One at a time keeps a six-player showdown feasible.
    extra_banned = []
    while len(candidates) < target_count:
        base_roster = max(candidates, key=get_score)
        removable = [
            p.name for p in sorted(
                base_roster.players,
                key=lambda x: x.kv_store.get('ceiling') or x.proj,
            )
            if p.name.replace(' (MVP)', '') not in locked_bases
            and p.name.replace(' (MVP)', '') not in banned_bases
            and p.name not in extra_banned
        ][:1]
        if not removable:
            break
        extra_banned.append(removable[0])
        add(optimize_lineup(players, score_by='ceiling', extra_banned=extra_banned))

    return sorted(candidates, key=get_score, reverse=True)


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

    total_score = get_score(roster)
    wta_ceiling = get_wta_score(roster)

    print("\n" + "=" * 122)
    print(f"OPTIMIZED LINEUP (total_score={total_score:.2f}, "
          f"wta_ceiling={wta_ceiling:.2f} [+{wta_ceiling - total_score:.2f} upside], "
          f"teams={num_teams_in_roster}/{max_teams_possible})\n---")

    position_order = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'D': 4, 'MVP': 5, 'FLEX': 6}

    roster_data = []
    total_salary = 0
    no_ceiling = []
    for p in roster.players:
        base_fppg = _safe_float(p.kv_store.get('base_proj') or p.kv_store.get('FPPG'))
        salary = int(p.cost)
        opponent = get_opponent(p)
        spread = favor_map.get(p.team, 0)
        total_salary += salary
        weather_factor = _safe_float(p.kv_store.get('weather_factor'), 1.0)
        if p.kv_store.get('ceiling') is None:
            no_ceiling.append(p.name)
        ceiling = _safe_float(p.kv_store.get('ceiling'), p.proj)

        roster_data.append({
            'slot': p.pos,
            'name': p.name[:24],
            'team': p.team,
            'opp': opponent,
            'salary': f"${salary:,}",
            'weather': f"{weather_factor:.3f}",
            'base': f"{base_fppg:6.2f}",
            'ceil': f"{ceiling:6.2f}",
            'proj': f"{p.proj:6.2f}",
            'value': f"{(p.proj / salary) * 1000:6.1f}x",
            'spread': f"{spread:+.1f}",
            'pos_order': position_order.get(p.pos, 99),
        })

    roster_data.sort(key=lambda x: x['pos_order'])

    columns = [
        ('slot', 'Slot', 6), ('name', 'Name', 24), ('team', 'Team', 5),
        ('opp', 'Opp', 5), ('salary', 'Salary', 10), ('weather', 'Weather', 8),
        ('base', 'Base Proj', 12), ('ceil', 'Ceil', 9),
        ('proj', 'Weighted Proj', 15), ('value', 'Value', 10), ('spread', 'Spread', 8),
    ]
    row_fmt = ' '.join(f"{{{key}:<{width}}}" for key, _, width in columns)
    header = row_fmt.format(**{key: label for key, label, _ in columns})
    rule = '-' * len(header)

    print(header)
    print(rule)
    for row in roster_data:
        print(row_fmt.format(**row))

    print(rule)
    print(f"{'TOTAL':<6} {f'${total_salary:,}':<24} "
          f"weighted proj {total_score:.2f}   ceiling {wta_ceiling:.2f}")
    print(rule)

    # Make the objective auditable: every point in total_score comes from the
    # per-player blend below, and the ceiling column shows the upside it draws on.
    print(f"weighted proj = {1 - upside_weight:.2f} * adjusted + {upside_weight:.2f} * ceiling "
          f"(upside applied only when ceiling > adjusted; upside weight = {upside_weight:.2f})")
    if no_ceiling:
        print(f"no ceiling history for: {', '.join(no_ceiling)} "
              f"(weighted proj = adjusted projection, no upside credit)")


if sneaky_upside_plays:
    print_table(
        "SNEAKY UPSIDE PLAYS (cheap, ceiling >> cost - low-rostered boom candidates)",
        f"{'Player':<26} {'Pos':<4} {'Salary':>8} {'Proj':>7} {'Ceiling':>8} {'Ceil/$1k':>8}",
        [f"{name:<26} {pos:<4} ${cost:>7,} {proj:>7.2f} {ceiling:>8.2f} {uptv:>7.1f}x"
         for name, pos, cost, proj, ceiling, uptv in sneaky_upside_plays[:config.SHOWDOWN_SNEAKY_TOP]],
        66,
    )
    print("=" * 66)

print_data_status()

# ============================================================
# Lineup generation: upside-weighted projection selection.
# Set NUM_LINEUPS=<n> to generate alternatives; locks are preserved in all.
# ============================================================

NUM_LINEUPS = int(os.environ.get('NUM_LINEUPS') or (config.DEFAULT_SINGLE_LINEUPS if SINGLE_GAME else 0))

if SINGLE_GAME:
    # Exact search first: enumerate every legal showdown roster when the pool is
    # small, then fall back to solver/ceiling/diversification builds.
    rosters = generate_lineups(players, max(NUM_LINEUPS, 1))
else:
    roster = optimize_lineup(players)
    if not roster:
        rosters = []
    elif NUM_LINEUPS > 1:
        # Classic slates keep the projection-based rotation, and the resulting
        # builds are ranked by the same weighted proj shown in the header.
        locked_bases = {name.replace(' (MVP)', '') for name in config.LOCKED}
        banned_bases = {name.replace(' (MVP)', '') for name in BANNED}
        extra_banned = []
        rosters = [roster]
        for _ in range(NUM_LINEUPS - 1):
            prev = rosters[-1]
            removable = [
                p.name for p in sorted(
                    prev.players,
                    key=lambda x: x.kv_store.get('ceiling') or x.proj,
                )
                if p.name.replace(' (MVP)', '') not in locked_bases
                and p.name.replace(' (MVP)', '') not in banned_bases
                and p.name not in extra_banned
            ][:1]
            if not removable:
                break
            extra_banned.append(removable[0])
            r = optimize_lineup(players, extra_banned=extra_banned)
            if not r:
                break
            rosters.append(r)
        rosters = sorted(rosters, key=get_score, reverse=True)
    else:
        rosters = [roster]

roster = None
if not rosters:
    print("No solution")
else:
    roster = rosters[0]
    print_optimized_roster(roster)

    if NUM_LINEUPS > 1:
        order = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'D': 4, 'MVP': 5, 'FLEX': 6}
        print(f"\nCompared {len(rosters)} diversified lineups; showing the top (set DEBUG=1 to list all)")
        if os.environ.get('DEBUG'):
            print_banner(f"TOP {len(rosters)} DIVERSIFIED LINEUPS (ranked by weighted proj)", 100)
            for i, r in enumerate(rosters, 1):
                names = ' | '.join(
                    f"{p.name.replace(' (MVP)', '')}({p.pos})"
                    for p in sorted(r.players, key=lambda x: order.get(x.pos, 9))
                )
                print(f"{i:>2}. proj {get_score(r):>6.2f} | WTA {get_wta_score(r):>6.2f}  {names}")
            print("=" * 100)

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
    print("-" * 25)
    print(f"Overall Diversity Score: {diversity['overall_score']}/100")
    print(f"  - Team Diversity:      {diversity['team_diversity']}/40")
    print(f"  - Position Diversity:  {diversity['position_diversity']}/30")
    print(f"  - Stack Penalty:       {diversity['stack_penalty']}/30")

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
