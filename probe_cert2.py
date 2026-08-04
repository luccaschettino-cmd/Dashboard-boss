import requests
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('EAD_TOKEN')
BASE = 'https://bosstreinamentos.com/api/1'
H = {'x-auth-token': token}


def data_no(off):
    try:
        r = requests.get(f'{BASE}/certificate', headers=H, params={'limit': 1, 'offset': off}, timeout=30)
        d = r.json()
        if d and len(d) > 0:
            return d[0].get('concluido', 'sem_data')
        return 'VAZIO'
    except Exception as e:
        return f'ERRO'


# Testa se offset funciona e onde os dados terminam
print('Verificando paginação e volume de certificados:')
for off in [0, 5000, 10000, 20000, 30000, 40000, 50000]:
    print(f'  offset {off}: concluido={data_no(off)}')
