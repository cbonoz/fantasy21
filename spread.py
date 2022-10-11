import requests
import pandas as pd
import numpy as np
import os
from bs4 import BeautifulSoup
import json
from urllib.request import urlopen

def get_bettingdata_spread(week):
    url = "https://bettingdata.com/NFL_Odds/Odds_Read"
    file_name = f"./spread/week_{week}.csv"
    if os.path.isfile(file_name):
        print('return cached data', f"week {week}")
        return pd.read_csv(file_name)
    else:
        print('fetching data')
        
    payload = "{\"filters\":{\"scope\":1,\"subscope\":2,\"week\":" + str(week) + ",\"season\":2021,\"seasontype\":1,\"team\":null,\"conference\":null,\"exportType\":null,\"date\":null,\"teamkey\":\"ARI\",\"show_no_odds\":false,\"client\":1,\"state\":\"WORLD\",\"geo_state\":null,\"league\":\"nfl\",\"widget_scope\":1}}"
    headers = {
      'authority': 'bettingdata.com',
      'sec-ch-ua': '"Google Chrome";v="93", " Not;A Brand";v="99", "Chromium";v="93"',
      'accept': 'application/json, text/plain, */*',
      'content-type': 'application/json;charset=UTF-8',
      'sec-ch-ua-mobile': '?0',
      'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.82 Safari/537.36',
      'sec-ch-ua-platform': '"macOS"',
      'origin': 'https://bettingdata.com',
      'sec-fetch-site': 'same-origin',
      'sec-fetch-mode': 'cors',
      'sec-fetch-dest': 'empty',
      'referer': 'https://bettingdata.com/nfl/odds?scope=1&subscope=2&week=3&season=2021&seasontype=1&teamkey=ARI&client=1&state=WORLD&league=nfl&widget_scope=1',
      'accept-language': 'en-US,en;q=0.9,es;q=0.8',
      'cookie': '_ga=GA1.1.197300910.1632099438; _cioanonid=b79c5d7c-926f-6403-3a20-2fd25bdfc87d; _ga_QXY5NYRE7Q=GS1.1.1632099437.1.1.1632099464.0'
    }

    response = requests.request("POST", url, headers=headers, data=payload)
    try:
        data = response.json()
    except Exception as e:
        data = response.text
        print('error', data, e)
        return pd.DataFrame()
        

    df = pd.DataFrame.from_records(data['Scores'])
    df.to_csv(file_name)
    return df

def get_current_rankings(week, x_hash_header, year_string='current', defense=False):
    # @param week: Week for ranking query.
    # @param x_hash: added param as x_hash header changes week over week.
    # https://www.lineups.com/nfl-team-rankings
    
    defense_string = "defense/" if defense else ""
    url = f"https://api.lineups.com/nfl/fetch/teams/team-rankings/{defense_string}{year_string}"
    
    file_name = f"./ranking/week_{week}_defense.csv" if defense else f"./ranking/week_{week}.csv"
    if os.path.isfile(file_name):
        print('return cached data', f"week {week}")
        return pd.read_csv(file_name)
    else:
        print('fetching data', x_hash_header, url)
    

    payload={}
    headers = {
      'x-hash': x_hash_header,
      'Accept': 'application/json, text/plain, */*',
      'Referer': 'https://www.lineups.com/',
      'sec-ch-ua-mobile': '?0',
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
      'sec-ch-ua': '" Not A;Brand";v="99", "Chromium";v="96", "Google Chrome";v="96"',
      'sec-ch-ua-platform': '"macOS"'
    }


    response = requests.request("GET", url, headers=headers, data=payload)

    data = response.json()
    try:
        df = pd.DataFrame.from_records(data['data'])
    except Exception as e:
        print('err', data)
        # If error recopy headers from the above request made on: https://www.lineups.com/nfl-team-rankings
        raise e
    df.to_csv(file_name)
    return df


def get_metabet_spread(week, api_key='219f64094f67ed781035f5f7a08840fc'):
    file_name = f"./spread/week_{week}.csv"
    if os.path.isfile(file_name):
        print('return cached data', f"week {week}")
        return pd.read_csv(file_name)
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
    print(results)
    if results:
        df = pd.DataFrame(results)
        df.to_csv(file_name)

    return df

def get_fantasy_def_points_against(week):
    file_name = f"./ranking/defense_{week}.json"
    if os.path.isfile(file_name):
        print('return cached data', f"week {week}")
        with open(file_name) as json_file:
            data = json.load(json_file)
            return data
    else:
        print('fetching data')
        
    url = f"https://fantasy.nfl.com/research/pointsagainst?position=8&statCategory=pointsAgainst&statSeason=2021&statType=seasonPointsAgainst"
    page = urlopen(url)
    soup = BeautifulSoup(page, "html.parser")
    rows = soup.find_all('td', attrs={'class': 'teamNameAndInfo'})
    
    teams = [row.text.replace('vs', '').replace(' DEF', '') for row in rows]
    points = []
    for r in soup.find_all('td', attrs={'class', 'numeric'}):
        against = r.find('span', attrs={'class', 'pointsAgainstStatId-pts'})
        if against:
            points.append(float(against.text))

    def_map = {}
    for i, team in enumerate(teams):
        rank = len(teams) - i
        def_map[team] = {
            'rank': rank,
            'allowed': points[i]
        }
    
    with open(file_name, 'w') as outfile:
        outfile.write(json.dumps(def_map))
        
    return def_map