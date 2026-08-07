from flask import Flask, render_template, jsonify, send_file, request
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


VALIDADE_NR = [
    ("nr 33", 365), ("nr 20 intermedi", 730), ("nr 20 avanc", 730),
    ("nr 20 basic", 1095), ("nr 20", 1095), ("nr 35", 730), ("nr 10", 730),
    ("nr 12", 730), ("nr 06", 730), ("nr 05", 730), ("nr 18", 730),
    ("nr 37", 730), ("nr 34", 730), ("direção defensiva", 1095),
    ("direcao defensiva", 1095), ("primeiros socorros", 730), ("cbasi", 730),
]
VALIDADE_PADRAO = 730

UF_SIGLAS = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPÁ": "AP", "AMAZONAS": "AM",
    "BAHIA": "BA", "CEARÁ": "CE", "DISTRITO FEDERAL": "DF",
    "ESPÍRITO SANTO": "ES", "GOIÁS": "GO", "MARANHÃO": "MA",
    "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS", "MINAS GERAIS": "MG",
    "PARÁ": "PA", "PARAÍBA": "PB", "PARANÁ": "PR", "PERNAMBUCO": "PE",
    "PIAUÍ": "PI", "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS", "RONDÔNIA": "RO", "RORAIMA": "RR",
    "SANTA CATARINA": "SC", "SÃO PAULO": "SP", "SERGIPE": "SE",
    "TOCANTINS": "TO",
}


def validade_curso(titulo):
    if not titulo:
        return VALIDADE_PADRAO
    t = titulo.lower()
    for padrao, dias in VALIDADE_NR:
        if padrao in t:
            return dias
    return VALIDADE_PADRAO


def normalizar_uf(uf_raw):
    if not uf_raw:
        return ""
    v = uf_raw.strip().upper()
    if len(v) == 2:
        return v
    return UF_SIGLAS.get(v, v[:2] if len(v) >= 2 else v)


sync_status = {"running": False, "progress": "", "last_sync": None, "total": 0, "done": 0}
_table_cache: dict = {}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS enrollments (id INTEGER PRIMARY KEY, data TEXT NOT NULL, synced_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS certificates (id INTEGER PRIMARY KEY, data TEXT NOT NULL, synced_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY, data TEXT NOT NULL, synced_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS progress (id INTEGER PRIMARY KEY, data TEXT NOT NULL, synced_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


init_db()


def get_meta(key):
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        app.logger.warning("get_meta(%s) falhou: %s", key, e)
        return None


def count_table(table):
    try:
        conn = sqlite3.connect(DB_PATH)
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return n
    except Exception as e:
        app.logger.warning("count_table(%s) falhou: %s", table, e)
        return 0


def load_table(table):
    if table not in _table_cache:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(f"SELECT data FROM {table}").fetchall()
        conn.close()
        _table_cache[table] = [json.loads(r[0]) for r in rows]
    return _table_cache[table]


def sync_endpoint(endpoint, table, label, id_field=None):
    """Busca endpoint paginado e salva tudo no SQLite sem filtro de data."""
    LIMIT = 200
    offset = 0
    total_salvos = 0
    total_lidos = 0
    conn = sqlite3.connect(DB_PATH)
    try:
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
                now_str = datetime.now().isoformat()
                batch = []
                for i, e in enumerate(items):
                    if id_field:
                        row_id = e.get(id_field) or f"o{offset}_{i}"
                    else:
                        row_id = e.get("id") or e.get("matricula_id") or e.get("aluno_id") or f"o{offset}_{i}"
                    batch.append((row_id, json.dumps(e), now_str))
                c.executemany(f"INSERT OR REPLACE INTO {table} (id, data, synced_at) VALUES (?,?,?)", batch)
                conn.commit()
                total_salvos += len(items)
                sync_status["progress"] = f"{label}: {total_lidos} lidos, {total_salvos} salvos"
                sync_status["done"] = total_salvos
                if len(items) < LIMIT:
                    break
                offset += LIMIT
            except requests.RequestException:
                sync_status["progress"] = f"{label}: erro na requisição (offset {offset})"
                break
            except Exception as e:
                sync_status["progress"] = f"{label} erro offset {offset}: {type(e).__name__}"
                app.logger.exception("sync_endpoint %s offset %d", label, offset)
                break
    finally:
        conn.close()
    return total_salvos


def sync_endpoint_rapido(endpoint, table, label, paginas=10, id_field=None):
    """Busca apenas as últimas `paginas` páginas e faz upsert sem apagar o histórico."""
    LIMIT = 200
    total_no_banco = count_table(table)
    offset_inicial = max(0, total_no_banco - paginas * LIMIT)

    total_lidos = 0
    total_salvos = 0
    offset = offset_inicial
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
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
                now_str = datetime.now().isoformat()
                batch = []
                for i, e in enumerate(items):
                    if id_field:
                        row_id = e.get(id_field) or f"o{offset}_{i}"
                    else:
                        row_id = e.get("id") or e.get("matricula_id") or e.get("aluno_id") or f"o{offset}_{i}"
                    batch.append((row_id, json.dumps(e), now_str))
                c.executemany(f"INSERT OR REPLACE INTO {table} (id, data, synced_at) VALUES (?,?,?)", batch)
                conn.commit()
                total_salvos += len(items)
                sync_status["progress"] = f"{label} (rápido): {total_lidos} verificados, {total_salvos} atualizados"
                sync_status["done"] = total_salvos
                if len(items) < LIMIT:
                    break
                offset += LIMIT
            except requests.RequestException:
                sync_status["progress"] = f"{label}: erro na requisição (offset {offset})"
                break
            except Exception as e:
                sync_status["progress"] = f"{label} erro offset {offset}: {type(e).__name__}"
                app.logger.exception("sync_endpoint_rapido %s offset %d", label, offset)
                break
    finally:
        conn.close()
    return total_salvos


def _salvar_meta_sync(now_str, modo):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)", (now_str,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync_modo', ?)", (modo,))
    conn.commit()
    conn.close()


def do_sync(modo="completo"):
    global sync_status
    sync_status["running"] = True
    sync_status["progress"] = "Iniciando..."
    sync_status["done"] = 0
    try:
        if modo == "rapido":
            n_enr = sync_endpoint_rapido("enrollment", "enrollments", "Matrículas", paginas=10)
            n_cert = sync_endpoint_rapido("certificate", "certificates", "Certificados", paginas=10)
            n_stu = sync_endpoint_rapido("student", "students", "Alunos", paginas=3, id_field="aluno_id")
            n_prog = sync_endpoint_rapido("progress", "progress", "Progresso", paginas=10, id_field="matricula_id")
            msg = f"Rápido — {n_enr} matrículas, {n_cert} certificados, {n_stu} alunos, {n_prog} progresso."
        else:
            n_enr = sync_endpoint("enrollment", "enrollments", "Matrículas")
            n_cert = sync_endpoint("certificate", "certificates", "Certificados")
            n_stu = sync_endpoint("student", "students", "Alunos", id_field="aluno_id")
            n_prog = sync_endpoint("progress", "progress", "Progresso", id_field="matricula_id")
            msg = f"Completo — {n_enr} matrículas, {n_cert} certificados, {n_stu} alunos, {n_prog} progresso."

        now_str = datetime.now().isoformat()
        _salvar_meta_sync(now_str, modo)
        sync_status["last_sync"] = now_str
        sync_status["progress"] = msg
        _table_cache.clear()
    except Exception as e:
        sync_status["progress"] = f"Erro geral: {type(e).__name__}"
        app.logger.exception("do_sync falhou")
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


def mapa_student():
    """aluno_id -> dict do student (telefone, cpf, cidade, uf, ultimo_acesso)."""
    m = {}
    for s in load_table("students"):
        aid = s.get("aluno_id")
        if aid is not None:
            m[aid] = s
    return m


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
    """aluno_id -> 'B2B'/'B2C'. B2B prevalece se aluno aparece nos dois."""
    m = {}
    for e in load_table("enrollments"):
        aid = e.get("aluno_id")
        if aid is None:
            continue
        canal = "B2B" if e.get("grupo_nome") else "B2C"
        if aid not in m or canal == "B2B":
            m[aid] = canal
    return m


def mapa_empresa():
    """aluno_id -> nome da empresa (B2B apenas, último grupo encontrado)."""
    m = {}
    for e in load_table("enrollments"):
        aid = e.get("aluno_id")
        if aid and e.get("grupo_nome"):
            emp = extract_empresa(e["grupo_nome"])
            if emp:
                m[aid] = emp
    return m


def compute_geo(enrollments, students_map):
    """Distribuição de alunos B2C ativos por UF (sigla)."""
    b2c_ativos_ids = {
        e.get("aluno_id")
        for e in enrollments
        if e.get("status") == 1 and not e.get("grupo_nome") and e.get("aluno_id") is not None
    }
    contagem = defaultdict(int)
    for aid in b2c_ativos_ids:
        s = students_map.get(aid)
        if s:
            sigla = normalizar_uf(s.get("uf", ""))
            if sigla:
                contagem[sigla] += 1
    return sorted(contagem.items(), key=lambda x: x[1], reverse=True)


def anos_disponiveis(enrollments):
    anos = sorted({
        int((e.get("cadastro") or "")[:4])
        for e in enrollments
        if (e.get("cadastro") or "")[:4].isdigit()
    })
    return anos


def compute_vendas(enrollments, ano):
    now = datetime.now()
    ano_atual = now.year
    mes_atual = now.strftime("%Y-%m")
    ano_str = str(ano)

    b2b = [e for e in enrollments if e.get("grupo_nome")]
    b2c = [e for e in enrollments if not e.get("grupo_nome")]

    # Totais ativos são sempre o estado atual (sem filtro de ano)
    b2b_active = [e for e in b2b if e.get("status") == 1]
    b2c_active = [e for e in b2c if e.get("status") == 1]
    total_active = len(b2b_active) + len(b2c_active)

    empresa_count_ativas = defaultdict(int)
    for e in b2b_active:
        emp = extract_empresa(e.get("grupo_nome", ""))
        if emp:
            empresa_count_ativas[emp] += 1

    # Filtrados pelo ano selecionado para tendência, top10 e KPI de vendas
    b2b_ano = [e for e in b2b if (e.get("cadastro") or "").startswith(ano_str)]
    b2c_ano = [e for e in b2c if (e.get("cadastro") or "").startswith(ano_str)]
    enr_ano = [e for e in enrollments if (e.get("cadastro") or "").startswith(ano_str)]

    # KPI de vendas: mês a mês se ano atual, anual vs. anterior se ano passado
    if ano == ano_atual:
        mes_anterior = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        vendas_kpi = len([e for e in enr_ano if (e.get("cadastro") or "").startswith(mes_atual)])
        vendas_ant = len([e for e in enrollments if (e.get("cadastro") or "").startswith(mes_anterior)])
        kpi_label = "Vendas este mês"
        kpi_compare = "vs mês passado"
    else:
        ano_ant_str = str(ano - 1)
        vendas_kpi = len(enr_ano)
        vendas_ant = len([e for e in enrollments if (e.get("cadastro") or "").startswith(ano_ant_str)])
        kpi_label = f"Vendas em {ano}"
        kpi_compare = f"vs {ano - 1}"

    var_pct = round((vendas_kpi - vendas_ant) / vendas_ant * 100, 1) if vendas_ant else 0

    # Top 10 pelo ano selecionado
    empresa_count_ano = defaultdict(int)
    for e in b2b_ano:
        emp = extract_empresa(e.get("grupo_nome", ""))
        if emp:
            empresa_count_ano[emp] += 1
    top10_clientes = sorted(empresa_count_ano.items(), key=lambda x: x[1], reverse=True)[:10]

    curso_count = defaultdict(int)
    for e in enr_ano:
        curso_count[e.get("titulo_curso") or "Sem nome"] += 1
    top10_cursos = sorted(curso_count.items(), key=lambda x: x[1], reverse=True)[:10]

    # Trend: todos os 12 meses do ano selecionado
    trend = {f"{ano_str}-{m:02d}": {"b2b": 0, "b2c": 0} for m in range(1, 13)}
    for e in b2b_ano:
        mes = (e.get("cadastro") or "")[:7]
        if mes in trend:
            trend[mes]["b2b"] += 1
    for e in b2c_ano:
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
            "vendas_kpi": vendas_kpi,
            "vendas_ant": vendas_ant,
            "var_pct": var_pct,
            "kpi_label": kpi_label,
            "kpi_compare": kpi_compare,
            "total_empresas_b2b": len(empresa_count_ativas),
            "mes_atual": mes_atual,
            "ano": ano,
        },
        "top10_clientes": [{"empresa": k, "matriculas": v} for k, v in top10_clientes],
        "top10_cursos": [{"curso": k, "matriculas": v} for k, v in top10_cursos],
        "trend_mensal": [
            {"mes": k, "b2b": v["b2b"], "b2c": v["b2c"], "atual": k == mes_atual}
            for k, v in sorted(trend.items())
        ],
    }


def compute_funil(progress_data):
    """Conta alunos por situação de progresso: Concluído / Em Andamento / Não Iniciado."""
    counts = {"Concluído": 0, "Em Andamento": 0, "Não Iniciado": 0}
    for p in progress_data:
        s = p.get("situacao") or "Não Iniciado"
        if s in counts:
            counts[s] += 1
        else:
            counts["Não Iniciado"] += 1
    total = sum(counts.values())
    return {
        "concluido": counts["Concluído"],
        "andamento": counts["Em Andamento"],
        "nao_iniciado": counts["Não Iniciado"],
        "total": total,
    }


def compute_novos_recorrentes(enrollments, ano):
    """Para o ano selecionado: quantos alunos únicos compram pela 1ª vez vs. já eram clientes."""
    ano_str = str(ano)
    primeiro_ano = {}
    for e in enrollments:
        aid = e.get("aluno_id")
        ano_e = (e.get("cadastro") or "")[:4]
        if aid and ano_e.isdigit():
            a = int(ano_e)
            if aid not in primeiro_ano or a < primeiro_ano[aid]:
                primeiro_ano[aid] = a

    alunos_no_ano = set()
    for e in enrollments:
        if (e.get("cadastro") or "").startswith(ano_str):
            aid = e.get("aluno_id")
            if aid:
                alunos_no_ano.add(aid)

    novos = sum(1 for aid in alunos_no_ano if primeiro_ano.get(aid) == ano)
    recorrentes = len(alunos_no_ano) - novos
    total = len(alunos_no_ano)
    return {
        "novos": novos,
        "recorrentes": recorrentes,
        "total": total,
        "pct_novos": round(novos / total * 100, 1) if total else 0,
        "pct_recorrentes": round(recorrentes / total * 100, 1) if total else 0,
    }


def compute_conclusao_cursos(enrollments, certificates, ano, min_enrolled=5):
    """Taxa de conclusão por curso para o ano selecionado (alunos com cert / alunos matriculados)."""
    ano_str = str(ano)

    enrolled = defaultdict(set)
    for e in enrollments:
        if (e.get("cadastro") or "").startswith(ano_str):
            titulo = e.get("titulo_curso") or "Sem nome"
            aid = e.get("aluno_id")
            if aid:
                enrolled[titulo].add(aid)

    certified = defaultdict(set)
    for c in certificates:
        titulo = c.get("curso_titulo") or "Sem nome"
        aid = c.get("aluno_id")
        if aid:
            certified[titulo].add(aid)

    result = []
    for titulo, alunos in enrolled.items():
        if len(alunos) < min_enrolled:
            continue
        cert_count = len(certified.get(titulo, set()) & alunos)
        taxa = round(cert_count / len(alunos) * 100, 1)
        result.append({
            "curso": titulo,
            "matriculados": len(alunos),
            "certificados": cert_count,
            "taxa": taxa,
        })

    result.sort(key=lambda x: x["matriculados"], reverse=True)
    return result[:15]


def compute_recert_lista(certificates, students_map, emails, canais, empresas):
    """Lista deduplicada de recertificações nos próximos 90 dias, enriquecida com dados do aluno."""
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
                s = students_map.get(aid, {})
                lista.append({
                    "aluno": c.get("aluno_nome", ""),
                    "email": emails.get(aid, ""),
                    "telefone": s.get("telefone", ""),
                    "cpf": s.get("cpf", ""),
                    "cidade": s.get("cidade", ""),
                    "uf": normalizar_uf(s.get("uf", "")),
                    "canal": canais.get(aid, "B2C"),
                    "empresa": empresas.get(aid, "") if canais.get(aid) == "B2B" else "",
                    "curso": titulo,
                    "nota": c.get("media_final", ""),
                    "concluido": concl[:10] if concl else "",
                    "recertifica": dt_recert.strftime("%Y-%m-%d"),
                    "dias": dias,
                    "ultimo_acesso": (s.get("ultimo_acesso") or "")[:10],
                    "pdf": c.get("certificado_pdf", ""),
                })
        except (ValueError, TypeError, KeyError):
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
    students_map = mapa_student()
    emails = mapa_email()
    canais = mapa_canal()
    empresas = mapa_empresa()

    ano_atual = datetime.now().year
    try:
        ano = int(request.args.get("ano", ano_atual))
    except (ValueError, TypeError):
        ano = ano_atual

    progress_data = load_table("progress")

    data = compute_vendas(enrollments, ano)
    lista = compute_recert_lista(certificates, students_map, emails, canais, empresas)

    r30 = len([x for x in lista if x["dias"] <= 30])
    r60 = len([x for x in lista if 30 < x["dias"] <= 60])
    r90 = len([x for x in lista if 60 < x["dias"] <= 90])

    geo = compute_geo(enrollments, students_map)

    data["recert"] = {"r30": r30, "r60": r60, "r90": r90, "total": len(lista)}
    data["geo"] = [{"uf": k, "total": v} for k, v in geo[:15]]
    data["funil"] = compute_funil(progress_data)
    data["novos_recorrentes"] = compute_novos_recorrentes(enrollments, ano)
    data["conclusao_cursos"] = compute_conclusao_cursos(enrollments, certificates, ano)
    data["cache_info"] = {
        "total_matriculas": count_table("enrollments"),
        "total_certificados": count_table("certificates"),
        "total_alunos": count_table("students"),
        "ultimo_sync": get_meta("last_sync"),
        "ultimo_sync_modo": get_meta("last_sync_modo") or "completo",
        "anos": anos_disponiveis(enrollments),
    }
    return jsonify(data)


@app.route("/api/exportar")
def exportar():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    certificates = load_table("certificates")
    students_map = mapa_student()
    emails = mapa_email()
    canais = mapa_canal()
    empresas = mapa_empresa()
    lista = compute_recert_lista(certificates, students_map, emails, canais, empresas)

    wb = Workbook()
    ws = wb.active
    ws.title = "Recompra - Recertificacao"

    headers = [
        "Prazo (dias)", "Faixa", "Aluno", "E-mail", "Telefone", "CPF",
        "Cidade", "UF", "Canal", "Empresa", "Curso", "Nota Final",
        "Concluído em", "Recertifica em", "Último Acesso", "Link do Certificado",
    ]
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

    fill_30 = PatternFill(start_color="FDE7E7", end_color="FDE7E7", fill_type="solid")
    fill_60 = PatternFill(start_color="FEF3E2", end_color="FEF3E2", fill_type="solid")
    fill_90 = PatternFill(start_color="E7F0FD", end_color="E7F0FD", fill_type="solid")

    # Colunas centralizadas
    cols_center = {1, 2, 8, 9, 12, 13, 14, 15}

    for item in lista:
        dias = item["dias"]
        faixa = "Urgente (0-30)" if dias <= 30 else ("Atenção (31-60)" if dias <= 60 else "Planejamento (61-90)")
        fill = fill_30 if dias <= 30 else (fill_60 if dias <= 60 else fill_90)
        row_data = [
            dias, faixa, item["aluno"], item["email"], item["telefone"], item["cpf"],
            item["cidade"], item["uf"], item["canal"], item["empresa"], item["curso"],
            item["nota"], item["concluido"], item["recertifica"], item["ultimo_acesso"], item["pdf"],
        ]
        ws.append(row_data)
        row_idx = ws.max_row
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col)
            cell.font = Font(name="Arial", size=10)
            cell.border = border
            if col in cols_center:
                cell.alignment = Alignment(horizontal="center")
            if col == 2:
                cell.fill = fill

    widths = [12, 18, 30, 32, 16, 15, 18, 6, 8, 30, 45, 10, 14, 14, 14, 50]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions

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
    modo = (request.get_json(silent=True) or {}).get("modo", "rapido")
    if modo not in ("rapido", "completo"):
        modo = "rapido"
    t = threading.Thread(target=do_sync, args=(modo,), daemon=True)
    t.start()
    return jsonify({"ok": True, "modo": modo})


@app.route("/api/sync/status")
def sync_status_route():
    return jsonify(sync_status)


if __name__ == "__main__":
    try:
        import openpyxl
    except ImportError:
        print("⚠  Falta a biblioteca openpyxl. Rode: pip install openpyxl")
    print("=" * 50)
    print("Boss Dashboard — http://localhost:5000")
    print(f"Matrículas: {count_table('enrollments')} | Certificados: {count_table('certificates')} | Alunos: {count_table('students')} | Progresso: {count_table('progress')}")
    print("=" * 50)
    app.run(debug=True, port=5000)
