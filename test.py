import requests

url = "https://api.lineups.com/nfl/fetch/teams/team-rankings/current"

payload={}
headers = {
  'Connection': 'keep-alive',
  'sec-ch-ua': '" Not A;Brand";v="99", "Chromium";v="96", "Google Chrome";v="96"',
  'Accept': 'application/json, text/plain, */*',
  'sec-ch-ua-mobile': '?0',
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.55 Safari/537.36',
  'x-hash': 'MTA4NjUyNzIwNQ==',
  'sec-ch-ua-platform': '"macOS"',
  'Origin': 'https://www.lineups.com',
  'Sec-Fetch-Site': 'same-site',
  'Sec-Fetch-Mode': 'cors',
  'Sec-Fetch-Dest': 'empty',
  'Referer': 'https://www.lineups.com/',
  'Accept-Language': 'en-US,en;q=0.9,es;q=0.8'
}

response = requests.request("GET", url, headers=headers, data=payload)

print(response.text)

