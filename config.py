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

# ============================================================
# Projection weights
# ============================================================

WEIGHTED = True
AVERAGE_WEIGHT = .5
INJURY_FACTOR = .12
# Bonus when a team's starting QB is out. Full value to defenses/MVPs facing
# the injured QB (backup-QB matchups are a strong DST edge); half to skill
# players (game-script benefit); RBs get full when their OWN QB is out.
INJURED_QB_BONUS = 2.5
# Multiplier on the opponent's net injury weakness (abs of excluded_bonus)
# for defenses/MVPs: a weakened opponent offense helps the D.
OPPONENT_INJURY_WEIGHT = 0.4
MIN_SCORE = 7
MAX_SCORE = 29
MAX_DEF_MULTIPLIER = 2.0
MIN_PROJ_MULTIPLIER = 0.5
LOW_SALARY_SKIP = 4200
MAX_WEATHER_BONUS = 0.40

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
  'Christian McCaffrey', 'Cincinnati Bengals', 'Derrick Henry', 'Deebo Samuel Sr.', 'James Cook III', 'Kenneth Walker III',
  'Case Keenum', 'Minnesota Vikings', 'Devaughn Vele', 'Jahmyr Gibbs', 'Jaxon Smith-Njigba',  'Parker Washington', 'CeeDee Lamb',
  # 'Cincinnati Bengals', 'New England Patriots', 'Devaughn Vele', 'Case Keenum', 'CeeDee Lamb', 'Patrick Mahomes',
  # 'Deebo Samuel Sr.', 'Christian McCaffrey', 'Chris Olave', 'Jahmyr Gibbs', 'Minnesota Vikings', "D'Andre Swift", "Kenneth Walker III", "Jaxon Smith-Njigba", "Derrick Henry", "Las Vegas Raiders"
  # 'Seattle Seahawks', 'Jakobi Meyers', 'Jaylen Waddle', 'George Kittle', 'CeeDee Lamb',
  # 'Mike Gesicki', 'Tre Tucker', 'Jaxon Smith-Njigba', 'Amon-Ra St. Brown',  'Devaughn Vele', 'Derrick Henry', 'Jahmyr Gibbs'
  # 'Juwan Johnson', 'Mike Gesicki', 'Chris Olave', 'Patrick Mahomes', 'Jaxon Smith-Njigba', 'Amon-Ra St. Brown', 'Justin Jefferson', 'Tre Tucker', 'Devaughn Vele'
  # 'Tre Tucker', 'Derrick Henry', 'Justin Jefferson', 'Jaxon Smith-Njigba', 'Amon-Ra St. Brown', 'Devaughn Vele', 'Garrett Wilson', 'Tetairoa McMillan', 'Chris Olave', "D'Andre Swift" , 'Chuba Hubbard', 'Jack Bech'
                  # 'Jaxon Smith-Njigba'
  # 'Kenneth Walker III', 'James Cook III', 'Jaxson Dart', 'Bryce Young', 'Antonio Williams', 'Braelon Allen', 'Amon-Ra St. Brown',
  #   'Patrick Mahomes', 'Antonio Williams', 'Jared Goff'
  # 'Kenneth Walker III', 'Antonio Williams', 'Jakobi Meyers', 'Jack Bech', 'James Cook III', 'Carolina Panthers', 'Emmett Johnson'
  # 'Pittsburgh Steelers', 'Deebo Samuel Sr.', 'Dallas Goedert', 'Justin Jefferson', 'Mark Andrews',
  # 'Parker Washington', 'Arizona Cardinals','
  # 'Pittsburgh Steelers',  'Parker Washington', 'Las Vegas Raiders',
  #   'Brenton Strange', 'David Montgomery', 'Dallas Goedert', 'Cincinnati Bengals', 'Justin Jefferson', 'Deebo Samuel Sr.', 'Trey McBride', 'Juwan Johnson', 'Seattle Seahawks', "D'Andre Swift"
  #  'Seattle Seahawks', 'Tampa Bay Buccaneers', 'San Francisco 49ers', 'Justin Jefferson'
#   'Jared Goff', 'Chris Olave', 'Seattle Seahawks', 'KC Concepcion', 'Dallas Goedert', 'Deebo Samuel Sr.', 'Demarcus Robinson', 'Mark Andrews', 'Denver Broncos', 'Tennessee Titans'
]
BANNED_SINGLE = ['Xavier Smith', 'New York Jets', 'James Cook III'
  # 'Kenneth Walker III (MVP)'
  # 'Los Angeles Rams', 'Ronnie Rivers', 'Devin Singletary', 'Terrance Ferguson'
  # 'DJ Moore (MVP)', 'Jahmyr Gibbs (MVP)'
]

LOCKED = [
  # 'Christian Watson'
  # 'Bijan Robinson'
  # 'Brock Purdy'
  # 'Dalton Schultz'
  # 'Josh Allen', 'Tennessee Titans', 'Chuba Hubbard',
  # 'Travis Kelce'
  # 'Terry McLaurin'
  # 'Josh Allen',
  # 'Davante Adams'
  # 'Matthew Stafford'
  # 'Travis Kelce', 'David Montgomery'
  # 'Cincinnati Bengals', 'Josh Allen',
  # 'Malik Nabers', 'Matthew Stafford'
  # 'Kenneth Walker III', 'Rashod Bateman', 'Derrick Henry', 'Bijan Robinson', 'Caleb Williams',
  # 'Bryce Young',
    # 'New England Patriots'
  ]
BLOCKED_TEAMS = []
