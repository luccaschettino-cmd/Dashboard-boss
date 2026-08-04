import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('EAD_TOKEN')
BASE = 'https://bosstreinamentos.com/api/1'
H = {'x-auth-token': token}

# Testa vários endpoints possíveis de certificado
endpoints = ['certificate', 'certificado', 'certificates', 'certificados']

for ep in endpoints:
    print(f'\n=== Testando /{ep} ===')
    try:
        r = requests.get(f'{BASE}/{ep}', headers=H, params={'limit': 2}, timeout=30)
        print(f'Status HTTP: {r.status_code}')
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list) and len(data) > 0:
                print('CAMPOS DISPONÍVEIS:')
                print(json.dumps(data[0], indent=2, ensure_ascii=False))
            elif isinstance(data, dict):
                print(json.dumps(data, indent=2, ensure_ascii=False)[:800])
            else:
                print('Retornou vazio')
        else:
            print(f'Resposta: {r.text[:200]}')
    except Exception as e:
        print(f'Erro: {e}')
