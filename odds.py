import requests
import pandas as pd
import numpy as np
import os
import re
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

    file_name = f"./ranking/week_{week}.csv"
    if os.path.isfile(file_name):
        print('return cached data', f"week {week}")
        return pd.read_csv(file_name)

    url = 'https://www.lineups.com/nfl-team-rankings'
    print('fetching data from', url)

    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9,es;q=0.8',
        'cache-control': 'max-age=0',
        'cookie': '_ga=GA1.1.594751972.1756564717; cf_76213_id=2d1c25a8-460a-4e43-9b95-916d05d43508; cf_76213_first_touch=%7B%22landing_page%22%3A%22https%3A//www.lineups.com/%22%2C%22referral_source%22%3A%22https%3A//www.google.com/%22%2C%22timestamp%22%3A1756564717171%7D; cf_76213_cta_187636=245722; cf_76213_cta_187639=245740; cf_76213_cta_187637=245733; cf_76213_cta_187641=245746; cf_76213_cta_187732=245897; cf_76213_cta_187733=245903; cf_76213_person_time=1756564761492; __cf_bm=La8WZAac1pCJEiI62n1eYyf1miOWXISUzzo9AMTXYg4-1756565616-1.0.1.1-rWNPWLXWaD1lQxfIDLshNbJiufn2BrSIhvNGyNYcGyQ0krK8BBVsHvNiZQ3E._UhS3z9mBcT2k2Ae_12gperRwtK2mhxGnNtm.MwduOiN4g; nexus_cookie={"is_new_session":"true","user_id":"88adb57e-f169-4b0f-a936-c698095743c8","last_session_id":"e1a9f00f-bdb5-4a8c-aa55-e3a3c5255e4e","last_session_start":"1756566225499","referrer_og":"https://www.lineups.com/nfl-team-rankings/defense"}; cf_76213_person_last_update=1756566233922; _ga_DZ7RXNC50W=GS2.1.s1756564717$o1$g1$t1756566233$j52$l0$h0',
        'priority': 'u=0, i',
        'sec-ch-ua': '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }

    resp = requests.get(url, headers=headers)
    # Handle compressed content
    if resp.headers.get('Content-Encoding') in ('gzip', 'br', 'deflate', 'zstd'):
        import io
        import gzip
        import brotli
        import zlib
        encoding = resp.headers.get('Content-Encoding')
        raw = resp.content
        if encoding == 'gzip':
            html = gzip.decompress(raw).decode('utf-8', errors='replace')
        elif encoding == 'deflate':
            html = zlib.decompress(raw).decode('utf-8', errors='replace')
        elif encoding == 'br':
            html = brotli.decompress(raw).decode('utf-8', errors='replace')
        elif encoding == 'zstd':
            try:
                import zstandard as zstd
                dctx = zstd.ZstdDecompressor()
                html = dctx.decompress(raw).decode('utf-8', errors='replace')
            except ImportError:
                raise RuntimeError('zstandard module required for zstd decoding')
        else:
            html = raw.decode('utf-8', errors='replace')
    else:
        html = resp.text

    # Extract window.TRANSFER_STATE JSON robustly
    match = re.search(r'window\.TRANSFER_STATE\s*=\s*(\{[\s\S]*?\})\s*;', html)
    if not match:
        # Try fallback: match up to </script> if no semicolon
        match = re.search(r'window\.TRANSFER_STATE\s*=\s*(\{[\s\S]*?\})\s*</script>', html)
    if not match:
        print(html)
        raise ValueError('Could not find embedded rankings JSON in page source.')
    transfer_state_str = match.group(1)
    # Replace JS keys (e.g., data:) with quoted keys ("data":)
    transfer_state_str = re.sub(r'(\{|,|\s)([a-zA-Z0-9_]+):', r'\1"\2":', transfer_state_str)
    data = json.loads(transfer_state_str)

    # Always use the /current key for the main rankings page
    url_key = 'https://api.lineups.com/nfl/fetch/teams/team-rankings/current'
    rankings = data.get(url_key, {}).get('data', [])
    if not rankings:
        raise ValueError('Could not find team rankings in embedded JSON.')
    df = pd.DataFrame(rankings)
    df.to_csv(file_name, index=False)
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
