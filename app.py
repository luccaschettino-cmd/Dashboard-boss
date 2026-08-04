from flask import Flask, render_template, jsonify, send_file
import requests
import os
import sqlite3
import json
import io
from datetime import datetime, timedelta
from dotenv import load_dotenv
from collections import defaultdict
import re
import threading

load_dotenv()

app = Flask(__name__)

TOKEN = os.getenv("EAD_TOKEN")
BASE_URL = "https://bosstreinamentos.com/api/1"
HEADERS = {"x-auth-token": TOKEN}
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.db")

DATA_INICIO = "2023-01-01"
OFFSET_INICIAL = 4000
OFFSET_INICIAL_CERT = 4000

VALIDADE_NR = [
    ("nr 33", 365), ("nr 20 intermedi", 730), ("nr 20 avanc", 730),
    ("nr 20 basic", 1095), ("nr 20", 1095), ("nr 35", 730), ("nr 10", 730),
    ("nr 12", 730), ("nr 06", 730), ("nr 05", 730), ("nr 18", 730),
    ("nr 37", 730), ("nr 34", 730), ("direção defensiva", 1095),
    ("direcao defensiva", 1095), ("primeiros socorros", 730), ("cbasi", 730),
]
VALIDADE_PADRAO = 730


def validade_curso(titulo):
    if not titulo:
        return VALIDADE_PADRAO
    t = titulo.lower()
    for padrao, dias in VALIDADE_NR:
        if padrao in t:
            return dias
    return VALIDADE_PADRAO


sync_status = {"running": False, "progress": "", "last_sync": None, "total": 0, "done": 0}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS enrollments (id INTEGER PRIMARY KEY, data TEXT NOT NULL, synced_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS certificates (id INTEGER PRIMARY KEY, data TEXT NOT NULL, synced_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


def get_meta(key):
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        conn.close()
        return row[0] if row else None
    except:
        return None


def count_table(table):
    try:
        conn = sqlite3.connect(DB_PATH)
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return n
    except:
        return 0


def load_table(table):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(f"SELECT data FROM {table}").fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def sync_endpoint(endpoint, table, offset_inicial, date_field, label):
    LIMIT = 200
    offset = offset_inicial
    total_salvos = 0
    total_lidos = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"DELETE FROM {table}")
    conn.commit()
    while True:
        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS,
                             params={"limit": LIMIT, "offset": offset}, timeout=30)
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            items = data if isinstance(data, list) else (data.get("data") or data.get("results") or [])
            if not items:
                break
            total_lidos += len(items)
            filtrados = [e for e in items if (e.get(date_field) or "")[:10] >= DATA_INICIO]
            if filtrados:
                now_str = datetime.now().isoformat()
                batch = [(e.get("id") or e.get("matricula_id") or f"o{offset}_{i}", json.dumps(e), now_str)
                         for i, e in enumerate(filtrados)]
                c.executemany(f"INSERT OR REPLACE INTO {table} (id, data, synced_at) VALUES (?,?,?)", batch)
                conn.commit()
                total_salvos += len(filtrados)
            sync_status["progress"] = f"{label}: {total_lidos} lidos, {total_salvos} salvos"
            sync_status["done"] = total_salvos
            if len(items) < LIMIT:
                break
            offset += LIMIT
        except Exception as e:
            sync_status["progress"] = f"{label} erro offset {offset}: {e}"
            break
    conn.close()
    return total_salvos


def do_sync():
    global sync_status
    sync_status["running"] = True
    sync_status["progress"] = "Iniciando..."
    sync_status["done"] = 0
    try:
        n_enr = sync_endpoint("enrollment", "enrollments", OFFSET_INICIAL, "cadastro", "Matrículas")
        n_cert = sync_endpoint("certificate", "certificates", OFFSET_INICIAL_CERT, "concluido", "Certificados")
        now_str = datetime.now().isoformat()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)", (now_str,))
        conn.commit()
        conn.close()
        sync_status["last_sync"] = now_str
        sync_status["progress"] = f"Concluído — {n_enr} matrículas, {n_cert} certificados."
    except Exception as e:
        sync_status["progress"] = f"Erro geral: {e}"
    finally:
        sync_status["running"] = False


def extract_empresa(grupo_nome):
    if not grupo_nome:
        return None
    parts = grupo_nome.split(" - ")
    if len(parts) >= 2:
        empresa = parts[1].strip()
        empresa = re.sub(r'\s+\d+$', '', empresa).strip()
        return empresa.upper() if empresa else None
    return grupo_nome.strip().upper()


def mapa_email():
    """aluno_id -> email, a partir das matrículas."""
    m = {}
    for e in load_table("enrollments"):
        aid = e.get("aluno_id")
        email = e.get("aluno_email")
        if aid and email and aid not in m:
            m[aid] = email
    return m


def mapa_canal():
    """aluno_id -> 'B2B'/'B2C', a partir das matrículas (tem grupo = B2B)."""
    m = {}
    for e in load_table("enrollments"):
        aid = e.get("aluno_id")
        if aid is None:
            continue
        canal = "B2B" if e.get("grupo_nome") else "B2C"
        # B2B prevalece se aluno aparece nos dois
        if aid not in m or canal == "B2B":
            m[aid] = canal
    return m


def compute_vendas(enrollments):
    now = datetime.now()
    mes_atual = now.strftime("%Y-%m")
    mes_anterior = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    b2b = [e for e in enrollments if e.get("grupo_nome")]
    b2c = [e for e in enrollments if not e.get("grupo_nome")]

    b2b_active = [e for e in b2b if e.get("status") == 1]
    b2c_active = [e for e in b2c if e.get("status") == 1]
    total_active = len(b2b_active) + len(b2c_active)

    def novas(lst, mes):
        return len([e for e in lst if (e.get("cadastro") or "").startswith(mes)])

    vendas_mes = novas(enrollments, mes_atual)
    vendas_ant = novas(enrollments, mes_anterior)
    var_pct = round((vendas_mes - vendas_ant) / vendas_ant * 100, 1) if vendas_ant else 0

    empresa_count = defaultdict(int)
    for e in b2b_active:
        emp = extract_empresa(e.get("grupo_nome", ""))
        if emp:
            empresa_count[emp] += 1
    top10_clientes = sorted(empresa_count.items(), key=lambda x: x[1], reverse=True)[:10]

    curso_count = defaultdict(int)
    for e in enrollments:
        if e.get("status") == 1:
            curso_count[e.get("titulo_curso") or "Sem nome"] += 1
    top10_cursos = sorted(curso_count.items(), key=lambda x: x[1], reverse=True)[:10]

    trend = {}
    for i in range(6):
        mes = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m") if i else mes_atual
    # build 6 months properly
    trend = {}
    ref = now.replace(day=1)
    meses = []
    for i in range(6):
        meses.append(ref.strftime("%Y-%m"))
        ref = (ref - timedelta(days=1)).replace(day=1)
    for mes in meses:
        trend[mes] = {"b2b": 0, "b2c": 0}
    for e in b2b:
        mes = (e.get("cadastro") or "")[:7]
        if mes in trend:
            trend[mes]["b2b"] += 1
    for e in b2c:
        mes = (e.get("cadastro") or "")[:7]
        if mes in trend:
            trend[mes]["b2c"] += 1

    return {
        "resumo": {
            "total_matriculas_ativas": total_active,
            "b2b_ativas": len(b2b_active),
            "b2c_ativas": len(b2c_active),
            "pct_b2b": round(len(b2b_active) / total_active * 100, 1) if total_active else 0,
            "pct_b2c": round(len(b2c_active) / total_active * 100, 1) if total_active else 0,
            "vendas_mes": vendas_mes,
            "vendas_mes_anterior": vendas_ant,
            "var_pct": var_pct,
            "total_empresas_b2b": len(empresa_count),
        },
        "top10_clientes": [{"empresa": k, "matriculas": v} for k, v in top10_clientes],
        "top10_cursos": [{"curso": k, "matriculas": v} for k, v in top10_cursos],
        "trend_mensal": [{"mes": k, "b2b": v["b2b"], "b2c": v["b2c"]} for k, v in sorted(trend.items())],
    }


def compute_recert_lista(certificates, emails, canais):
    """Retorna lista deduplicada de recertificações nos próximos 90 dias, com email e canal."""
    now = datetime.now()
    ultimo = {}
    for c in certificates:
        aluno = c.get("aluno_id")
        curso = c.get("curso_id")
        concl = (c.get("concluido") or "")[:10]
        if aluno is None or curso is None or not concl:
            continue
        chave = (aluno, curso)
        atual = ultimo.get(chave)
        if atual is None or concl > (atual.get("concluido") or "")[:10]:
            ultimo[chave] = c

    lista = []
    for c in ultimo.values():
        concl = c.get("concluido")
        titulo = c.get("curso_titulo") or ""
        try:
            dt = datetime.strptime(str(concl)[:10], "%Y-%m-%d")
            dt_recert = dt + timedelta(days=validade_curso(titulo))
            dias = (dt_recert - now).days
            if 0 <= dias <= 90:
                aid = c.get("aluno_id")
                lista.append({
                    "aluno": c.get("aluno_nome", ""),
                    "email": emails.get(aid, ""),
                    "canal": canais.get(aid, "B2C"),
                    "curso": titulo,
                    "concluido": concl[:10] if concl else "",
                    "recertifica": dt_recert.strftime("%Y-%m-%d"),
                    "dias": dias,
                    "pdf": c.get("certificado_pdf", ""),
                })
        except:
            pass
    lista.sort(key=lambda x: x["dias"])
    return lista


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/dashboard")
def dashboard():
    if count_table("enrollments") == 0:
        return jsonify({"error": "sem_cache"}), 503

    enrollments = load_table("enrollments")
    certificates = load_table("certificates")
    emails = mapa_email()
    canais = mapa_canal()

    data = compute_vendas(enrollments)
    lista = compute_recert_lista(certificates, emails, canais)

    r30 = len([x for x in lista if x["dias"] <= 30])
    r60 = len([x for x in lista if 30 < x["dias"] <= 60])
    r90 = len([x for x in lista if 60 < x["dias"] <= 90])

    data["recert"] = {"r30": r30, "r60": r60, "r90": r90, "total": len(lista)}
    data["cache_info"] = {
        "total_matriculas": count_table("enrollments"),
        "total_certificados": count_table("certificates"),
        "ultimo_sync": get_meta("last_sync"),
    }
    return jsonify(data)


@app.route("/api/exportar")
def exportar():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    certificates = load_table("certificates")
    emails = mapa_email()
    canais = mapa_canal()
    lista = compute_recert_lista(certificates, emails, canais)

    wb = Workbook()
    ws = wb.active
    ws.title = "Recompra - Recertificacao"

    headers = ["Prazo (dias)", "Faixa", "Aluno", "E-mail", "Canal", "Curso",
               "Concluído em", "Recertifica em", "Link do Certificado"]
    ws.append(headers)

    header_fill = PatternFill(start_color="1A1D27", end_color="1A1D27", fill_type="solid")
    header_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    for item in lista:
        dias = item["dias"]
        faixa = "Urgente (0-30)" if dias <= 30 else ("Atenção (31-60)" if dias <= 60 else "Planejamento (61-90)")
        ws.append([
            dias, faixa, item["aluno"], item["email"], item["canal"],
            item["curso"], item["concluido"], item["recertifica"], item["pdf"],
        ])

    # Cores por faixa
    fill_30 = PatternFill(start_color="FDE7E7", end_color="FDE7E7", fill_type="solid")
    fill_60 = PatternFill(start_color="FEF3E2", end_color="FEF3E2", fill_type="solid")
    fill_90 = PatternFill(start_color="E7F0FD", end_color="E7F0FD", fill_type="solid")

    for row_idx in range(2, ws.max_row + 1):
        dias = ws.cell(row=row_idx, column=1).value
        fill = fill_30 if dias <= 30 else (fill_60 if dias <= 60 else fill_90)
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col)
            cell.font = Font(name="Arial", size=10)
            cell.border = border
            if col in (1, 2, 5, 7, 8):
                cell.alignment = Alignment(horizontal="center")
            ws.cell(row=row_idx, column=2).fill = fill

    widths = [12, 18, 30, 32, 8, 45, 14, 14, 50]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = w

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nome = f"recompra_recertificacao_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=nome,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/sync", methods=["POST"])
def sync():
    if sync_status["running"]:
        return jsonify({"ok": False, "msg": "Já em andamento."})
    t = threading.Thread(target=do_sync, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/sync/status")
def sync_status_route():
    return jsonify(sync_status)


if __name__ == "__main__":
    init_db()
    try:
        import openpyxl
    except ImportError:
        print("⚠  Falta a biblioteca openpyxl para exportar Excel.")
        print("   Rode: pip install openpyxl")
    print("=" * 50)
    print("Boss Dashboard — http://localhost:5000")
    print(f"Matrículas: {count_table('enrollments')} | Certificados: {count_table('certificates')}")
    print("=" * 50)
    app.run(debug=True, port=5000)
