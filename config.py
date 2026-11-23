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
WEATHER_CACHE_MAX_AGE_MINUTES = 30
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

MAX_SALARY = 12900
SALARY_MAX = 60000
SALARY_MIN_OFFSET = 100
SALARY_MIN_OFFSET_SINGLE = 300
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
# A player's recent role can create replacement value even when a small sample
# or inefficient scoring keeps FPPG below MIN_INJURY_FPPG.
INJURY_ROLE_MIN_OPPORTUNITIES = {'RB': 2.0, 'WR': 1.0, 'TE': 1.0}
INJURY_ROLE_MIN_SNAP_SHARE = {'RB': 0.12, 'WR': 0.20, 'TE': 0.20}
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
# Small receiver/TE adjustment for an uncertain primary QB. When injury details
# exist without a reliable status code, treat that as lower-probability risk;
# scale/cap the penalty because consensus projections may already price it in.
QB_UNSPECIFIED_INJURY_WEIGHT = 0.25
QB_PASS_CATCHER_SENSITIVITY = 0.15
QB_PASS_CATCHER_PROJECTION_SCALE = 15.0
QB_PASS_CATCHER_MAX_PENALTY = 1.25
QB_PASS_CATCHER_CEILING_WEIGHT = 0.75
# History/ceiling smoothing: recent games decay exponentially in history.
HISTORY_RECENCY_DECAY = 0.8
# Full-slate form signal: compare a player's last few appearances' median
# FanDuel score with their season median, then shrink it toward zero.
RECENT_FORM_GAMES = 4
RECENT_FORM_MIN_GAMES = 3
RECENT_FORM_MIN_PRIOR_GAMES = 2
RECENT_FORM_PRIOR_GAMES = 2.0
RECENT_FORM_WEIGHT = 0.35
MAX_RECENT_FORM_ADJUSTMENT = 1.5
# Blend recent appearance ceiling into the season ceiling, shrinking the
# recent tail estimate as if it had two prior games of sample-size support.
RECENT_CEILING_WEIGHT = 0.50
RECENT_CEILING_PRIOR_GAMES = 2.0
# Recent role breakouts can raise a player's GPP ceiling above the full-season
# percentile, but only with enough games and a material target-share increase.
UPSIDE_RECENT_GAMES = 4
GPP_UPSIDE_PERCENTILE = 0.85
ROLE_TREND_MIN_TARGET_SHARE_DELTA = 0.03
ROLE_TREND_POINT_WEIGHT = 6.0
ROLE_TREND_MAX_BONUS = 1.0
# Injury/workload point adjustments also lift the high-percentile ceiling, but
# are capped so a role signal cannot overwhelm the empirical scoring history.
ROLE_CEILING_BONUS_WEIGHT = 1.0
MAX_ROLE_CEILING_BONUS = 2.0
# Classic full-slate winner-take-all contests favor right-tail outcomes. Keep
# expected points in the mix, but lean more heavily on the 85th-percentile
# ceiling; validate/tune this value with out-of-sample backtests.
GPP_UPSIDE_WEIGHT = 0.65
# Single-game projections retain an upside blend for diagnostics; the lineup
# selector ranks legal builds by their role-adjusted aggregate ceiling.
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
# Number of sneaky-upside players to use as forced lineup seeds when exact
# enumeration is unavailable; candidate lineups are ranked by ceiling.
SHOWDOWN_SNEAKY_LINEUP_SEEDS = 3
# Lineup-level penalty for additional WRs from the same team. It is applied
# only when multiple teammates are actually rostered, not to a player's
# standalone projection. Legacy names remain supported below.
SINGLE_GAME_SAME_TEAM_WR_PENALTY = 2.0
# Legacy per-team WR1 override retained for existing config files. The current
# lineup-level overlap adjustment no longer needs to label one receiver WR1.
SINGLE_GAME_WR1_OVERRIDES = {
}
# General names for the roster-level adjustment; initialized from the legacy
# settings so existing weekly configuration continues to work.
SAME_TEAM_WR_OVERLAP_PENALTY = SINGLE_GAME_SAME_TEAM_WR_PENALTY
# On short full slates, allow an opposing WR and defense together; retain the
# normal QB/RB/TE versus opposing-defense restriction.
ALLOW_OPPOSING_WR_DST_STACK_MAX_GAMES = 4
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
# Kickers benefit from scoring chances but not one-for-one with team points;
# use a smaller, bounded implied-total adjustment.
KICKER_TOTAL_WEIGHT = 0.10
MAX_KICKER_TOTAL_BONUS = 0.75
# Weight on the opposing offense's giveaway/sack propensity (in DST-point
# units) relative to the league average.
DST_GIVEAWAY_WEIGHT = 1.0
# Shrinkage games for the opponent-giveaway rate: teams are regressed toward
# the league mean by this many games, damping small-sample overreaction.
GIVEAWAY_SHRINKAGE_GAMES = 4.0

# Fantasy points allowed (FPA) weights
FPA_WEIGHT = 0.25                # boost vs. defenses allowing more points

# Small, one-time penalty when a previously absent teammate returns and the
# player's recent share falls. Require a meaningful usage share/change and cap
# the adjustment so one game or imperfect snap data cannot dominate a forecast.
RETURNING_ROLE_MIN_SHARE = 0.15
RETURNING_ROLE_MIN_SHARE_DROP = 0.12
RETURNING_ROLE_POINTS_PER_SHARE = 5.0
RETURNING_ROLE_MAX_PENALTY = 2.5
RETURNING_ROLE_CEILING_WEIGHT = 1.0
RETURNING_ROLE_MIN_ABSENCE_WEEKS = 3
RETURNING_ROLE_HISTORY_GAMES = 4

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
     'Kyle Monangai', 'Josh Allen', 'Ty Johnson', "James Cook III"
]
BANNED_SINGLE = [
    'Gavin Bartholomew', 'Jordan Watkins', 'Christian Watson (MVP)', 'Colby Parkinson', 'Matthew Golden (MVP)', 'Matthew Stafford (MVP)',
    'Pierre Strong Jr.', 'Colby Parkinson (MVP)', 'Xavier Smith', 'Green Bay Packers (MVP)',
    # 'Gavin Bartholomew',  'Christian McCaffrey (MVP)', 'Jordan Watkins', 'Kaelon Black', 'Mike Evans', 'T.J. Hockenson (MVP)', 'Kirk Cousins (MVP)',
    # 'Zay Flowers (MVP)', 'Jacoby Brissett (MVP)', 'Tank Dell', 'Foster Moreau'
    # 'Malachi Fields', 'Devin Singletary', 'Stefon Diggs', 'New York Giants (MVP)', 'Joe Burrow',
    # 'Sean Tucker', 'Roschon Johnson', 'Tez Johnson', 'Jameis Winston', 'Jameis Winston (MVP)', 'Isaiah Likely (MVP)', 'Malik Nabers (MVP)'
    # 'Stefon Diggs', 'Stefon Diggs (MVP)', 'Zay Flowers', 'Zay Flowers (MVP)'
    # 'Jayden Daniels (MVP)',

    # #   'Saquon Barkley (MVP)'
#    'Rhamondre Stevenson',  'Jayden Daniels (MVP)'
    # 'Eli Raridon'
    # 'Malik Washington (MVP)', 'Hunter Henry', 'Rhamondre Stevenson',  'Rhamondre Stevenson (MVP)', 'Malik Willis (MVP)', 'Eli Raridon', 'Washington Commanders (MVP)'
]

LOCKED = [
    'DeVonta Smith', 'Emmett Johnson', 'Dallas Cowboys'
    # 'Kaleb Johnson'
    # 'Jordan Love (MVP)', 'Konata Mumpfield', 'Blake Corum'
    #   'Kirk Cousins', 'Justin Jefferson'
    # 'Los Angeles Chargers', 'Jonah Coleman'
    # 'C.J. Stroud (MVP)', 'Xavier Hutchinson', 'David Montgomery'
    # 'Carolina Panthers', 'Emmett Johnson'
    # 'Tyler Shough',
    # 'Indianapolis Colts'
    # 'Parker Washington'
    # 'Jaylen Warren', 'Tony Pollard',' Chuba Hubbard',
    # 'Drake Maye'
    # 'Caleb Williams (MVP)', 'Cam Skattebo', 'Rachaad White'
    # 'Tee Higgins',
    # 'Jared Goff',
    # 'New Orleans Saints',
        # 'Christian McCaffrey',
    # 'Isaiah Likely',
    #   'Luther Burden III'
    # 'Joe Burrow'#, 'Bijan Robinson', 'Kenneth Walker III', #, "Germie Bernard", "Makai Lemon", "Wan'Dale Robinson"
]

BLOCKED_TEAMS = []
