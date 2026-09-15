# Central tuning config for the FanDuel lineup optimizer.
# Edit weekly tuning here instead of inside draft.py.

# ============================================================
# Paths
# ============================================================

DATA_FOLDER = './data26'
ACTIVE_FOLDER = './active'
UPLOAD_FOLDER = './upload'
CACHE_FOLDER = './ranking'

# ============================================================
# Season
# ============================================================

SEASON_START = '09/13/2026'

# ============================================================
# Roster / salary
# ============================================================

MAX_SALARY = 9900
SALARY_MAX = 60000
SALARY_MIN_OFFSET = 100          # 200 for single-game slates
MIN_SALARY_CLASSIC = 4900
MIN_SALARY_SINGLE = 2100
MIN_QB_SALARY_CLASSIC = 6400
MIN_QB_SALARY_SINGLE = 1000
ROSTER_SIZE_CLASSIC = 9
ROSTER_SIZE_SINGLE = 6
MAX_PLAYERS_PER_TEAM_CLASSIC = 9

# ============================================================
# Projection weights
# ============================================================

WEIGHTED = True
AVERAGE_WEIGHT = .5
INJURY_FACTOR = .12
INJURED_QB_BONUS = 1.25
MIN_SCORE = 7
MAX_SCORE = 27
MAX_DEF_MULTIPLIER = 2.0
MIN_PROJ_MULTIPLIER = 0.5
LOW_SALARY_SKIP = 4200
MAX_WEATHER_BONUS = 0.20

# Team-total (Vegas implied) projection weights.
# A player's base projection is scaled by how far their team's implied
# total deviates from the slate average. Defenses use the OPPONENT's
# total (a defense facing a weak offense scores better).
OFFENSE_TOTAL_WEIGHT = 0.45      # proj points per point of team-total deviation
DEFENSE_TOTAL_WEIGHT = 0.60      # proj points per point of opponent-total deviation

# Fantasy points allowed (FPA) weights
FPA_WEIGHT = 0.25                # boost vs. defenses allowing more points

# ============================================================
# Free data-source blends (nflverse + Sleeper)
# ============================================================

# Weight on Sleeper's projection vs. the FanDuel/history base
SLEEPER_WEIGHT = 0.3
# Weight on FantasyPros' weekly projection vs. the base
FANTASYPROS_WEIGHT = 0.25
# QB offense-snap share below which a QB is treated as a backup
BACKUP_SNAP_THRESHOLD = 0.5
USE_SNAP_BACKUP = True
# Teams playing in domes / retractable-roof stadiums (weather-immune)
ROOFED_TEAMS = {'ARI', 'ATL', 'DAL', 'DET', 'HOU', 'IND', 'LAR', 'LV', 'MIN', 'NO'}

# ============================================================
# Weekly tuning
# ============================================================

# Players to force into / out of the pool (weekly tuned)
READD = [

]
BANNED_CLASSIC = [
#   'Jared Goff', 'Chris Olave', 'Seattle Seahawks', 'KC Concepcion', 'Dallas Goedert', 'Deebo Samuel Sr.', 'Demarcus Robinson', 'Mark Andrews', 'Denver Broncos', 'Tennessee Titans'
]
BANNED_SINGLE = [
]

LOCKED = [
  'Bryce Young',
  ]
BLOCKED_TEAMS = []
