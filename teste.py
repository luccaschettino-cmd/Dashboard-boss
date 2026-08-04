import requests
import json

TOKEN = "2b70ce692bcc70fd7283c15345f5361b"
BASE_URL = "https://bosstreinamentos.com/api/1"
headers = {"x-auth-token": TOKEN}

r = requests.get(f"{BASE_URL}/course", headers=headers, params={"limit": 5})
cursos = r.json()

print("Total retornado nessa página:", len(cursos))
print()
print(json.dumps(cursos[:3], indent=2, ensure_ascii=False))