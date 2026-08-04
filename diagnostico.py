import requests, json
TOKEN = "2b70ce692bcc70fd7283c15345f5361b"
headers = {"x-auth-token": TOKEN}
r = requests.get("https://bosstreinamentos.com/api/1/certificate", headers=headers, params={"limit": 2})
print(json.dumps(r.json()[:2], indent=2, ensure_ascii=False))
