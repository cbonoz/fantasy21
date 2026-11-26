import requests
import pandas as pd
import os
import re
import numpy as np
import math
import time
from bs4 import BeautifulSoup
from datetime import datetime

import config
from nfl_teams import NFL_TEAM_MAP, normalize_team_abbr
from projection import (
    combined_weather_factor,
)

team_name_to_abbr = {
    'Rams': 'LAR', 'Cardinals': 'ARI', 'Falcons': 'ATL', 'Saints': 'NO', 'Panthers': 'CAR',
    'Bears': 'CHI', 'Lions': 'DET', 'Packers': 'GB', 'Vikings': 'MIN',
    'Cowboys': 'DAL', 'Eagles': 'PHI', 'Commanders': 'WAS', 'Giants': 'NYG',
    '49ers': 'SF', 'Seahawks': 'SEA', 'Buccaneers': 'TB',
    'Bills': 'BUF', 'Dolphins': 'MIA', 'Patriots': 'NE', 'Jets': 'NYJ',
    'Steelers': 'PIT', 'Browns': 'CLE', 'Ravens': 'BAL', 'Bengals': 'CIN',
    'Colts': 'IND', 'Texans': 'HOU', 'Jaguars': 'JAC', 'Titans': 'TEN',
    'Broncos': 'DEN', 'Chiefs': 'KC', 'Chargers': 'LAC', 'Raiders': 'LV',
    'Washington': 'WAS',
}


def _optional_round(value, scale=1):
    """Round a weather value, preserving missing data instead of inventing 0."""
    try:
        number = float(value) * scale
    except (TypeError, ValueError):
        return None
    return round(number) if math.isfinite(number) else None


def matchup_coverage(expected_matchups, weather_df):
    """Compare weather rows with the slate, ignoring malformed extra rows."""
    def normalize_weather_team(team):
        name = str(team).strip()
        abbreviation = team_name_to_abbr.get(name, NFL_TEAM_MAP.get(name, name))
        return normalize_team_abbr(abbreviation)

    def matchup_key(value):
        parts = str(value).split('@')
        if len(parts) != 2:
            return None
        teams = [normalize_weather_team(part) for part in parts]
        return tuple(sorted(teams)) if all(teams) else None

    expected = {key for key in map(matchup_key, expected_matchups) if key}
    actual = set()
    if weather_df is not None and not weather_df.empty:
        for row in weather_df.itertuples(index=False):
            teams = tuple(sorted((
                normalize_weather_team(getattr(row, 'away_team', '')),
                normalize_weather_team(getattr(row, 'home_team', '')),
            )))
            if all(teams):
                actual.add(teams)
    missing = sorted(expected - actual)
    return len(expected) - len(missing), len(expected), missing


def parse_weather_data(html_content):
    """Parse HTML content from NFLWeather.com and extract weather data for each game."""
    soup = BeautifulSoup(html_content, 'html.parser')

    games = []
    seen_matchups = set()

    # Find all game containers - look for sections with game info
    # The page structure has games in divs with specific classes
    # Prefer the individual game cards. Matching every class containing
    # "game" also captured the week-wide parent container, mixing one game's
    # matchup with conditions from later cards.
    game_sections = soup.select('div.game-box')
    if not game_sections:
        game_sections = soup.find_all(
            'div', class_=re.compile(r'^(?!container-).*(game|matchup)', re.I)
        )

    if not game_sections:
        # Alternative approach: look for divs containing team names and weather
        # Parse the structure more carefully
        all_divs = soup.find_all('div')
        for div in all_divs:
            text = div.get_text()
            # Look for patterns with team names and weather info
            if '@' in text and ('mph' in text or '°F' in text or '%' in text):
                game_data = parse_game_section(div)
                if game_data and game_data['matchup'] not in seen_matchups:
                    games.append(game_data)
                    seen_matchups.add(game_data['matchup'])
    else:
        for game_section in game_sections:
            game_data = parse_game_section(game_section)
            if game_data and game_data['matchup'] not in seen_matchups:
                games.append(game_data)
                seen_matchups.add(game_data['matchup'])

    return pd.DataFrame(games) if games else pd.DataFrame()


def parse_game_section(div):
    """Parse a single game section from the HTML."""
    text = div.get_text(separator=' ', strip=True)

    # Try to extract teams using @ separator
    if '@' not in text:
        return None

    # Split into away@home format
    parts = text.split('@')
    if len(parts) < 2:
        return None

    away_team = parts[0].strip().split()[-1] if parts[0].strip() else None
    home_parts = parts[1].strip().split()
    home_team = home_parts[0] if home_parts else None

    if not away_team or not home_team:
        return None

    # Extract weather information
    weather_info = extract_weather_from_text(text)

    if not weather_info:
        return None

    return {
        'away_team': away_team,
        'home_team': home_team,
        'temperature': weather_info.get('temperature'),
        'condition': weather_info.get('condition'),
        'wind_speed': weather_info.get('wind_speed'),
        'wind_direction': weather_info.get('wind_direction'),
        'precipitation_chance': weather_info.get('precipitation_chance'),
        'matchup': f"{away_team}@{home_team}"
    }


def extract_weather_from_text(text):
    """Extract weather details from text."""
    weather = {}

    # Temperature (e.g., "71 °F" or "71°F")
    temp_match = re.search(r'(\d+)\s*°\s*F', text)
    if temp_match:
        weather['temperature'] = int(temp_match.group(1))

    # Precipitation chance (e.g., "64%" or "10%")
    precip_match = re.search(r'(\d+)%', text)
    if precip_match:
        weather['precipitation_chance'] = int(precip_match.group(1))

    # Wind speed (e.g., "11 mph" or "11mph")
    wind_match = re.search(r'(\d+)\s*mph', text)
    if wind_match:
        weather['wind_speed'] = int(wind_match.group(1))

    # Wind direction (e.g., "south_west", "SW", etc.)
    direction_patterns = ['north', 'south', 'east', 'west', 'northeast', 'northwest', 'southeast', 'southwest', 'NE', 'NW', 'SE', 'SW', 'N', 'S', 'E', 'W']
    for direction in direction_patterns:
        if direction.lower() in text.lower():
            weather['wind_direction'] = direction
            break

    # Weather condition (e.g., "Clear", "Chance Rain", "Showersair", etc.)
    condition_keywords = ['clear', 'cloud', 'rain', 'snow', 'thunderstorm', 'wind', 'fog', 'drizzle', 'shower']
    for keyword in condition_keywords:
        if keyword.lower() in text.lower():
            weather['condition'] = keyword.capitalize()
            break

    return weather if weather else None


def _action_network_weather_records(data):
    """Parse Action Network's hourly forecasts using explicit home/away IDs."""
    records = []
    for game in data.get('games', []):
        teams = game.get('teams', [])
        by_id = {str(team.get('id')): team.get('abbr', '') for team in teams}
        away = by_id.get(str(game.get('away_team_id')))
        home = by_id.get(str(game.get('home_team_id')))
        if not away or not home:
            # Older responses may omit IDs; retain the legacy order as fallback.
            if len(teams) < 2:
                continue
            away, home = teams[0].get('abbr', ''), teams[1].get('abbr', '')

        forecasts = game.get('weather_forecast') or []
        if not forecasts:
            continue
        start = pd.to_datetime(game.get('start_time'), utc=True, errors='coerce')
        valid = [forecast for forecast in forecasts if isinstance(forecast, dict)]
        if not valid:
            continue
        if pd.notna(start):
            dated = []
            for forecast in valid:
                forecast_time = pd.to_datetime(
                    forecast.get('date'), utc=True, errors='coerce'
                )
                if pd.notna(forecast_time):
                    dated.append((abs((forecast_time - start).total_seconds()), forecast))
            forecast = min(dated, key=lambda item: item[0])[1] if dated else valid[0]
        else:
            forecast = valid[0]

        records.append({
            'away_team': away,
            'home_team': home,
            'temperature': _optional_round(forecast.get('temperature')),
            'condition': forecast.get('description', ''),
            'wind_speed': _optional_round(forecast.get('wind_speed')),
            'wind_direction': forecast.get('wind_direction', ''),
            'precipitation_chance': _optional_round(
                forecast.get('precipitation'), scale=100
            ),
            'matchup': f'{away}@{home}',
        })
    return pd.DataFrame(records)


def _merge_weather_forecasts(primary, supplemental):
    """Use the timestamped hourly forecast for matching games; retain page-only games."""
    if primary is None or primary.empty:
        return supplemental if supplemental is not None else pd.DataFrame()
    if supplemental is None or supplemental.empty:
        return primary

    def normalize_weather_team(team):
        name = str(team).strip()
        abbreviation = team_name_to_abbr.get(name, NFL_TEAM_MAP.get(name, name))
        return normalize_team_abbr(abbreviation)

    def key(row):
        return tuple(sorted((
            normalize_weather_team(row.get('away_team')),
            normalize_weather_team(row.get('home_team')),
        )))

    supplemental_by_matchup = {
        key(row): row for row in supplemental.to_dict('records') if all(key(row))
    }
    merged = []
    for row in primary.to_dict('records'):
        forecast = supplemental_by_matchup.get(key(row))
        if forecast:
            # Keep the primary source's home/away mapping; Action Network's
            # `teams` array is sorted by ID, not by game location.
            for field in (
                'temperature', 'condition', 'wind_speed', 'wind_direction',
                'precipitation_chance',
            ):
                value = forecast.get(field)
                if value is not None and pd.notna(value):
                    row[field] = value
        merged.append(row)
    return pd.DataFrame(merged)


def get_weather_from_action_network():
    """
    Fetch the timestamped hourly weather data from Action Network.
    
    @return: DataFrame with weather data for upcoming games
    """
    url = "https://api.actionnetwork.com/web/v1/games/weather/nfl"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        print("Fetching supplemental weather source: Action Network API")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        records = _action_network_weather_records(data)

        if records.empty:
            print("No games found in Action Network response")
            return pd.DataFrame()
        print(f"Successfully retrieved {len(records)} games from Action Network")
        return records
    
    except requests.RequestException as e:
        print(f"Error fetching from Action Network API: {e}")
        return pd.DataFrame()


def get_nfl_weather(week=None, season=None, force_refresh=False):
    """
    Fetch weekly weather data and merge matching game-time forecasts from
    Action Network. Either source can serve as a fallback if the other fails.

    @param week: NFL week number (1-18 for regular season, or wild-card, etc.)
    @param season: NFL season year (defaults to config season)
    @return: DataFrame with weather data for each matchup
    """
    if season is None:
        season = int(config.SEASON_START.split('/')[-1])

    if week is None:
        season_start = datetime(season, 9, 1)
        today = datetime.today()
        days_elapsed = (today - season_start).days + 1
        week = max(1, (days_elapsed // 7) + 1)
        if week > 18:
            week = 19  # Playoffs

    # Check cache first
    cache_file = f"./ranking/weather_week{week}.csv"
    weather_cache_fresh = (
        os.path.isfile(cache_file)
        and time.time() - os.path.getmtime(cache_file)
        < config.WEATHER_CACHE_MAX_AGE_MINUTES * 60
    )
    if weather_cache_fresh and not force_refresh:
        print(f"Returning cached weather data for week {week}")
        return pd.read_csv(cache_file)
    if os.path.isfile(cache_file):
        print(f"Cached weather data for week {week} is stale; refreshing")

    # Construct URL based on week
    if week <= 18:
        url = f"https://www.nflweather.com/week/{season}/week-{week}"
    else:
        # Playoff weeks
        playoff_map = {
            19: 'wild-card',
            20: 'divisional',
            21: 'conference',
            22: 'super-bowl'
        }
        week_name = playoff_map.get(week, f'week-{week}')
        if week > 22:
            week_name = 'super-bowl'
        url = f"https://www.nflweather.com/week/{season}/{week_name}"

    print(f"Fetching weather data from {url}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse the HTML
        df = parse_weather_data(response.text)

        if df.empty:
            print(f"No weather data found for week {week}")
        # Merge a timestamped hourly forecast into the weekly page values.
        # This catches stale/coarse game-card values without losing games the
        # hourly API does not cover.
        action_df = get_weather_from_action_network()
        df = _merge_weather_forecasts(df, action_df)
        
        if df.empty:
            print("No weather data available from any source")
            return pd.DataFrame()

        # Cache the results
        df.to_csv(cache_file, index=False)
        print(f"Saved weather data to {cache_file}")

        return df

    except requests.RequestException as e:
        print(f"Error fetching from NFLWeather.com: {e}")
        # Try fallback source
        print("Attempting fallback source...")
        df = get_weather_from_action_network()
        
        if not df.empty:
            # Cache the fallback results
            df.to_csv(cache_file, index=False)
            print(f"Saved weather data to {cache_file}")
        
        return df


def display_weather_summary(weather_df):
    """
    Display weather conditions and their impact on positions.

    The impact shown is derived from the SAME projection factors the optimizer
    uses (wind x temp x precip per position), so it never contradicts the
    actual lineup adjustments. Games in domed/roofed venues are marked
    weather-immune. Missing forecast values render as N/A.
    """
    if weather_df.empty:
        print("No weather data available to display")
        return

    print("\n" + "=" * 112)
    print("WEATHER CONDITIONS BY TEAM AND POSITION IMPACT")
    print("=" * 112)

    weather_summary = {}
    for _, game in weather_df.iterrows():
        for team in [game.get('away_team'), game.get('home_team')]:
            if team and isinstance(team, str) and team not in weather_summary:
                weather_summary[team] = {
                    'temp': game.get('temperature'),
                    'wind': game.get('wind_speed'),
                    'precip': game.get('precipitation_chance'),
                    'condition': game.get('condition'),
                    'is_home': team == game.get('home_team'),
                    'home_team': game.get('home_team'),
                }

    print(f"{'Team':<8} {'Temp':<8} {'Wind':<8} {'Precip':<10} {'Condition':<12} {'Home/Away':<10} {'Impact on Positions':<50}")
    print("-" * 112)

    rows = []
    for team in sorted(weather_summary.keys()):
        info = weather_summary[team]
        temp = fmt_weather(info['temp'], '°F')
        wind = fmt_weather(info['wind'], 'mph')
        precip = fmt_weather(info['precip'], '%')
        home_away = "HOME" if info['is_home'] else "AWAY"
        cond = info['condition']
        condition = (str(cond).capitalize()
                     if cond is not None and not (isinstance(cond, float) and np.isnan(cond))
                     else "N/A")

        home_abbr = team_name_to_abbr.get(str(info['home_team']), str(info['home_team']))
        roofed = home_abbr in config.ROOFED_TEAMS
        parts = []
        for pos, label in (('QB', 'QB'), ('WR', 'WR'), ('TE', 'TE'), ('RB', 'RB'), ('D', 'D'), ('K', 'K')):
            factor = combined_weather_factor(
                info['wind'], info['temp'], info['precip'], pos,
                config.WEATHER_MAX_ADJUSTMENT.get(pos),
            )
            if factor >= 1.02:
                parts.append(f"{label}↑")
            elif factor <= 0.98:
                parts.append(f"{label}↓")
        if not parts:
            continue
        impact_str = " ".join(parts)
        # Dome/roofed games are weather-immune (no projection adjustment), but
        # surface them when the raw forecast is severe enough that an open roof
        # could matter — flagged as informational only.
        if roofed:
            impact_str += "  (dome - informational)"
        rows.append((team, temp, wind, precip, condition, home_away, impact_str))

    if not rows:
        print("  (no abnormal weather conditions affecting this slate)")

    for team, temp, wind, precip, condition, home_away, impact_str in rows:
        print(f"{team:<8} {temp:<8} {wind:<8} {precip:<10} {condition:<12} {home_away:<10} {impact_str:<50}")

    print("=" * 112)
    print("\nPosition Impact Legend (only teams with weather that shifts projections are shown):")
    print("  Based on the exact projection factors: QB↓/WR↓ = passing penalized (rain/wind/cold),")
    print("  RB↑ = running boosted (rain/wind), K↓ = kicking penalized, D↑ = defense boosted.")
    print("  Neutral weather is omitted; dome/roofed games appear only when the raw forecast")
    print("  is severe (marked informational — no projection adjustment is applied).")
    print("=" * 112)


def fmt_weather(value, suffix):
    """Format a weather value or render N/A for missing/NaN data."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    return f"{value}{suffix}"
