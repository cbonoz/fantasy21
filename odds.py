import requests
import pandas as pd
import numpy as np
import os
import time

import config

def _odds_cache_is_fresh(path):
    """Live lines get a short TTL; history-style caches use the multi-day one."""
    if not os.path.isfile(path):
        return False
    max_age = config.ODDS_CACHE_MAX_AGE_MINUTES * 60
    return time.time() - os.path.getmtime(path) < max_age

def get_metabet_spread(week, api_key='219f64094f67ed781035f5f7a08840fc'):
    file_name = f"./spread/week_{week}.csv"
    if _odds_cache_is_fresh(file_name):
        print('return cached data', f"week {week}")
        return pd.read_csv(file_name)
    if os.path.isfile(file_name):
        print('stale cache; refreshing', f"week {week}")
    else:
        print('fetching data')
    url = f"https://metabet.static.api.areyouwatchingthis.com/api/odds.json?apiKey={api_key}&location=MA&leagueCode=FBP"
    if week <= 18: # normal season.
        url += f"&round=week%20{week}"
    payload={}
    headers = {
      'authority': 'metabet.static.api.areyouwatchingthis.com',
      'sec-ch-ua': '" Not A;Brand";v="99", "Chromium";v="96", "Google Chrome";v="96"',
      'sec-ch-ua-mobile': '?0',
      'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
      'sec-ch-ua-platform': '"macOS"',
      'accept': '*/*',
      'origin': 'https://www.thelines.com',
      'sec-fetch-site': 'cross-site',
      'sec-fetch-mode': 'cors',
      'sec-fetch-dest': 'empty',
      'referer': 'https://www.thelines.com/',
      'accept-language': 'en-US,en;q=0.9,es;q=0.8'
    }

    try:
        response = requests.request("GET", url, headers=headers, data=payload)
        data = response.json()
        results = []
        for game in data['results']:
            spread = np.mean([odds['spread'] for odds in game['odds'] if 'spread' in odds])
            over_under = np.mean([odds['overUnder'] for odds in game['odds'] if 'overUnder' in odds])
            result = {
                'AwayTeam': game['team1Initials'],
                'HomeTeam': game['team2Initials'],
                'PointSpread': np.round(spread, 2),
                'OverUnder': np.round(over_under, 2)
            }
            results.append(result)
    except Exception as exc:
        # A failed refresh should not wipe usable lines; fall back to the last
        # successful snapshot when one exists.
        if os.path.isfile(file_name):
            print(f"odds refresh failed ({exc}); using stale lines", file_name)
            return pd.read_csv(file_name)
        raise
    print(results)
    df = pd.DataFrame(results)
    if results:
        df.to_csv(file_name)
    return df
