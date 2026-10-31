# Central tuning config for the FanDuel lineup optimizer.
# Edit weekly tuning here instead of inside draft.py.

# ============================================================
# Paths
# ============================================================

import os
import time

DATA_FOLDER = './data26'
ACTIVE_FOLDER = './active'
UPLOAD_FOLDER = './upload'
CACHE_FOLDER = './ranking'
CACHE_MAX_AGE_DAYS = 3
FANTASYPROS_CACHE_MAX_AGE_HOURS = 6
FANTASYPROS_FAILURE_CACHE_HOURS = 2


def cache_is_fresh(path):
    """Return whether a cache exists and is younger than the configured TTL."""
    if not os.path.isfile(path):
        return False
    return time.time() - os.path.getmtime(path) < CACHE_MAX_AGE_DAYS * 24 * 60 * 60

# ============================================================
# Season
# ============================================================

SEASON_START = '09/13/2026'

# ============================================================
# Roster / salary
# ============================================================

MAX_SALARY = 9900
SALARY_MAX = 60000
SALARY_MIN_OFFSET = 100
SALARY_MIN_OFFSET_SINGLE = 900
MIN_SALARY_CLASSIC = 5000
MIN_SALARY_SINGLE = 2000
MIN_QB_SALARY_CLASSIC = 6800
MIN_QB_SALARY_SINGLE = 1000
ROSTER_SIZE_CLASSIC = 9
ROSTER_SIZE_SINGLE = 6

# Number of rows shown in the sorted defenses / QBs / MVPs printouts.
TOP_N_DISPLAY = 10
# Show a few more DSTs so low-cost ceiling plays aren't hidden just below top 10.
TOP_N_DEFENSE_DISPLAY = 15

# ============================================================
# Projection weights
# ============================================================

WEIGHTED = True
INJURY_FACTOR = .12
# Partial credit for Questionable players (they often play, but a banged-up
# starter still creates teammate opportunity). Out/IR get 1.0.
INJURY_QUESTIONABLE_WEIGHT = 0.5
# Doubtful players receive more replacement credit than Questionable players,
# but less than a confirmed Out/IR player.
INJURY_DOUBTFUL_WEIGHT = 0.7
# Minimum recent production for an injured player to create teammate workload.
MIN_INJURY_FPPG = 6.0
# A CONFIRMED Out/IR starter funds a "next man up" pool worth this fraction of
# their production for the team's remaining players at the SAME position (the
# replacement inherits the vacated role). Capped per injured player so one
# injury can't swing a projection unboundedly.
INJURY_OUT_TRANSFER = 0.30
INJURY_POOL_CAP = 2.0
# Multiplier on a skill-position (RB/WR/TE) vacated-production pool.
INJURY_SKILL_MULTIPLIER = 1.2
# Injury workload credit scales modestly with the active QB's blended projection
# relative to the slate average. Bounds keep QB quality from dominating.
INJURY_QB_QUALITY_WEIGHT = 0.5
INJURY_QB_QUALITY_MIN = 0.85
INJURY_QB_QUALITY_MAX = 1.15
# History/ceiling smoothing: recent games decay exponentially in history.
HISTORY_RECENCY_DECAY = 0.8
# Recent role breakouts can raise a player's GPP ceiling above the full-season
# percentile, but only with enough games and a material target-share increase.
UPSIDE_RECENT_GAMES = 4
GPP_UPSIDE_PERCENTILE = 0.85
ROLE_TREND_MIN_TARGET_SHARE_DELTA = 0.03
ROLE_TREND_POINT_WEIGHT = 6.0
ROLE_TREND_MAX_BONUS = 1.0
# Classic GPPs are treated as top-score contests: give expected points and an
# 85th-percentile ceiling equal influence. Validate/tune this by backtesting.
GPP_UPSIDE_WEIGHT = 0.50
# Single-game contests reward ceiling outcomes, so the optimizer blends the
# matchup-adjusted projection toward a player's historical ceiling.
SHOWDOWN_UPSIDE_WEIGHT = 0.6
SHOWDOWN_MAX_SCORE = 38.0
# Single-game defaults to printing several diversified GPP entries. Override
# with NUM_LINEUPS=<n> (or NUM_LINEUPS=1 to get only the single lineup).
DEFAULT_SINGLE_LINEUPS = 6
# Sneaky-upside detection: flags cheap players whose observed ceiling vaults
# well above their current projection (low-rostered boom candidates).
SHOWDOWN_SNEAKY_MAX_COST = 6000
SHOWDOWN_SNEAKY_MIN_UPSIDE = 1.25     # ceiling must be >= this multiple of proj
SHOWDOWN_SNEAKY_TOP = 8
# Number of sneaky-upside players to use as forced lineup seeds. Each seed is
# optimized separately, then ranked by weighted proj against the other builds.
SHOWDOWN_SNEAKY_LINEUP_SEEDS = 3
# Soft penalty applied to the second and later WRs from the same team in a
# single-game slate. This discourages target cannibalization without banning
# QB/WR or game-stack correlation.
SINGLE_GAME_SAME_TEAM_WR_PENALTY = 2.0
# The same-team penalty needs to know which WR is the true WR1. By default the
# model uses salary (then projection) as the role proxy; a small-sample
# projection can otherwise misrank a team's top receiver. Override per team
# when role/usage clearly differs from the salary/projection ordering.
SINGLE_GAME_WR1_OVERRIDES = {
}
# Bonus when a team's starting QB is out. Full value to defenses/MVPs facing
# the injured QB (backup-QB matchups are a strong DST edge); half to skill
# players (game-script benefit); RBs get full when their OWN QB is out.
INJURED_QB_BONUS = 2.5
# WR-injury pool is based on the injured players' typical production and
# distributed across active pass catchers by recent target share.
WR_INHERITANCE_WEIGHT = 1.0
# Cap on the per-team WR-vacancy pool so multiple absences cannot stack without bound.
WR_INHERITANCE_CAP = 3.0
# Multiplier on the opponent's net injury weakness (abs of excluded_bonus)
# for defenses/MVPs: a weakened opponent offense helps the D.
OPPONENT_INJURY_WEIGHT = 0.4
MIN_SCORE = 7
MAX_SCORE = 29
MAX_DEF_MULTIPLIER = 2.0
MIN_PROJ_MULTIPLIER = 0.5
# Ceiling on how far a projection can rise above its own base (prevents a
# low-base depth player from being boosted to absurd heights by a flat bonus).
MAX_PROJ_MULTIPLIER = 2.0
LOW_SALARY_SKIP = 4200
MAX_WEATHER_BONUS = 0.40

# Team-total (Vegas implied) projection weights.
# A player's base projection is scaled by how far their team's implied
# total deviates from the slate average. Defenses use the OPPONENT's
# total (a defense facing a weak offense scores better).
OFFENSE_TOTAL_WEIGHT = 0.45      # proj points per point of team-total deviation
DEFENSE_TOTAL_WEIGHT = 0.60      # proj points per point of opponent-total deviation
# Weight on the opposing offense's giveaway/sack propensity (in DST-point
# units) relative to the league average.
DST_GIVEAWAY_WEIGHT = 1.0
# Shrinkage games for the opponent-giveaway rate: teams are regressed toward
# the league mean by this many games, damping small-sample overreaction.
GIVEAWAY_SHRINKAGE_GAMES = 4.0

# Fantasy points allowed (FPA) weights
FPA_WEIGHT = 0.25                # boost vs. defenses allowing more points

# ============================================================
# Projection source weights
# ============================================================

# FanDuel FPPG and nflverse history are both backward-looking; keep their
# combined influence below the two independent weekly projection sources.
# Missing/non-positive sources are excluded and the remaining weights are
# renormalized before blending.
PROJECTION_WEIGHTS = {
    'fd_fppg': 0.20,
    'nflverse_history': 0.10,
    'sleeper': 0.35,
    'fantasypros': 0.35,
}
# QB offense-snap share below which a QB is treated as a backup
BACKUP_SNAP_THRESHOLD = 0.5
USE_SNAP_BACKUP = True
# Teams playing in domes / retractable-roof stadiums (weather-immune)
ROOFED_TEAMS = {'ARI', 'ATL', 'DAL', 'DET', 'HOU', 'IND', 'LAR', 'LV', 'MIN', 'NO'}

# ============================================================
# Weekly tuning
# ============================================================

# Players to force into / out of the pool (weekly tuned). Single-game slates
# include '(MVP)' variants for duplicated players.
READD = [

]
BANNED_CLASSIC = [
    # 'Cincinnati Bengals', 'Travis Kelce', 'Jameson Williams', 'Kyren Williams',
    # 'Josh Allen', 'Zay Flowers', 'CeeDee Lamb',  'Keon Coleman', 'Chase Brown', 'Ryan Flournoy', 'Mike Gesicki', 'Hunter Henry', 'Bucky Irving'
    # 'Cincinnati Bengals' 'Jaxon Smith-Njigba', 'Cincinnati Bengals', 'DeMario Douglas'
    # 'Javonte Williams', 'Kyren Williams', 'Zay Flowers', "D'Andre Swift", "Josh Allen", 'T.J. Hockenson', 'Mike Gesicki'
    # 'Aaron Jones Sr.', 'Jameson Williams', 'Jordan Mason',  'Mike Gesicki', 'Bijan Robinson', 'Zay Flowers', 'Matthew Stafford',  'Derrick Henry', 'Kirk Cousins', 'Omar Cooper Jr.', 'Darius Cooper', 'T.J. Hockenson',
    #  'Ryan Flournoy', 'Travis Kelce', 'Tyler Huntley', 'Jaylen Waddle', 'Joe Burrow',   'George Pickens', 'Dak Prescott', 'Garrett Wilson', 'George Kittle',
    #    'Nico Collins', 'Sam LaPorta', 'Jaylin Noel', 'Tre Tucker', 'Patrick Mahomes', 'Tyler Shough', "Ja'Marr Chase", 'Aaron Rodgers', 'Bryce Young',
    #     'Chris Olave',  'Jaxon Smith-Njigba', 'DeVonta Smith', 'Minnesota Vikings', 'Geno Smith', 'Aaron Jones Sr.', 'CeeDee Lamb', 'George Kittle', 'Chuba Hubbard'
]
BANNED_SINGLE = ['Malik Washington (MVP)', 'Rhamondre Stevenson',  'Rhamondre Stevenson (MVP)', 'Malik Willis (MVP)', 'Eli Raridon', 'Washington Commanders (MVP)'
]

LOCKED = [
    # 'Trevor Lawrence',  'Bijan Robinson', 'Kenneth Walker III', 'Jahmyr Gibbs','Detroit Lions', "Germie Bernard"
]
BLOCKED_TEAMS = []
