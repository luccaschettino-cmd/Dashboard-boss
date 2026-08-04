import sqlite3
import json

conn = sqlite3.connect('cache.db')
rows = [json.loads(r[0]) for r in conn.execute('SELECT data FROM enrollments').fetchall()]
conn.close()

for s in [1, 2, 3, 4]:
    exemplos = [e for e in rows if e.get('status') == s][:3]
    print(f'\n--- STATUS {s} ---')
    for e in exemplos:
        cad = str(e.get('cadastro', ''))[:10]
        exp = str(e.get('expira', ''))[:10]
        ini = str(e.get('inicio', ''))[:10]
        cert = e.get('emitir_certificado')
        titulo = str(e.get('titulo_curso', ''))[:30]
        print(f'  cadastro={cad} expira={exp} inicio={ini} cert={cert} | {titulo}')
