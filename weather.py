import requests
import pandas as pd
import os
import re
import numpy as np
from bs4 import BeautifulSoup
from datetime import datetime

import config
from projection import (
    calculate_precipitation_factor,
    calculate_temperature_factor,
    calculate_wind_factor,
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

def parse_weather_data(html_content):
    """Parse HTML content from NFLWeather.com and extract weather data for each game."""
    soup = BeautifulSoup(html_content, 'html.parser')

    games = []
    seen_matchups = set()

    # Find all game containers - look for sections with game info
    # The page structure has games in divs with specific classes
    game_sections = soup.find_all('div', class_=re.compile('game|matchup', re.I))

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


def get_weather_from_action_network():
    """
    Fetch weather data from Action Network API as a fallback source.
    
    @return: DataFrame with weather data for upcoming games
    """
    url = "https://api.actionnetwork.com/web/v1/games/weather/nfl"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        print("Attempting fallback weather source: Action Network API")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        games = data.get('games', [])
        
        if not games:
            print("No games found in Action Network response")
            return pd.DataFrame()
        
        weather_records = []
        
        for game in games:
            # Get team information
            teams = game.get('teams', [])
            if len(teams) < 2:
                continue
            
            away_team = teams[0].get('abbr', '')
            home_team = teams[1].get('abbr', '')
            
            # Get weather forecast (use the first forecast entry for game time)
            forecasts = game.get('weather_forecast', [])
            if not forecasts:
                continue
            
            forecast = forecasts[0]
            
            # Extract weather data
            weather_record = {
                'away_team': away_team,
                'home_team': home_team,
                'temperature': round(forecast.get('temperature', 0)),
                'condition': forecast.get('description', ''),
                'wind_speed': round(forecast.get('wind_speed', 0)),
                'wind_direction': forecast.get('wind_direction', ''),
                'precipitation_chance': round(forecast.get('precipitation', 0) * 100) if forecast.get('precipitation') else 0,
                'matchup': f"{away_team}@{home_team}"
            }
            
            weather_records.append(weather_record)
        
        if weather_records:
            print(f"Successfully retrieved {len(weather_records)} games from Action Network")
            return pd.DataFrame(weather_records)
        else:
            print("No valid weather data extracted from Action Network response")
            return pd.DataFrame()
    
    except requests.RequestException as e:
        print(f"Error fetching from Action Network API: {e}")
        return pd.DataFrame()


def get_nfl_weather(week=None, season=None):
    """
    Fetch weather data from NFLWeather.com for a given week.
    Falls back to Action Network API if primary source fails.

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
    if os.path.isfile(cache_file):
        print(f"Returning cached weather data for week {week}")
        return pd.read_csv(cache_file)

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
            # Try fallback source
            df = get_weather_from_action_network()
        
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

    print("\n" + "=" * 120)
    print("WEATHER CONDITIONS BY TEAM AND POSITION IMPACT")
    print("=" * 120)

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
    print("-" * 120)

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
        if home_abbr in config.ROOFED_TEAMS:
            impact_str = "Dome - weather-immune"
        else:
            parts = []
            for pos, label in (('QB', 'QB'), ('WR', 'WR'), ('TE', 'TE'), ('RB', 'RB'), ('D', 'D'), ('K', 'K')):
                factor = (calculate_wind_factor(info['wind'], pos)
                          * calculate_temperature_factor(info['temp'], pos)
                          * calculate_precipitation_factor(info['precip'], pos))
                if factor >= 1.02:
                    parts.append(f"{label}↑")
                elif factor <= 0.98:
                    parts.append(f"{label}↓")
            impact_str = " ".join(parts) if parts else "Neutral"

        print(f"{team:<8} {temp:<8} {wind:<8} {precip:<10} {condition:<12} {home_away:<10} {impact_str:<50}")

    print("=" * 120)
    print("\nPosition Impact Legend:")
    print("  Based on the exact projection factors: QB↓/WR↓ = passing penalized (rain/wind/cold),")
    print("  RB↑ = running boosted (rain/wind), K↓ = kicking penalized, D↑ = defense boosted.")
    print("  'Dome - weather-immune' = played in a covered stadium, no weather adjustment.")
    print("=" * 120)


def fmt_weather(value, suffix):
    """Format a weather value or render N/A for missing/NaN data."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    return f"{value}{suffix}"
