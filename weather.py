import requests
import pandas as pd
import os
import re
import numpy as np
from bs4 import BeautifulSoup
from datetime import datetime

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


def get_nfl_weather(week=None, season=2025):
    """
    Fetch weather data from NFLWeather.com for a given week.
    Falls back to Action Network API if primary source fails.

    @param week: NFL week number (1-18 for regular season, or wild-card, etc.)
    @param season: NFL season year
    @return: DataFrame with weather data for each matchup
    """

    if week is None:
        # Auto-detect current week
        from datetime import datetime
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


def apply_weather_adjustments(players_df, weather_df, weather_factors=None):
    """
    Apply weather-based adjustments to player projections.

    @param players_df: DataFrame with player data (must have 'team' or 'Team' column)
    @param weather_df: DataFrame with weather data from get_nfl_weather()
    @param weather_factors: Dict with adjustment factors for different conditions
    @return: players_df with new 'weather_factor' column
    """

    if weather_df.empty:
        print("No weather data available, skipping adjustments")
        players_df['weather_factor'] = 1.0
        return players_df

    # Default weather impact factors
    if weather_factors is None:
        weather_factors = {
            'temperature': {
                'cold': (0, 40, 0.98),  # (min_temp, max_temp, factor)
                'cool': (40, 55, 0.99),
                'moderate': (55, 75, 1.0),
                'warm': (75, 90, 1.01),
                'hot': (90, 110, 1.02)
            },
            'wind': {
                'calm': (0, 5, 1.0),
                'light': (5, 10, 0.98),
                'moderate': (10, 15, 0.96),
                'strong': (15, 20, 0.93),
                'very_strong': (20, 100, 0.90)
            },
            'precipitation': {
                'none': (0, 20, 1.0),
                'light': (20, 50, 0.97),
                'moderate': (50, 80, 0.95),
                'heavy': (80, 100, 0.92)
            }
        }

    # Find player's team in matchups
    team_col = 'Team' if 'Team' in players_df.columns else 'team'

    # Create weather factor column
    weather_factors_list = []

    for idx, player in players_df.iterrows():
        player_team = player[team_col]

        # Find matching game
        game = weather_df[
            (weather_df['away_team'] == player_team) |
            (weather_df['home_team'] == player_team)
        ]

        if game.empty:
            weather_factors_list.append(1.0)
            continue

        game = game.iloc[0]
        factor = 1.0

        # Apply temperature adjustment
        if pd.notna(game['temperature']):
            temp = game['temperature']
            for condition, (min_t, max_t, adj) in weather_factors['temperature'].items():
                if min_t <= temp < max_t:
                    factor *= adj
                    break

        # Apply wind adjustment
        if pd.notna(game['wind_speed']):
            wind = game['wind_speed']
            for condition, (min_w, max_w, adj) in weather_factors['wind'].items():
                if min_w <= wind < max_w:
                    factor *= adj
                    break

        # Apply precipitation adjustment
        if pd.notna(game['precipitation_chance']):
            precip = game['precipitation_chance']
            for condition, (min_p, max_p, adj) in weather_factors['precipitation'].items():
                if min_p <= precip < max_p:
                    factor *= adj
                    break

        weather_factors_list.append(factor)

    players_df['weather_factor'] = weather_factors_list
    return players_df


def display_weather_summary(weather_df):
    """
    Display weather conditions and their impact on positions.
    
    @param weather_df: DataFrame with weather data from get_nfl_weather()
    """
    if weather_df.empty:
        print("No weather data available to display")
        return
    
    # Display weather conditions and their impact on positions
    print("\n" + "="*120)
    print("WEATHER CONDITIONS BY TEAM AND POSITION IMPACT")
    print("="*120)

    weather_summary = {}
    for _, game in weather_df.iterrows():
        for team in [game.get('away_team'), game.get('home_team')]:
            # Only process if team is a valid string (not None, NaN, or numeric)
            if team and isinstance(team, str) and team not in weather_summary:
                weather_info = {
                    'temp': game.get('temperature'),
                    'wind': game.get('wind_speed'),
                    'precip': game.get('precipitation_chance'),
                    'condition': game.get('condition'),
                    'is_home': team == game.get('home_team')
                }
                weather_summary[team] = weather_info

    # Display weather by team with position impact
    print(f"{'Team':<8} {'Temp':<8} {'Wind':<8} {'Precip':<10} {'Condition':<12} {'Home/Away':<10} {'Impact on Positions':<50}")
    print("-"*120)

    for team in sorted(weather_summary.keys()):
        info = weather_summary[team]
        temp = f"{info['temp']}°F" if info['temp'] else "N/A"
        wind = f"{info['wind']}mph" if info['wind'] else "N/A"
        precip = f"{info['precip']}%" if info['precip'] else "N/A"
        home_away = "HOME" if info['is_home'] else "AWAY"
        condition = info['condition'] or "N/A"

        # Position impact summary - prioritize primary weather factors to avoid double counting
        impacts = set()  # Use set to avoid duplicates

        # Cold weather (<30°F) is the dominant factor - hurts all offense
        if info['temp'] and info['temp'] < 30:
            impacts.add("QB↓")  # QB penalized in cold
            impacts.add("WR↓")  # WR penalized in cold
            impacts.add("RB↓")  # RB efficiency reduced in cold
        # If not cold, check other factors
        else:
            # Moderate wind (>8mph) favors running game, hurts passing
            if info['wind'] and info['wind'] > 8:
                impacts.add("RB↑")  # RB benefits from wind
                impacts.add("QB/WR↓")  # QB/WR penalized

        # Any precipitation (>15%) hurts passing game - add only if not already cold and condition is not clear
        is_clear_condition = condition and 'clear' in str(condition).lower()
        if info['precip'] and info['precip'] > 15 and not (info['temp'] and info['temp'] < 30) and not is_clear_condition:
            impacts.add("QB↓")  # QB penalized by rain/snow
            impacts.add("WR↓")  # WR penalized by rain/snow

        # Warm/hot weather (>85°F) benefits passing game, increases scoring
        if info['temp'] and info['temp'] > 85:
            impacts.add("QB↑")  # QB benefits from warm
            impacts.add("WR↑")  # WR benefits from warm

        # Ideal passing conditions: low wind (<5mph), low precip, moderate temp (30-85°F)
        has_low_wind = info['wind'] is None or (isinstance(info['wind'], (int, float)) and info['wind'] <= 5)
        has_low_precip = info['precip'] is None or (isinstance(info['precip'], (int, float)) and info['precip'] <= 15) or (isinstance(info['precip'], float) and np.isnan(info['precip']))
        has_moderate_temp = info['temp'] is None or (isinstance(info['temp'], (int, float)) and 30 <= info['temp'] <= 85)

        if has_low_wind and has_low_precip and has_moderate_temp and not impacts:
            impacts.add("QB↑")  # QB benefits from clear conditions
            impacts.add("WR↑")  # WR benefits from clear conditions

        # Sort impacts for consistent display: positive first, then negative
        impact_list = sorted(impacts, key=lambda x: (x[-1] != '↑', x))
        impact_str = " ".join(impact_list) if impact_list else "Neutral"

        print(f"{team:<8} {temp:<8} {wind:<8} {precip:<10} {condition:<12} {home_away:<10} {impact_str:<50}")

    print("="*120)
    print("\nPosition Impact Legend:")
    print("  RB↑ = Running Backs benefit (wind favors rushing)")
    print("  QB↑/WR↑ = Quarterbacks/Wide Receivers benefit (warm weather, clear conditions)")
    print("  QB↓/WR↓ = Quarterbacks/Wide Receivers penalized (high wind, cold, or rain/snow hurts passing)")
    print("  RB↓ = Running Backs efficiency reduced (cold weather)")
    print("="*120)
