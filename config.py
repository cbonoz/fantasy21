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
SALARY_MIN_OFFSET = 100
SALARY_MIN_OFFSET_SINGLE = 300
MIN_SALARY_CLASSIC = 5000
MIN_SALARY_SINGLE = 2100
MIN_QB_SALARY_CLASSIC = 6400
MIN_QB_SALARY_SINGLE = 1000
ROSTER_SIZE_CLASSIC = 9
ROSTER_SIZE_SINGLE = 6
MAX_PLAYERS_PER_TEAM_CLASSIC = 9

# Number of rows shown in the sorted defenses / QBs / MVPs printouts.
TOP_N_DISPLAY = 10

# ============================================================
# Projection weights
# ============================================================

WEIGHTED = True
AVERAGE_WEIGHT = .5
INJURY_FACTOR = .12
# Partial credit for Questionable players (they often play, but a banged-up
# starter still creates teammate opportunity). Out/IR get 1.0; Doubtful is 0.
INJURY_QUESTIONABLE_WEIGHT = 0.4
# History/ceiling smoothing: recent games decay exponentially in history.
HISTORY_RECENCY_DECAY = 0.8
# GPP upside tilt: blend projections toward each player's ceiling so lineups
# target boom scores (needed for winning/top-10% GPP finishes). SINGLE outside
# single-game; set 0 for strict cash optimization.
GPP_UPSIDE_WEIGHT = 0.35
# Single-game only: blend projections toward each player's observed ceiling so
# the optimizer targets boom scores (needed for winning/top-10% GPP finishes)
# instead of point-expectation averages.
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
# Bonus when a team's starting QB is out. Full value to defenses/MVPs facing
# the injured QB (backup-QB matchups are a strong DST edge); half to skill
# players (game-script benefit); RBs get full when their OWN QB is out.
INJURED_QB_BONUS = 2.5
# Multiplier on the WR-injury pool for a team's remaining WRs: when a WR is
# out, the other WRs inherit the vacated targets on top of the shared pool.
WR_INHERITANCE_WEIGHT = 4.0
# Cap on the per-team WR-injury pool that gets the inheritance multiplier:
# multiple injured WRs share the same target pie, so their contributions
# shouldn't stack unboundedly.
WR_INHERITANCE_CAP = 2.0
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

# Players to force into / out of the pool (weekly tuned). Single-game slates
# include '(MVP)' variants for duplicated players.
READD = [

]
BANNED_CLASSIC = [
  'Josh Allen', 'Cody White', 'Jameis Winston', 'Kirk Cousins', 'Tyler Shough',
]
BANNED_SINGLE = [
  'Romeo Doubs', 'Ashton Jeanty', 'TreVeyon Henderson', 'New England Patriots', 'Cody White', 'Brock Bowers', 'Ashton Jeanty (MVP)', 'TreVeyon Henderson (MVP)', 'Brock Bowers (MVP)', 'Xavier Smith', 'Romeo Doubs (MVP)', 'New York Jets', 'Las Vegas Raiders', 'James Cook III','Kirk Cousins (MVP)', 'Las Vegas Raiders (MVP)',
]

LOCKED = [


  ]
BLOCKED_TEAMS = []
