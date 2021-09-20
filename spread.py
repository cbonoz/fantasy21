import requests
import pandas as pd
import os

def get_spread_data(week):
    url = "https://bettingdata.com/NFL_Odds/Odds_Read"
    file_name = f"./data/week_{week}.csv"
    if os.path.isfile(file_name):
        print('return cached data')
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

    data = response.json()

    df = pd.DataFrame.from_records(data['Scores'])
    df.to_csv(file_name)
    return df
