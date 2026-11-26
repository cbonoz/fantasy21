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
import fantasypros
from fantasypros import build_projection_map as build_fp_projection_map
from nfl_teams import NFL_TEAM_MAP, normalize_player_name, normalize_team_abbr
from optimizer_rules import (
    forbid_opposing_positions_with_defense,
    is_player_override,
    min_players_per_team_rule,
    split_active_locks,
    should_block_backup_qb,
    upload_slots,
)
import numpy as np
import pandas as pd
from odds import get_metabet_spread
from projection import (
    blend_available_projections,
    cap_projection,
    combined_weather_factor,
    compute_role_trend_bonus,
    compute_role_competition_penalty,
    compute_qb_health_drop,
    compute_qb_pass_catcher_penalty,
    allocate_vacated_role_bonus,
    adjust_ceiling_for_role_change,
    adjust_ceiling_for_weather,
    compute_kicker_total_bonus,
    compute_same_team_wr_overlap_penalty,
    compute_same_team_wr_te_overlap_penalty,
    compute_team_totals,
    compute_wr_injury_bonus,
    qb_quality_multiplier,
    scale_source_weight,
)
from nflverse import (
    compute_fpa_map,
    compute_defense_upside_map,
    compute_giveaway_map,
    compute_history,
    compute_qb_snap_share_map,
    compute_returning_competition_map,
    compute_recent_opportunity_map,
    compute_recent_form_map,
    compute_recent_target_share_map,
    compute_target_share_trend_map,
    compute_upside_map,
)
from sleeper import build_projection_map
from weather import (
    display_weather_summary,
    get_nfl_weather,
    matchup_coverage,
    team_name_to_abbr,
)

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
STALE_LOCK_WARNING_REPORTED = False
RELAXED_HEURISTICS_WARNING_REPORTED = False


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
    name_width = max(20, max((len(name) for name in DATA_STATUS), default=0))
    for name in sorted(DATA_STATUS):
        status, detail = DATA_STATUS[name]
        print(f"  {name:<{name_width}} {status:<18} {detail}")
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

weather_df = get_nfl_weather(
    WEEK,
    force_refresh=os.getenv('FANTASY_FORCE_WEATHER_REFRESH') == '1',
)
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
        covered_matchups, expected_matchups, missing_matchups = matchup_coverage(
            df['Game'].dropna().astype(str), weather_df
        )
        detail = f"{covered_matchups}/{expected_matchups} slate matchups covered"
        if invalid_count:
            detail += f"; ignored {invalid_count} invalid rows"
        if missing_matchups:
            detail += f"; missing {missing_matchups} matchups"
        _record_source('Weather', not missing_matchups, detail)
        if missing_matchups:
            print(f"WARNING: weather missing for slate matchups: {missing_matchups}")
else:
    _record_source('Weather', False, 'no data for this week')

# ============================================================
# Filter player pool
# ============================================================

set_teams = set(df['Team'])
SINGLE_GAME = len(set_teams) == 2
SLATE_GAME_COUNT = int(df['Game'].dropna().astype(str).str.strip().replace('', pd.NA).nunique())
if SLATE_GAME_COUNT == 0:
    SLATE_GAME_COUNT = max(len(set_teams) // 2, 1)

MIN_SALARY = config.MIN_SALARY_SINGLE if SINGLE_GAME else config.MIN_SALARY_CLASSIC

df['Name'] = df['First Name'] + " " + df['Last Name']
df['Salary/FPPG'] = df['FPPG'] / df['Salary']

questionable_players = list(df[(~df['Injury Indicator'].isna()) | (~df['Injury Details'].isna())]['Name'])
low_salary_players = list(df[((df['Salary'] < MIN_SALARY)) & (df['Position'] != 'D')]['Name'])
excluded_players = set([*questionable_players, *low_salary_players])

# Manual re-additions (weekly tuned). ALLOW_PLAYERS also bypasses automatic
# eligibility filters later in the optimizer.
for p in [*config.READD, *config.ALLOW_PLAYERS]:
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

recent_form_map = {}
if not SINGLE_GAME:
    try:
        recent_form_map = compute_recent_form_map(WEEK) or {}
        _record_source(
            'Recent scoring form', bool(recent_form_map),
            f"{len(recent_form_map)} players with recent appearance samples",
        )
    except Exception as e:
        _record_source('Recent scoring form', False, str(e))
        print('Error loading recent scoring form:', e)
        recent_form_map = {}
else:
    _record_source('Recent scoring form', False, 'not used for single-game slates')

recent_target_share_map = {}
try:
    recent_target_share_map = compute_recent_target_share_map(WEEK) or {}
    _record_source('Recent pass-catcher usage', bool(recent_target_share_map),
                   f"{len(recent_target_share_map)} player entries")
    role_trend_map = compute_target_share_trend_map(WEEK) or {}
    _record_source('Target-share trends', bool(role_trend_map),
                   f"{len(role_trend_map)} player entries")
except Exception as e:
    _record_source('Recent pass-catcher usage', False, str(e))
    _record_source('Target-share trends', False, str(e))
    print('Error loading recent receiving usage:', e)
    recent_target_share_map = {}
    role_trend_map = {}

recent_opportunity_map = {}
try:
    recent_opportunity_map = compute_recent_opportunity_map(WEEK) or {}
    _record_source(
        'Recent workload', bool(recent_opportunity_map),
        f"{len(recent_opportunity_map)} player roles; opportunities and snap shares used where available",
    )
except Exception as e:
    _record_source('Recent workload', False, str(e))
    print('Error loading recent workload:', e)
    recent_opportunity_map = {}

returning_competition_map = {}
try:
    available_role_players = {}
    for _, row in df.iterrows():
        if row.get('Position') in ('RB', 'WR', 'TE') and row.get('Name'):
            key = (normalize_team_abbr(row['Team']), row['Position'],
                   normalize_player_name(row['Name']))
            available_role_players[key] = 1.0
    for _, row in questionable_df.iterrows():
        indicator = str(row.get('Injury Indicator') or '').strip().upper()
        if indicator == 'Q' and row.get('Position') in ('RB', 'WR', 'TE') and row.get('Name'):
            key = (normalize_team_abbr(row['Team']), row['Position'],
                   normalize_player_name(row['Name']))
            available_role_players[key] = config.INJURY_QUESTIONABLE_WEIGHT
    returning_competition_map = compute_returning_competition_map(
        WEEK, available_players=available_role_players
    ) or {}
    _record_source(
        'Returning teammate roles', True,
        f"{len(returning_competition_map)} role reductions detected"
    )
except Exception as e:
    _record_source('Returning teammate roles', False, str(e))
    print('Error loading returning-teammate role data:', e)
    returning_competition_map = {}

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

if not config.FANTASYPROS_USE_API:
    print("FantasyPros: HTML mode (API disabled; the free API is too limited)")
elif os.environ.get('FANTASYPROS_API_KEY'):
    print("FantasyPros API: enabled, key configured")
else:
    print("FantasyPros API: enabled but no key set; using the HTML page")

fp_proj_map = {}
fp_source_status = {
    'status': 'unavailable', 'requested_week': WEEK,
    'source_week': None, 'detail': 'not loaded',
}
try:
    fp_proj_map = build_fp_projection_map(WEEK) or {}
    fp_source_status = fantasypros.get_projection_source_status()
    if fp_source_status.get('source_week') not in (None, WEEK):
        # Previous-week projections are useful provenance, not valid current
        # inputs. Let the normalized blend fall back to the other sources.
        print(f"Ignoring stale FantasyPros week {fp_source_status.get('source_week')} fallback")
        fp_proj_map = {}
    fp_status = fp_source_status.get('status')
    if (fp_proj_map and fp_status != 'ok'
            and not config.FANTASYPROS_ALLOW_DEGRADED):
        # A partial scrape covers only part of the pool. Giving a 35%-weight
        # weekly source to some players and not others silently changes each
        # player's effective blend, so drop it entirely and let the normalized
        # blend fall back consistently across the remaining sources.
        print(
            f"WARNING: FantasyPros projections are '{fp_status}' (partial scrape); "
            "excluding them because FANTASYPROS_ALLOW_DEGRADED is off. "
            "Set FANTASYPROS_API_KEY or fix the scraper for full coverage."
        )
        fp_proj_map = {}
    _record_source(
        'FantasyPros',
        bool(fp_proj_map) and fp_source_status.get('status') == 'ok',
        f"{len(fp_proj_map)} projections; {fp_source_status.get('detail', 'unknown source status')}",
    )
    print(
        f"Loaded {len(fp_proj_map)} FantasyPros projections "
        f"({fp_source_status.get('method', 'unknown')})"
    )
except Exception as e:
    _record_source('FantasyPros', False, str(e))
    print('Error loading FantasyPros projections:', e)
    fp_proj_map = {}

# FantasyPros' free API (and the public HTML page) return only the top ten
# players per position, so coverage can be partial even when the fetch itself
# succeeds. Scale the blend weight by the covered fraction either way so the
# players it includes do not receive a full extra source the rest of the pool
# never gets.
PROJECTION_WEIGHTS = dict(config.PROJECTION_WEIGHTS)
if fp_proj_map:
    pool = df[df['Position'].isin(['QB', 'RB', 'WR', 'TE', 'K', 'D'])]
    covered = 0
    for row in pool.itertuples():
        position = 'D' if row.Position == 'D' else row.Position
        name = '' if position == 'D' else normalize_player_name(row.Name)
        if fp_proj_map.get((row.Team, position, name)):
            covered += 1
    coverage = covered / len(pool) if len(pool) else 0.0
    if coverage < 1 and config.FANTASYPROS_SCALE_WEIGHT_BY_COVERAGE:
        PROJECTION_WEIGHTS['fantasypros'] = scale_source_weight(
            PROJECTION_WEIGHTS.get('fantasypros'), coverage)
    if coverage < 1:
        print(
            f"Partial FantasyPros source: {covered}/{len(pool)} players "
            f"({coverage:.0%} coverage), blend weight "
            f"{PROJECTION_WEIGHTS.get('fantasypros', 0):.2f}"
        )

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
# Vacated production is routed to active same-position players by recent
# opportunity share, instead of giving every teammate the full pool.
pos_bonus_by_player = defaultdict(float)
injured_qb = defaultdict(lambda: False)
# When a team's WR is out, remaining pass catchers inherit some of the target
# pool, allocated by recent target share (on top of the shared excluded_bonus).
pass_catcher_bonus = defaultdict(lambda: 0)

# Identify each team's available QB and project their quality from the same
# season/history/weekly sources used for player projections. Prefer prior snap
# share to distinguish the starter from backups; fall back to games played.
qb_candidates = pd.concat([
    df[df['Position'] == 'QB'],
    questionable_df[questionable_df['Position'] == 'QB'],
], ignore_index=True)
qb_quality_candidates = defaultdict(list)


def qb_unavailability_probability(qb):
    """Translate a QB's injury status/details into a conservative miss chance."""
    indicator = str(qb.get('Injury Indicator') or '').strip().upper()
    if indicator in ('O', 'IR'):
        return 1.0
    if indicator == 'D':
        return config.INJURY_DOUBTFUL_WEIGHT
    if indicator == 'Q':
        return config.INJURY_QUESTIONABLE_WEIGHT
    details = str(qb.get('Injury Details') or '').strip()
    if details and details.lower() not in ('nan', 'none', 'n/a', 'healthy'):
        return config.QB_UNSPECIFIED_INJURY_WEIGHT
    return 0.0


for _, qb in qb_candidates.iterrows():
    team = normalize_team_abbr(qb['Team'])
    name = normalize_player_name(qb['Name'])
    fppg = float(qb['FPPG']) if pd.notna(qb['FPPG']) else 0.0
    history = historic_averages.get(name)
    sleeper_qb = sleeper_proj_map.get((team, 'QB', name))
    fp_qb = fp_proj_map.get((team, 'QB', name))
    base = blend_available_projections({
        'fd_fppg': fppg,
        'nflverse_history': history,
        'sleeper': sleeper_qb,
        'fantasypros': fp_qb,
    }, PROJECTION_WEIGHTS)
    snap_share = qb_snap_share_map.get((team, name))
    played = float(qb['Played']) if pd.notna(qb['Played']) else 0.0
    # Season appearances establish the likely primary; snap share and FPPG
    # break ties, so a recent fill-in's 100% snaps do not erase the starter's
    # longer-term role when that starter is expected back.
    starter_key = (played, snap_share is not None, snap_share or 0.0, fppg)
    qb_quality_candidates[team].append({
        'key': starter_key,
        'name': name,
        'projection': base,
        'unavailability': qb_unavailability_probability(qb),
    })

team_qb_projections = {}
qb_health_drop_by_team = {}
qb_primary_by_team = {}
for team, candidates in qb_quality_candidates.items():
    candidates.sort(key=lambda candidate: candidate['key'], reverse=True)
    primary = candidates[0]
    replacements = [
        (candidate['projection'], candidate['unavailability'])
        for candidate in candidates[1:]
    ]
    health_drop = compute_qb_health_drop(
        primary['projection'], primary['unavailability'], replacements
    )
    qb_health_drop_by_team[team] = health_drop
    qb_primary_by_team[team] = primary['name']
    team_qb_projections[team] = max(primary['projection'] - health_drop, 0.0)
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


def available_role_weights(team, pos):
    """Recent workload weights for active same-position players on a team."""
    candidates = df[(df['Team'].apply(normalize_team_abbr) == team)
                    & (df['Position'] == pos)]
    raw_weights = {}
    for _, player in candidates.iterrows():
        name = normalize_player_name(player['Name'])
        role = recent_opportunity_map.get((team, pos, name), {})
        weight = float(role.get('opportunities') or 0)
        if weight <= 0:
            weight = float(role.get('snap_share') or 0)
        if weight <= 0:
            weight = float(player['FPPG']) if pd.notna(player['FPPG']) else 0.0
        if weight > 0:
            raw_weights[name] = weight
    return raw_weights

for index, p in questionable_df.iterrows():
    pos = p['Position']
    if pos in ['TE', 'WR', 'RB', 'QB']:
        # Out (O) / Injured Reserve (IR) fund full bonuses. Doubtful (D) and
        # Questionable (Q) statuses receive progressively smaller transfers.
        indicator = str(p['Injury Indicator']).strip().upper()
        if indicator not in ('O', 'IR', 'Q', 'D'):
            continue
        points = float(p['FPPG']) if pd.notna(p['FPPG']) else 0.0
        played = float(p['Played']) if pd.notna(p['Played']) else 0.0
        team = normalize_team_abbr(p['Team'])
        injured_role = recent_opportunity_map.get((
            team, pos, normalize_player_name(p['Name'])
        ), {})
        role_qualified = (
            injured_role.get('opportunities_per_game', 0)
            >= config.INJURY_ROLE_MIN_OPPORTUNITIES.get(pos, float('inf'))
            or injured_role.get('snap_share', 0)
            >= config.INJURY_ROLE_MIN_SNAP_SHARE.get(pos, float('inf'))
        )
        production_qualified = points >= config.MIN_INJURY_FPPG
        if (production_qualified or role_qualified) and played >= WEEK / 2:
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
                    injured_qb[team] = True
            elif pos in ('RB', 'WR', 'TE'):
                amt = injury_offset * config.INJURY_SKILL_MULTIPLIER * weight
                if pos in ('RB', 'TE'):
                    allocated = allocate_vacated_role_bonus(
                        amt, available_role_weights(team, pos)
                    )
                    for player_name, player_bonus in allocated.items():
                        pos_bonus_by_player[(team, pos, player_name)] += player_bonus
                if pos == 'WR':
                    pass_catcher_bonus[team] += amt
            else:
                amt = injury_offset * weight
            excluded_bonus[team] += amt

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
    for name, proj, cost, value, pos, base_fppg, opponent, ceiling in sorted(
        mvps, key=lambda x: x[7], reverse=True
    ):
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

        manual_override = is_player_override(base_name, config.ALLOW_PLAYERS)
        is_starter = (
            not starter_name or starter_name == base_name or manual_override
        )
        if is_starter or starter_injured:
            if manual_override and starter_name != base_name:
                status = "MANUAL PLAYER OVERRIDE"
            if name in config.BANNED_SINGLE:
                status = f"BANNED / {status}"
            elif name in config.LOCKED:
                status = f"LOCKED / {status}"
            filtered_mvps.append((
                name, proj, cost, value, pos, base_fppg,
                player_games, opponent, status, ceiling,
            ))

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
    role_adjustment = 0.0
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
    # Active pass catchers share vacated WR targets. RB/TE absences also retain
    # the same-position workload bonus below.
    if p.pos in ('WR', 'TE', 'RB'):
        vacated_targets_bonus = compute_wr_injury_bonus(
            pass_catcher_bonus.get(p.team, 0),
            pass_catcher_base_shares.get(p.team, {}).get(p.name, 0),
            qb_quality_factors.get(p.team, 1.0),
            config.WR_INHERITANCE_CAP,
            config.WR_INHERITANCE_WEIGHT,
        )
        bonuses += vacated_targets_bonus
        role_adjustment += vacated_targets_bonus
        if vacated_targets_bonus > 0:
            p.kv_store['vacated_targets_bonus'] = vacated_targets_bonus
    if p.pos in ('RB', 'TE'):
        workload_bonus = pos_bonus_by_player.get((
            normalize_team_abbr(p.team), p.pos, normalize_player_name(p.name)
        ), 0.0) * qb_quality_factors.get(p.team, 1.0)
        bonuses += workload_bonus
        role_adjustment += workload_bonus
        if workload_bonus > 0:
            p.kv_store['vacated_workload_bonus'] = workload_bonus
    if p.pos == 'RB' and injured_qb.get(p.team, False):
        bonuses += INJURED_QB_BONUS
        role_adjustment += INJURED_QB_BONUS
    if role_adjustment > 0:
        p.kv_store['role_adjustment'] = role_adjustment
    return bonuses


def get_blended_projection(p, history_key):
    """Blend current FPPG, nflverse history, Sleeper, and FantasyPros inputs."""
    return blend_available_projections({
        'fd_fppg': p.proj,
        'nflverse_history': historic_averages.get(history_key),
        'sleeper': sleeper_projection_for(p),
        'fantasypros': fp_projection_for(p),
    }, PROJECTION_WEIGHTS)


def calculate_role_trend_adjustment(p):
    """Small capped role-trend bonus for pass-catchers and running backs."""
    if p.pos not in ('WR', 'TE', 'RB'):
        return 0.0
    usage = role_trend_map.get((
        normalize_team_abbr(p.team), normalize_player_name(p.name)
    ))
    if not usage or usage['delta'] < config.ROLE_TREND_MIN_TARGET_SHARE_DELTA:
        return 0.0
    role_bonus = compute_role_trend_bonus(
        usage['recent'], usage['earlier'],
        config.ROLE_TREND_POINT_WEIGHT,
        config.ROLE_TREND_MAX_BONUS,
    )
    if role_bonus:
        p.kv_store['role_trend_bonus'] = role_bonus
        p.kv_store['role_adjustment'] = (
            p.kv_store.get('role_adjustment', 0.0) + role_bonus
        )
    return role_bonus


def calculate_returning_role_adjustment(p):
    """Apply a small penalty when recent data confirms a teammate reclaimed work."""
    if p.pos not in ('RB', 'WR', 'TE'):
        return 0.0
    role = returning_competition_map.get((
        normalize_team_abbr(p.team), p.pos, normalize_player_name(p.name)
    ))
    if not role:
        return 0.0
    penalty = compute_role_competition_penalty(
        role.get('recent_share'),
        role.get('earlier_share'),
        role.get('returning_teammate_share'),
        config.RETURNING_ROLE_MIN_SHARE_DROP,
        config.RETURNING_ROLE_MIN_SHARE,
        config.RETURNING_ROLE_POINTS_PER_SHARE,
        config.RETURNING_ROLE_MAX_PENALTY,
    )
    if penalty:
        p.kv_store['returning_role_penalty'] = penalty
        p.kv_store['returning_teammate'] = role.get('returning_teammate')
    return -penalty


def calculate_qb_pass_catcher_adjustment(p):
    """Modestly reduce WR/TE projections for uncertain or downgraded QB play."""
    if p.pos not in ('WR', 'TE'):
        return 0.0
    team = normalize_team_abbr(p.team)
    qb_drop = qb_health_drop_by_team.get(team, 0.0)
    penalty = compute_qb_pass_catcher_penalty(
        qb_drop,
        p.kv_store.get('base_proj', p.proj),
        config.QB_PASS_CATCHER_SENSITIVITY,
        config.QB_PASS_CATCHER_PROJECTION_SCALE,
        config.QB_PASS_CATCHER_MAX_PENALTY,
    )
    if penalty:
        p.kv_store['qb_status_penalty'] = penalty
        p.kv_store['qb_status_projection_drop'] = qb_drop
        p.kv_store['qb_primary'] = qb_primary_by_team.get(team, '')
    return -penalty


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
    """Sleeper half-PPR projection, keyed by base position/name."""
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
                combined_factor = combined_weather_factor(
                    wind, temp, precip, pos,
                    config.WEATHER_MAX_ADJUSTMENT.get(pos),
                )
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

    # Kickers use an empirical source blend plus conservative scoring-volume
    # and weather adjustments; cheaper non-defense players still receive
    # evidence-backed injury/role changes below.
    if p.pos == 'K':
        base_score = get_blended_projection(p, history_key_for(p))
        p.kv_store['base_proj'] = base_score
        total_bonus = compute_kicker_total_bonus(
            team_totals.get(p.team), avg_team_total,
            config.KICKER_TOTAL_WEIGHT,
            config.MAX_KICKER_TOTAL_BONUS,
        )
        if total_bonus:
            p.kv_store['kicker_total_bonus'] = total_bonus
        weather_factor = weather_factor_map.get((p.team, 'K'), 1.0)
        if weather_factor != 1.0:
            p.kv_store['weather_factor'] = weather_factor
        weather_bonus = base_score * (weather_factor - 1.0)
        max_weather_bonus = base_score * config.MAX_WEATHER_BONUS
        weather_bonus = max(min(weather_bonus, max_weather_bonus), -max_weather_bonus)
        return cap_projection(
            base_score + total_bonus + weather_bonus,
            base_score,
            p.pos,
            max_score=MAX_SCORE,
            min_proj_multiplier=config.MIN_PROJ_MULTIPLIER,
            max_def_multiplier=config.MAX_DEF_MULTIPLIER,
            max_proj_multiplier=config.MAX_PROJ_MULTIPLIER,
        )

    base_score = get_blended_projection(p, history_key_for(p))
    p.kv_store['base_proj'] = base_score

    opponent = get_opponent(p)

    if p.cost <= config.LOW_SALARY_SKIP and p.pos != 'D':
        role_bonus = calculate_injury_bonuses(p, opponent)
        role_bonus += calculate_role_trend_adjustment(p)
        role_bonus += calculate_returning_role_adjustment(p)
        role_bonus += calculate_qb_pass_catcher_adjustment(p)
        return cap_projection(
            base_score + role_bonus,
            base_score,
            p.pos,
            max_score=MAX_SCORE,
            min_proj_multiplier=config.MIN_PROJ_MULTIPLIER,
            max_def_multiplier=config.MAX_DEF_MULTIPLIER,
            max_proj_multiplier=config.MAX_PROJ_MULTIPLIER,
        )

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

    # A sustained rise in target share is an early signal of a changing role;
    # give it a small capped median-projection adjustment, separate from the
    # recent-ceiling tilt used for GPPs.
    role_bonus = calculate_role_trend_adjustment(p)
    matchup_bonus += role_bonus
    matchup_bonus += calculate_returning_role_adjustment(p)
    matchup_bonus += calculate_qb_pass_catcher_adjustment(p)

    # Defenses/MVPs also account for opponent offense weakness.
    # excluded_bonus is negative when the opponent's QB is out (and positive
    # for skill-position injuries); either way a weakened offense helps the D.
    if p.pos in ['D', 'MVP']:
        matchup_bonus += abs(excluded_bonus.get(opponent, 0)) * config.OPPONENT_INJURY_WEIGHT

    # Weather adjustment (additive, capped at 40% of base)
    weather_bonus = 0
    weather_adjustment_factor = weather_factor_map.get((p.team, p.pos), 1.0)
    if weather_adjustment_factor != 1.0:
        p.kv_store['weather_factor'] = weather_adjustment_factor
        weather_bonus = base_score * (weather_adjustment_factor - 1.0)
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

# Distribute vacated WR targets among active pass catchers using recent target
# share, so an emerging role can outrank season-to-date fantasy points. Fall
# back to FPPG if recent usage is unavailable for the whole team.
pass_catcher_base_shares = defaultdict(dict)
pass_catcher_usage_totals = defaultdict(float)
pass_catcher_fppg_totals = defaultdict(float)
pass_catcher_recent_usage = defaultdict(dict)
for p in players:
    if p.pos in ('WR', 'TE', 'RB'):
        fppg = float(p.kv_store.get('FPPG') or 0)
        usage = recent_target_share_map.get((
            normalize_team_abbr(p.team), normalize_player_name(p.name)
        ), 0.0)
        pass_catcher_fppg_totals[p.team] += max(fppg, 0.0)
        pass_catcher_recent_usage[p.team][p.name] = max(float(usage or 0), 0.0)
        pass_catcher_usage_totals[p.team] += max(float(usage or 0), 0.0)
for p in players:
    if p.pos not in ('WR', 'TE', 'RB'):
        continue
    if pass_catcher_usage_totals[p.team] > 0:
        pass_catcher_base_shares[p.team][p.name] = (
            pass_catcher_recent_usage[p.team].get(p.name, 0.0)
            / pass_catcher_usage_totals[p.team]
        )
    elif pass_catcher_fppg_totals[p.team] > 0:
        fppg = max(float(p.kv_store.get('FPPG') or 0), 0.0)
        pass_catcher_base_shares[p.team][p.name] = (
            fppg / pass_catcher_fppg_totals[p.team]
        )

for p in players:
    p.proj = calculate_adjusted_projection(p)
    if not SINGLE_GAME:
        recent_form = recent_form_map.get(normalize_player_name(p.name))
        if recent_form:
            form_adjustment = float(recent_form.get('adjustment') or 0.0)
            if form_adjustment:
                p.proj = cap_projection(
                    p.proj + form_adjustment,
                    p.kv_store.get('base_proj', p.proj),
                    p.pos,
                    max_score=MAX_SCORE,
                    min_proj_multiplier=config.MIN_PROJ_MULTIPLIER,
                    max_def_multiplier=config.MAX_DEF_MULTIPLIER,
                    max_proj_multiplier=config.MAX_PROJ_MULTIPLIER,
                )
                p.kv_store['recent_form_adjustment'] = form_adjustment
            p.kv_store['recent_form_median'] = recent_form.get('recent_median')
            p.kv_store['season_median'] = recent_form.get('season_median')
    p.kv_store['adjusted_proj'] = p.proj

def _has_projection(value):
    try:
        return value is not None and np.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _player_labels(players, limit=8):
    labels = [f"{p.name} ({p.team}/{p.pos})" for p in players[:limit]]
    omitted = len(players) - len(labels)
    if omitted:
        labels.append(f"+{omitted} more")
    return ', '.join(labels)


# GPP upside tilt: blend projections toward each player's ceiling so lineups
# target boom scores (single-game winners / top-10% GPP finishes) instead of
# point-expectation averages. Also surfaces cheap high-ceiling plays.
upside_map = {}
defense_upside_map = {}
upside_weight = config.SHOWDOWN_UPSIDE_WEIGHT if SINGLE_GAME else config.GPP_UPSIDE_WEIGHT
sneaky_upside_plays = []
if upside_weight > 0:
    try:
        upside_map = compute_upside_map(WEEK) or {}
        defense_upside_map = compute_defense_upside_map(WEEK) or {}
        ceiling_players = [p for p in players if p.pos in ('QB', 'RB', 'WR', 'TE', 'K')]
        ceiling_matched = sum(
            _has_projection(upside_map.get(normalize_player_name(p.name)))
            for p in ceiling_players
        )
        _record_source(
            'Upside/ceiling', ceiling_matched == len(ceiling_players),
            f"{len(upside_map)} history entries; matched {ceiling_matched}/{len(ceiling_players)} active offensive players",
        )
        _record_source(
            'DST ceiling', bool(defense_upside_map),
            f"big-play ceiling surplus available for {len(defense_upside_map)} team defenses; points-allowed variance excluded",
        )
    except Exception as e:
        _record_source('Upside/ceiling', False, str(e))
        _record_source('DST ceiling', False, str(e))
        print('Error loading upside data:', e)
        upside_map = {}
        defense_upside_map = {}
    w = upside_weight
    for p in players:
        weather_position = p.kv_store.get('base_pos') if p.pos == 'MVP' else p.pos
        ceiling_weather_factor = p.kv_store.get(
            'weather_factor', weather_factor_map.get((p.team, weather_position), 1.0)
        )
        if p.pos == 'D':
            surplus = defense_upside_map.get(normalize_team_abbr(p.team).lower())
            if surplus is None:
                continue
            weathered_surplus = adjust_ceiling_for_weather(
                surplus, ceiling_weather_factor, config.MAX_WEATHER_BONUS
            )
            base = p.kv_store.get('base_proj') or p.proj
            def_ceiling_cap = min(config.MAX_SCORE, base * config.MAX_DEF_MULTIPLIER)
            ceiling = min(p.proj + weathered_surplus, max(def_ceiling_cap, p.proj))
        else:
            ceiling = upside_map.get(normalize_player_name(p.name))
            if not ceiling:
                continue
            ceiling = adjust_ceiling_for_weather(
                ceiling, ceiling_weather_factor, config.MAX_WEATHER_BONUS
            )
            if p.pos == 'K':
                ceiling += p.kv_store.get('kicker_total_bonus', 0.0)
        ceiling = adjust_ceiling_for_role_change(
            ceiling,
            p.kv_store.get('role_adjustment', 0.0),
            config.ROLE_CEILING_BONUS_WEIGHT,
            config.MAX_ROLE_CEILING_BONUS,
        )
        ceiling += p.kv_store.get('recent_form_adjustment', 0.0)
        role_risk = (
            p.kv_store.get('returning_role_penalty', 0.0)
            * config.RETURNING_ROLE_CEILING_WEIGHT
            + p.kv_store.get('qb_status_penalty', 0.0)
            * config.QB_PASS_CATCHER_CEILING_WEIGHT
        )
        ceiling = max(ceiling - role_risk, p.proj)
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
            ceiling = p.kv_store.get('ceiling') or upside_map.get(normalize_player_name(p.name))
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
        mvps.append((
            p.name, p.proj, p.cost, p.proj / p.cost, p.pos,
            base_fppg, opponent, p.kv_store.get('ceiling') or p.proj,
        ))
    elif p.pos == 'D':
        defenses.append((p.team, p.proj, p.cost, p.proj / p.cost, base_fppg, favor_map.get(p.team, 0), opponent))
    elif p.pos in ('RB', 'WR', 'TE') and p.proj > 0:
        team_total = team_totals.get(p.team, avg_team_total)
        total_deviation = team_total - avg_team_total
        weather = float(p.kv_store.get('weather_factor', 1.0))
        mean_proj = float(p.kv_store.get('adjusted_proj') or p.proj)
        ceiling = float(p.kv_store.get('ceiling') or p.proj)
        skills.append((p.name, p.pos, p.proj, p.cost, p.proj / p.cost, team_total, total_deviation, base_fppg, mean_proj, ceiling, opponent, weather))
    elif p.pos == 'QB' and p.cost >= MIN_QB_SALARY and p.proj > 0:
        team_total = team_totals.get(p.team, avg_team_total)
        total_deviation = team_total - avg_team_total
        weather = float(p.kv_store.get('weather_factor', 1.0))
        mean_proj = float(p.kv_store.get('adjusted_proj') or p.proj)
        ceiling = float(p.kv_store.get('ceiling') or p.proj)
        qbs.append((p.name, p.pos, p.proj, p.cost, p.proj / p.cost, team_total, total_deviation, base_fppg, mean_proj, ceiling, opponent, weather))

starter_map = build_starter_map(players, questionable_df)
filtered_mvps = filter_mvps(mvps, players, starter_map)

# ============================================================
# Display: sorted defenses / QBs / MVPs
# ============================================================

PROJ_TABLE_HEADER = (f"{'Name':<35} {'Proj':>8} {'Mean':>6} {'Ceil':>6} {'Salary':>10} {'Value':>8} "
                     f"{'TeamTotal':>10} {'Dev':>8} {'Opp':>5} {'BlendBase':>10} {'Weather':>8}")


def _proj_row(name, proj, cost, value, team_total, total_deviation, base_fppg,
              mean_proj, ceiling, opp, weather):
    arrow = '↓' if weather < 0.85 else ''
    return (f"{name:<35} {proj:>8.2f} {mean_proj:>6.2f} {ceiling:>6.2f} ${cost:>9,.0f} "
            f"{(value * 1000):>7.1f}x {team_total:>10.2f} {total_deviation:>8.2f} "
            f"{opp:>5} {base_fppg:>10.2f} {weather:>7.3f}{arrow}")


def print_sorted_proj_table(title, rows, width=124):
    """Print a QB/skill-position table (shared header and row format)."""
    print_table(title, PROJ_TABLE_HEADER, [_proj_row(r[0], *r[2:]) for r in rows], width)


print_table(
    "SORTED DEFENSES",
    f"{'Team':<8} {'Proj':>8} {'Salary':>10} {'Base':>8} {'Spread':>8} {'Opp':>5} {'Value':>8}",
    [f"{team:<8} {proj:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {spread:>8.2f} {opp:>5} {(value * 1000):>7.1f}x"
     for team, proj, cost, value, base_fppg, spread, opp in sorted(defenses, key=lambda x: x[3], reverse=True)[:config.TOP_N_DEFENSE_DISPLAY]],
    61,
)

if SINGLE_GAME:
    print_table(
        f"SORTED MVPs (Top {config.TOP_N_DISPLAY} by ceiling - Backups Shown Only if Starter Injured)",
        f"{'Name':<35} {'Proj':>8} {'Ceiling':>8} {'Salary':>10} {'Base':>8} {'Value':>8} {'Games':>7} {'Opp':>5} {'Status':<20}",
        [f"{name:<35} {proj:>8.2f} {ceiling:>8.2f} ${cost:>9,.0f} {base_fppg:>8.2f} {(value * 1000):>7.1f}x {games_played:>7.0f} {opp:>5} {status:<20}"
         for name, proj, cost, value, pos, base_fppg, games_played, opp, status, ceiling in filtered_mvps[:config.TOP_N_DISPLAY]],
        118,
    )

print_sorted_proj_table("SORTED QBs", sorted(qbs, key=lambda x: x[4], reverse=True)[:config.TOP_N_DISPLAY])
for _pos in ('RB', 'WR', 'TE'):
    print_sorted_proj_table(f"SORTED {_pos}s", sorted((s for s in skills if s[1] == _pos), key=lambda x: x[4], reverse=True)[:config.TOP_N_DISPLAY])

role_risers = []
for p in players:
    if p.pos not in ('WR', 'TE', 'RB'):
        continue
    usage = role_trend_map.get((
        normalize_team_abbr(p.team), normalize_player_name(p.name)
    ))
    role_bonus = float(p.kv_store.get('role_trend_bonus') or 0)
    vacancy_bonus = float(p.kv_store.get('vacated_targets_bonus') or 0)
    workload_bonus = float(p.kv_store.get('vacated_workload_bonus') or 0)
    if role_bonus > 0 or vacancy_bonus > 0 or workload_bonus > 0:
        role_risers.append((p, usage or {}, role_bonus, vacancy_bonus, workload_bonus))
role_risers.sort(key=lambda item: (item[3] + item[2] + item[4]), reverse=True)
if role_risers:
    print_table(
        "ROLE / INJURY OPPORTUNITY SIGNALS",
        f"{'Player':<27} {'Pos':<3} {'Team':<4} {'Salary':>8} {'Prior':>8} {'Recent':>8} {'Targets+':>8} {'Workload+':>9} {'Role+':>7} {'Proj':>7} {'Value':>7}",
        [f"{p.name:<27} {p.pos:<3} {p.team:<4} ${p.cost:>7,} "
          f"{usage.get('earlier', 0) * 100:>7.1f}% {usage.get('recent', 0) * 100:>7.1f}% "
          f"{vacancy_bonus:>7.2f} {workload_bonus:>8.2f} {role_bonus:>6.2f} "
          f"{p.proj:>7.2f} {(p.proj / p.cost * 1000):>6.2f}x"
          for p, usage, role_bonus, vacancy_bonus, workload_bonus
          in role_risers[:max(config.TOP_N_DISPLAY, 15)]],
        121,
    )

role_reductions = [
    (p, returning_competition_map.get((
        normalize_team_abbr(p.team), p.pos, normalize_player_name(p.name)
    )))
    for p in players
    if p.pos in ('RB', 'WR', 'TE')
    and p.kv_store.get('returning_role_penalty')
]
if role_reductions:
    role_reductions.sort(
        key=lambda item: item[0].kv_store.get('returning_role_penalty', 0),
        reverse=True,
    )
    print_table(
        "RETURNING-TEAMMATE ROLE REDUCTIONS",
        f"{'Player':<27} {'Pos':<3} {'Team':<4} {'Returner':<25} {'Basis':<12} "
        f"{'Shared':>8} {'Absent':>8} {'Penalty':>8} {'Proj':>7} {'Ceiling':>8}",
        [f"{p.name:<27} {p.pos:<3} {p.team:<4} "
         f"{(details or {}).get('returning_teammate', ''):<25} "
         f"{'pre-return' if (details or {}).get('prospective') else 'post-return':<12} "
         f"{(details or {}).get('recent_share', 0) * 100:>7.1f}% "
         f"{(details or {}).get('earlier_share', 0) * 100:>7.1f}% "
         f"{p.kv_store.get('returning_role_penalty', 0):>7.2f} "
         f"{p.proj:>7.2f} {p.kv_store.get('ceiling', p.proj):>8.2f}"
         for p, details in role_reductions[:max(config.TOP_N_DISPLAY, 15)]],
        121,
    )

qb_adjusted_pass_catchers = [
    p for p in players if p.kv_store.get('qb_status_penalty')
]
if qb_adjusted_pass_catchers:
    qb_adjusted_pass_catchers.sort(
        key=lambda p: p.kv_store.get('qb_status_penalty', 0), reverse=True
    )
    print_table(
        "QB HEALTH PASS-CATCHER ADJUSTMENTS",
        f"{'Player':<27} {'Pos':<3} {'Team':<4} {'Primary QB':<22} "
        f"{'QB Drop':>8} {'Penalty':>8} {'Proj':>7} {'Ceiling':>8}",
        [f"{p.name:<27} {p.pos:<3} {p.team:<4} "
         f"{p.kv_store.get('qb_primary', ''):<22} "
         f"{p.kv_store.get('qb_status_projection_drop', 0):>7.2f} "
         f"{p.kv_store.get('qb_status_penalty', 0):>7.2f} "
         f"{p.proj:>7.2f} {p.kv_store.get('ceiling', p.proj):>8.2f}"
         for p in qb_adjusted_pass_catchers[:max(config.TOP_N_DISPLAY, 15)]],
        122,
    )

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
    manual_override = is_player_override(p.name, config.ALLOW_PLAYERS)
    # A missing baseline plus failed projection sources must not be mistaken
    # for a cheap usable player, especially when solver constraints tighten.
    if not _has_projection(p.proj):
        return True
    played = int(float(store.get('Played') or 0))
    if SINGLE_GAME:
        if played < MIN_PLAYED and not manual_override:
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
            if should_block_backup_qb(
                    base_name, is_backup, starter_injured,
                    config.ALLOW_PLAYERS):
                return True
        return False
    if p.team in BLOCKED_TEAMS and not manual_override:
        return True
    if p.pos == 'D' and p.cost > 5000 and not manual_override:
        return True
    if not manual_override and (
            (p.pos == 'QB' and p.cost < MIN_QB_SALARY)
            or p.cost > MAX_SALARY):
        return True
    cost_filter = (
        not manual_override
        and p.pos != 'QB'
        and (p.cost > MAX_SALARY or played < 1)
    )
    return (not manual_override) and (
        (p.proj < 0 and p.pos != 'D')
        or (p.proj < 10 and p.pos == 'QB')
        or cost_filter
    )


def build_optimizer_settings(players, block_function, forbid_same_team_wr_te=False,
                             relax_heuristics=False):
    """Custom rules: block players, and at most 1 per position per team.

    `relax_heuristics` drops the optional correlation/stacking heuristics while
    keeping eligibility, locks, and bans. It is used as a fallback when those
    heuristics make a requested set of locks infeasible.
    """
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
        if not relax_heuristics:
            custom_rules.append(
                CustomRule(
                    group_a=lambda p: base_position(p) == 'QB',
                    group_b=lambda p: False,
                    comparison=lambda sum, a, b: sum(a) <= 2,
                )
            )

        # Require a floor from each team. A six-man showdown roster can
        # otherwise be five or six players from one side. This is a user rule,
        # not a correlation preference, so it survives heuristic relaxation.
        min_per_team = max(int(config.SINGLE_GAME_MIN_PER_TEAM), 0)
        if min_per_team:
            for team in sorted({p.team for p in players}):
                available = len({
                    p.name.replace(' (MVP)', '')
                    for p in players
                    if p.team == team and not block_function(p)
                })
                required = min(min_per_team, available)
                if required > 0:
                    custom_rules.append(
                        min_players_per_team_rule(team, required)
                    )

        return OptimizerSettings(custom_rules=custom_rules, min_teams=2)

    # Non-single: at most 1 player per (position, team)
    if not relax_heuristics:
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

    small_slate_stacks = (
        SLATE_GAME_COUNT <= config.SMALL_SLATE_STACK_RULES_MAX_GAMES
    )
    if not small_slate_stacks and not relax_heuristics:
        # On larger slates, retain the anti-correlation heuristics. Short
        # slates benefit from allowing same-team stacks for tournament upside.
        for team in set(p.team for p in players if p.pos in ('QB', 'RB')):
            custom_rules.append(
                CustomRule(
                    group_a=lambda p, team=team: p.pos == 'QB' and p.team == team,
                    group_b=lambda p, team=team: p.pos == 'RB' and p.team == team,
                    comparison=lambda sum, a, b: sum(a) + sum(b) <= 1,
                )
            )

    if (not small_slate_stacks or forbid_same_team_wr_te) and not relax_heuristics:
        # Larger slates ban WR/TE stacks. On short slates this can also be
        # enabled for the candidate search that estimates the no-pair build.
        for team in set(p.team for p in players if p.pos in ('WR', 'TE')):
            custom_rules.append(
                CustomRule(
                    group_a=lambda p, team=team: p.pos == 'WR' and p.team == team,
                    group_b=lambda p, team=team: p.pos == 'TE' and p.team == team,
                    comparison=lambda sum, a, b: sum(a) + sum(b) <= 1,
                )
            )

    # Let projections and the lineup objective decide whether an opposing
    # skill player/defense pairing is worthwhile. Keep only the QB/DST
    # restriction; the comparison is conditional on selecting that defense.
    if not relax_heuristics:
        for defense in (p for p in players if p.pos == 'D'):
            custom_rules.append(
                forbid_opposing_positions_with_defense(
                    defense.team,
                    get_opponent(defense),
                    ('QB',),
                    config.ROSTER_SIZE_CLASSIC,
                )
            )

    return OptimizerSettings(
        custom_rules=custom_rules,
        min_teams=3,
        no_offense_against_defense=False,
    )


def _report_relaxed_heuristics():
    """Announce once that a heuristic was dropped to keep the lineup legal."""
    global RELAXED_HEURISTICS_WARNING_REPORTED
    if RELAXED_HEURISTICS_WARNING_REPORTED:
        return
    RELAXED_HEURISTICS_WARNING_REPORTED = True
    print(
        "WARNING: no legal lineup satisfied the optional stacking heuristics; "
        "relaxed them (same-team caps) to return a legal build."
    )


def optimize_lineup(players, score_by='proj', extra_locked=None, extra_banned=None,
                    forbid_same_team_wr_te=False):
    """Run draftfast's weighted optimizer and return the optimal roster.

    score_by='ceiling' temporarily optimizes on each player's tournament
    ceiling instead of the blended projection. That is the direct WTA search.
    """
    global STALE_LOCK_WARNING_REPORTED
    configured_locked, stale_locks = split_active_locks(
        config.LOCKED, (p.name for p in players)
    )
    if stale_locks and not STALE_LOCK_WARNING_REPORTED:
        print(f"WARNING: ignoring out-of-slate locks: {stale_locks}")
        STALE_LOCK_WARNING_REPORTED = True
    locked = list(dict.fromkeys(configured_locked + list(extra_locked or [])))
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
            optimizer_settings=build_optimizer_settings(
                players, block_function,
                forbid_same_team_wr_te=forbid_same_team_wr_te,
            ),
            constraints=active_constraints,
        )
        if roster is None and not forbid_same_team_wr_te:
            # Correlation/stacking heuristics are preferences, not platform
            # rules. If honoring them leaves no legal lineup for the requested
            # locks, retry with only the hard constraints so a legal build is
            # still produced (and say so). The no-pair variant is skipped: its
            # caller already has the normal build as a candidate.
            roster = run(
                rule_set=ACTIVE_RULE_SET,
                player_pool=players,
                verbose=False,
                optimizer_settings=build_optimizer_settings(
                    players, block_function,
                    forbid_same_team_wr_te=False,
                    relax_heuristics=True,
                ),
                constraints=active_constraints,
            )
            if roster is not None:
                _report_relaxed_heuristics()
    finally:
        for p in players:
            p.proj = original_proj[p.name]

    # draftfast deep-copies the pool when constraints exist, so restore the
    # returned roster's blended projections before scoring/displaying it.
    if roster and score_by == 'ceiling':
        for p in roster.players:
            p.proj = original_proj.get(p.name, p.proj)
    return roster


def same_team_wr_overlap_penalty(roster_players):
    """Apply WR target competition only when multiple teammates are rostered."""
    if config.SAME_TEAM_WR_OVERLAP_PENALTY <= 0:
        return 0.0
    wrs_by_team = defaultdict(list)
    for p in roster_players:
        if base_position(p) == 'WR':
            wrs_by_team[normalize_team_abbr(p.team)].append(p)
    return compute_same_team_wr_overlap_penalty(
        {team: len(team_wrs) for team, team_wrs in wrs_by_team.items()},
        config.SAME_TEAM_WR_OVERLAP_PENALTY,
    )


def same_team_wr_te_overlap_penalty(roster_players):
    """Bounded target-share adjustment for short-slate WR/TE lineup pairs."""
    if (SINGLE_GAME
            or SLATE_GAME_COUNT > config.SMALL_SLATE_STACK_RULES_MAX_GAMES
            or config.SAME_TEAM_WR_TE_TARGET_SHARE_WEIGHT <= 0
            or config.MAX_SAME_TEAM_WR_TE_OVERLAP_PENALTY <= 0):
        return 0.0
    wrs_by_team = defaultdict(list)
    tes_by_team = defaultdict(list)
    for p in roster_players:
        if p.pos == 'WR':
            wrs_by_team[normalize_team_abbr(p.team)].append(p)
        elif p.pos == 'TE':
            tes_by_team[normalize_team_abbr(p.team)].append(p)
    penalty = 0.0
    for team in wrs_by_team.keys() & tes_by_team.keys():
        for wr in wrs_by_team[team]:
            wr_share = recent_target_share_map.get((
                team, normalize_player_name(wr.name)
            ))
            for te in tes_by_team[team]:
                te_share = recent_target_share_map.get((
                    team, normalize_player_name(te.name)
                ))
                penalty += compute_same_team_wr_te_overlap_penalty(
                    wr_share,
                    te_share,
                    config.SAME_TEAM_WR_TE_TARGET_SHARE_WEIGHT,
                    config.MAX_SAME_TEAM_WR_TE_OVERLAP_PENALTY,
                )
    return penalty


def get_score(roster):
    return (sum(p.proj for p in roster.players)
            - same_team_wr_overlap_penalty(roster.players)
            - same_team_wr_te_overlap_penalty(roster.players))


def get_wta_score(roster):
    """Showdown selection score: sum each player's best available ceiling."""
    return (sum(p.kv_store.get('ceiling') or p.proj for p in roster.players)
            - same_team_wr_overlap_penalty(roster.players)
            - same_team_wr_te_overlap_penalty(roster.players))


def _roster_key(roster):
    return tuple(sorted(p.name for p in roster.players))


def optimize_short_slate_classic_lineup(players, extra_locked=None, extra_banned=None):
    """Compare normal, no-pair, and forced-pair builds with overlap scoring."""
    candidates = []
    seen = set()

    def add(roster):
        if not roster:
            return
        key = _roster_key(roster)
        if key not in seen:
            seen.add(key)
            candidates.append(roster)

    add(optimize_lineup(
        players, extra_locked=extra_locked, extra_banned=extra_banned
    ))
    # Include the best build that avoids all same-team WR/TE pairs.
    add(optimize_lineup(
        players,
        extra_locked=extra_locked,
        extra_banned=extra_banned,
        forbid_same_team_wr_te=True,
    ))

    active_names = {p.name for p in players}
    active_config_locks, _ = split_active_locks(config.LOCKED, active_names)
    locked_names = set(active_config_locks) | (
        set(extra_locked or []) & active_names
    )
    extra_banned_names = set(extra_banned or [])
    banned_names = set(BANNED) | extra_banned_names
    teams = defaultdict(lambda: {'WR': [], 'TE': []})
    for p in players:
        if (p.name in banned_names or block_function(p)
                or p.pos not in ('WR', 'TE')):
            continue
        teams[normalize_team_abbr(p.team)][p.pos].append(p)

    # Force each legal same-team pair once so the solver explores those
    # combinations; the final selector then applies the target-share penalty.
    for group in teams.values():
        for wr, te in itertools.product(group['WR'], group['TE']):
            pair = [wr.name, te.name]
            if any(
                name in banned_names and name not in locked_names
                for name in pair
            ):
                continue
            add(optimize_lineup(
                players,
                extra_locked=list(locked_names | set(pair)),
                extra_banned=extra_banned,
            ))

    return max(candidates, key=get_score) if candidates else None


class _EnumeratedRoster:
    """Minimal roster wrapper for lineups built by the exact single-game search."""
    __slots__ = ('players',)

    def __init__(self, players):
        self.players = players


def enumerate_single_game_lineups(players, limit=None):
    """Exhaustively enumerate legal single-game rosters, ranked by ceiling.

    The showdown pool is small (two teams), so an exact search is practical and
    guarantees we do not miss a higher-scoring construction that the local
    optimizer/diversification loop would skip.

    """
    def base_name(name):
        return name.replace(' (MVP)', '')

    active_names = {p.name for p in players}
    stale_locks = [name for name in config.LOCKED if name not in active_names]
    if stale_locks:
        print(f"WARNING: ignoring out-of-slate showdown locks: {stale_locks}")
    locked = list(dict.fromkeys(name for name in config.LOCKED if name in active_names))
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

    # Two-team showdown: enforce a floor from each side (a six-man roster can
    # otherwise be 5-1 or 6-0). Cap the floor by what each team can supply.
    min_per_team = max(int(config.SINGLE_GAME_MIN_PER_TEAM), 0)
    required_per_team = {}
    if min_per_team:
        available_by_team = defaultdict(set)
        for p in regular_pool + locked_regulars + mvp_pool:
            available_by_team[p.team].add(base_name(p.name))
        required_per_team = {
            team: min(min_per_team, len(names))
            for team, names in available_by_team.items()
        }

    def search(max_qbs=2):
        """Enumerate legal rosters, optionally enforcing the soft heuristics."""
        found = []
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
            if max_qbs is not None and qb_count > max_qbs:
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
                if (max_qbs is not None
                        and qb_count + (1 if mvp_is_qb else 0) > max_qbs):
                    continue

                roster_players = parts + [m]
                final_counts = dict(team_counts)
                final_counts[m.team] = final_counts.get(m.team, 0) + 1
                if any(final_counts.get(team, 0) < required
                       for team, required in required_per_team.items()):
                    continue
                overlap_penalty = same_team_wr_overlap_penalty(roster_players)
                wta = sum(p.kv_store.get('ceiling') or p.proj for p in roster_players) - overlap_penalty
                proj = sum(p.proj for p in roster_players) - overlap_penalty
                found.append((wta, proj, salary, roster_players))
        return found

    results = search()
    if not results:
        # Drop the two-QB preference so an unusual lock set still resolves.
        results = search(max_qbs=None)
        if results:
            _report_relaxed_heuristics()

    # Winner-take-all showdown objective: maximize aggregate player ceiling.
    # Weighted projection is retained as a tie-breaker and reporting diagnostic.
    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
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


def select_ceiling_lineups(rosters, count):
    """Return the legal showdown entries with the highest aggregate ceiling."""
    return sorted(
        rosters,
        key=lambda roster: (get_wta_score(roster), get_score(roster)),
        reverse=True,
    )[:count]


def generate_lineups(players, target_count):
    """Explore candidate builds and select showdown lineups by aggregate ceiling."""
    # The two-team showdown pool is small enough for an exact search. That
    # guarantees we see every legal construction, including sneaky-play builds
    # that a local rotation would otherwise miss, before ceiling ranking.
    regular_count = sum(1 for p in players if p.pos != 'MVP')
    mvp_count = sum(1 for p in players if p.pos == 'MVP')
    if regular_count <= 30 and mvp_count <= 30:
        enumerated = enumerate_single_game_lineups(players, limit=None)
        if enumerated:
            return select_ceiling_lineups(enumerated, target_count)

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
        base_roster = max(candidates, key=get_wta_score)
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

    return select_ceiling_lineups(candidates, target_count)


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
    objective = ('aggregate player ceiling (weighted proj is diagnostic)'
                 if SINGLE_GAME
                 else 'aggregate weighted projection (ceiling is diagnostic)')
    print(f"lineup selection objective = {objective}")
    print(f"weighted proj = {1 - upside_weight:.2f} * adjusted + {upside_weight:.2f} * ceiling "
          f"(upside applied only when ceiling > adjusted; upside weight = {upside_weight:.2f})")
    wr_overlap_penalty = same_team_wr_overlap_penalty(roster.players)
    if wr_overlap_penalty > 0:
        print(f"same-team WR overlap adjustment = -{wr_overlap_penalty:.2f} on lineup totals")
    wr_te_penalty = same_team_wr_te_overlap_penalty(roster.players)
    if wr_te_penalty > 0:
        print(
            "same-team WR/TE target-competition adjustment = "
            f"-{wr_te_penalty:.2f} on lineup totals"
        )
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

def record_optimizer_source_coverage():
    """Report source coverage for players that the optimizer can actually use."""
    locked_names = set(config.LOCKED)
    banned_names = set(BANNED) - locked_names
    eligible = [
        p for p in players
        if p.pos != 'MVP'
        and p.name not in banned_names
        and not block_function(p)
    ]
    pool_count = sum(p.pos != 'MVP' for p in players)
    offensive_players = [p for p in eligible if p.pos in ('QB', 'RB', 'WR', 'TE', 'K')]
    history_players = offensive_players

    history_matched = sum(
        _has_projection(historic_averages.get(history_key_for(p)))
        for p in history_players
    )
    sleeper_matched = sum(_has_projection(sleeper_projection_for(p)) for p in eligible)
    fp_matched = sum(_has_projection(fp_projection_for(p)) for p in eligible)
    fppg_matched = sum(_has_projection(p.kv_store.get('FPPG')) for p in eligible)
    source_inputs = [
        (
            _has_projection(historic_averages.get(history_key_for(p))),
            _has_projection(sleeper_projection_for(p)),
            _has_projection(fp_projection_for(p)),
            _has_projection(p.kv_store.get('FPPG')),
        )
        for p in eligible
    ]
    salary_history_only = sum(
        not has_sleeper and not has_fp and (has_history or has_fppg)
        for has_history, has_sleeper, has_fp, has_fppg in source_inputs
    )
    no_positive_sources = sum(not any(inputs) for inputs in source_inputs)

    _record_source(
        'FanDuel FPPG', True,
        f"salary file loaded; positive prior FPPG for {fppg_matched}/{len(eligible)} optimizer-eligible ({pool_count} player pool)",
    )
    _record_source(
        'History (nflverse)', history_matched == len(history_players),
        f"{len(historic_averages)} entries; matched {history_matched}/{len(history_players)} eligible skill players",
    )
    _record_source(
        'Sleeper', sleeper_matched == len(eligible),
        f"{len(sleeper_proj_map)} projections; matched {sleeper_matched}/{len(eligible)} eligible ({pool_count} player pool)",
    )
    fp_detail = fp_source_status.get('detail', 'source unavailable')
    if 'returned HTTP 200 without tables' in fp_detail:
        fp_detail = 'HTML returned no projection table for any position; prior-week cache not used'
    _record_source(
        'FantasyPros',
        bool(fp_proj_map) and fp_matched == len(eligible) and fp_source_status.get('status') == 'ok',
        f"{len(fp_proj_map)} current-week projections; matched {fp_matched}/{len(eligible)} eligible; method={fp_source_status.get('method', 'unknown')}; {fp_detail}",
    )
    _record_source(
        'Projection fallback', no_positive_sources == 0,
        f"{salary_history_only} eligible rely on salary/history only; {no_positive_sources} have no positive inputs",
    )

    upside_matched = sum(
        _has_projection(upside_map.get(normalize_player_name(p.name)))
        for p in offensive_players
    )
    _record_source(
        'Upside/ceiling', upside_matched == len(offensive_players),
        f"{len(upside_map)} history entries; matched {upside_matched}/{len(offensive_players)} eligible offensive players",
    )
    defenses = [p for p in eligible if p.pos == 'D']
    dst_ceiling_matched = sum(
        normalize_team_abbr(p.team).lower() in defense_upside_map for p in defenses
    )
    _record_source(
        'DST ceiling', dst_ceiling_matched == len(defenses),
        f"big-play-tail surplus; matched {dst_ceiling_matched}/{len(defenses)} eligible defenses; points-allowed variance excluded",
    )

    sleeper_missing = [p for p in eligible if not _has_projection(sleeper_projection_for(p))]
    fp_missing = [p for p in eligible if not _has_projection(fp_projection_for(p))]
    if sleeper_missing:
        print(f"WARNING: Sleeper missing {len(sleeper_missing)}/{len(eligible)} optimizer-eligible players: {_player_labels(sleeper_missing)}")
    if fp_missing and not fp_proj_map:
        print(
            f"WARNING: FantasyPros excluded for this run "
            f"({fp_source_status.get('status')}); all players blend FanDuel FPPG, "
            "nflverse history, and Sleeper"
        )
    elif fp_missing:
        print(
            f"WARNING: FantasyPros covers {len(eligible) - len(fp_missing)}/"
            f"{len(eligible)} optimizer-eligible players; using the partial source"
        )
    if upside_matched < len(offensive_players):
        print(f"WARNING: historical ceiling unavailable for {len(offensive_players) - upside_matched}/{len(offensive_players)} optimizer-eligible offensive players")


record_optimizer_source_coverage()
print_data_status()

# ============================================================
# Lineup generation: showdown entries ranked by aggregate ceiling.
# Set NUM_LINEUPS=<n> to generate alternatives; locks are preserved in all.
# ============================================================

NUM_LINEUPS = int(os.environ.get('NUM_LINEUPS') or (config.DEFAULT_SINGLE_LINEUPS if SINGLE_GAME else 0))
SHORT_CLASSIC_SLATE = (
    not SINGLE_GAME
    and SLATE_GAME_COUNT <= config.SMALL_SLATE_STACK_RULES_MAX_GAMES
)

if SINGLE_GAME:
    # Exact search first: enumerate every legal showdown roster when the pool is
    # small, then fall back to solver/ceiling/diversification builds.
    rosters = generate_lineups(players, max(NUM_LINEUPS, 1))
else:
    roster = (
        optimize_short_slate_classic_lineup(players)
        if SHORT_CLASSIC_SLATE
        else optimize_lineup(players)
    )
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
            r = (
                optimize_short_slate_classic_lineup(
                    players, extra_banned=extra_banned
                )
                if SHORT_CLASSIC_SLATE
                else optimize_lineup(players, extra_banned=extra_banned)
            )
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
            selection_metric = 'ceiling' if SINGLE_GAME else 'weighted proj'
            print_banner(f"TOP {len(rosters)} DIVERSIFIED LINEUPS (ranked by {selection_metric})", 100)
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
    slots = upload_slots(SINGLE_GAME)
    headers = [name for name, _ in slots]
    player_names = []
    roster_copy = roster.players.copy()
    for slot_name, accepted in slots:
        for player in roster_copy:
            if player.pos in accepted:
                # FanDuel identifies the MVP by column, not by a name suffix.
                display_name = player.name.replace(' (MVP)', '')
                player_names.append(f"{player.kv_store['Id']}:{display_name}")
                roster_copy.remove(player)
                break

    if len(player_names) != len(headers) or roster_copy:
        print(
            f"WARNING: upload CSV incomplete ({len(player_names)}/{len(headers)} "
            f"slots filled); unplaced: {[p.name for p in roster_copy]}"
        )

    with open(UPLOAD_FILE, 'w') as f:
        f.write(','.join(headers))
        f.write('\n')
        f.write(','.join(player_names))

print('done')
