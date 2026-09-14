NFL_TEAM_MAP = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAC",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS"
}

# Team abbreviations that differ between data sources
TEAM_ABBR_ALIASES = {
    'LA': 'LAR', 'SL': 'LAR', 'STL': 'LAR',
    'JAX': 'JAC',
    'OAK': 'LV', 'SD': 'LAC',
}


def normalize_team_abbr(abbr):
    return TEAM_ABBR_ALIASES.get(str(abbr).strip().upper(), str(abbr).strip().upper())


def normalize_player_name(name):
    """Lowercase, strip periods, collapse whitespace: 'J.K. Dobbins' -> 'jk dobbins'."""
    return ' '.join(str(name).lower().replace('.', '').split())
