# Boss Dashboard — Painel Operacional

Dashboard interno da **Boss Consultoria e Treinamentos** para acompanhamento de matrículas, certificados, fila de recertificação e disparo de e-mails automáticos para alunos com certificado vencendo.

---

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3.14 + Flask |
| Banco local | SQLite (modo WAL) |
| Frontend | HTML/CSS/JS vanilla + Chart.js |
| E-mail | SMTP Outlook / Hotmail |
| Exportação | openpyxl (Excel) |
| Deploy | Gunicorn |

---

## Estrutura de arquivos

```
boss_dashboard/
├── app.py                  # Toda a lógica do servidor Flask
├── templates/
│   └── index.html          # Frontend single-page (HTML + CSS + JS inline)
├── static/
│   ├── logo.jpg            # Logo circular da empresa
│   └── banner.png          # Banner do cabeçalho
├── requirements.txt
├── Procfile                # Para deploy com Gunicorn
├── .env                    # Variáveis de ambiente (não versionado)
├── cache.db                # Banco SQLite local (não versionado)
└── README.md
```

---

## Variáveis de ambiente (`.env`)

```env
EAD_TOKEN=seu_token_aqui       # Token da API da plataforma EAD
EMAIL_SENHA=sua_senha_aqui     # Senha do bosstreinamentos@hotmail.com
```

> **Hotmail/Outlook:** se autenticação falhar, ative verificação em duas etapas em account.microsoft.com e gere uma **senha de aplicativo** para usar no lugar da senha normal.

---

## Como rodar localmente

```bash
pip install -r requirements.txt
python app.py
# Acesse http://localhost:5000
```

---

## API da plataforma EAD

- **Base URL:** `https://bosstreinamentos.com/api/1`
- **Auth:** header `x-auth-token: <EAD_TOKEN>`
- **Paginação:** parâmetros `offset` e `limit` (NÃO é page-based)

### Endpoints usados

| Endpoint | Tabela local | Descrição |
|----------|-------------|-----------|
| `enrollment` | `enrollments` | Matrículas |
| `certificate` | `certificates` | Certificados emitidos |
| `student` | `students` | Dados cadastrais dos alunos |
| `progress` | `progress` | Progresso por matrícula |

### Campos importantes

**enrollment:**
- `aluno_id`, `aluno_nome`, `aluno_email`
- `curso_id`, `titulo_curso`
- `cadastro` — data da matrícula *(usar para filtros de ano)*
- `expira` — prazo de acesso à plataforma (**NÃO usar para recertificação**)
- `status` — 1=ativo, 3=cancelado, 4=expirado
- `grupo_nome` — preenchido = B2B; vazio = B2C

**certificate:**
- `concluido` — data de emissão do certificado *(usar para calcular recertificação)*
- `aluno_id`, `curso_id`, `curso_titulo`

**student:**
- `aluno_id`, `nome`, `email`, `telefone`, `cpf`, `cidade`
- `uf` — nome completo do estado (ex: `"ESPÍRITO SANTO"`) → normalizado para sigla internamente

---

## Banco de dados SQLite (`cache.db`)

| Tabela | Conteúdo |
|--------|---------|
| `enrollments` | JSON de cada matrícula |
| `certificates` | JSON de cada certificado |
| `students` | JSON de cada aluno |
| `progress` | JSON de progresso por matrícula |
| `meta` | Chave/valor: `last_sync`, `last_sync_modo` |
| `emails_enviados` | Registro de e-mails disparados (deduplicação) |

Todas as tabelas de dados armazenam o JSON bruto da API em uma coluna `data TEXT`. A leitura é feita via `json.loads` em memória.

---

## Lógica de negócio

### B2B vs B2C
- **B2B:** matrícula com `grupo_nome` preenchido (empresa parceira)
- **B2C:** matrícula sem `grupo_nome` (aluno avulso)
- Empresa extraída de `grupo_nome` com `split(" - ")[1]`

### Recertificação
Calculada como `certificate.concluido + validade legal da norma`:

| Curso | Validade |
|-------|---------|
| NR-33 | 1 ano (365 dias) |
| NR-20 básico / intermediário I | 3 anos (1095 dias) |
| NR-20 intermediário II/III e avançado | 2 anos (730 dias) |
| Direção Defensiva | 3 anos (1095 dias) |
| Todos os demais | 2 anos (730 dias) |

> ⚠️ **Nunca usar o campo `expira` do enrollment para calcular recertificação.** Ele representa o prazo de acesso à plataforma EAD (definido comercialmente), não a validade legal do certificado.

Deduplicação: por `(aluno_id, curso_id)`, mantendo o certificado com `concluido` mais recente.

### Filtro de ano
- Ano atual → KPIs mensais + tendência mensal
- Ano passado → totais anuais + comparativo com ano anterior
- Totais de ativos **não são filtrados** por ano (representam estado atual)

---

## Sincronização de dados

Dois modos acessíveis pelo header do dashboard:

| Botão | Modo | Comportamento |
|-------|------|--------------|
| ⟳ Atualizar | `rapido` | Estima offset pelo COUNT atual, busca últimas ~10 páginas, faz upsert |
| ↺ Completo | `completo` | DELETE + rebusca tudo do offset 0 |

O sync roda em **thread background** — o dashboard continua acessível durante o processo.

**Volumes aproximados:**
- Matrículas: ~44k
- Certificados: ~44k
- Alunos: ~32k

---

## Disparo de e-mails

Rota: `POST /api/enviar_recert`

**Fluxo:**
1. Monta fila de recertificação (certificados vencendo em até 90 dias)
2. Para cada aluno, verifica tabela `emails_enviados`:
   - Se já recebeu na mesma **faixa** nos últimos 25 dias → pula
3. Envia via SMTP Outlook e registra na tabela
4. Roda em thread background com progresso em tempo real

**Faixas de envio** (cada aluno recebe no máximo 1 e-mail por faixa por ciclo):
- Faixa 90: vence em 61–90 dias
- Faixa 60: vence em 31–60 dias
- Faixa 30: vence em até 30 dias

**Rotas relacionadas:**

| Rota | Descrição |
|------|-----------|
| `POST /api/enviar_recert` | Inicia envio em background |
| `GET /api/email/status` | Progresso atual (polling) |
| `GET /api/email/stats` | Estatísticas acumuladas (alunos notificados, total de envios) |

---

## Rotas da API interna

| Rota | Método | Descrição |
|------|--------|-----------|
| `/` | GET | Dashboard (HTML) |
| `/api/dashboard?ano=XXXX` | GET | Todos os dados do painel |
| `/api/sync` | POST | Inicia sync (`{"modo": "rapido" ou "completo"}`) |
| `/api/sync/status` | GET | Status do sync em andamento |
| `/api/exportar` | GET | Download da lista de recertificação em Excel |
| `/api/enviar_recert` | POST | Inicia disparo de e-mails |
| `/api/email/status` | GET | Progresso do envio em andamento |
| `/api/email/stats` | GET | Estatísticas de e-mails enviados |

---

## Frontend

Single-page em `templates/index.html` — sem framework, tudo inline.

**Seções do dashboard:**
1. **Como estamos indo?** — KPI de vendas, split B2B/B2C, ativos totais
2. **O que fazer agora?** — Fila de recertificação (30/60/90 dias) + botão Excel + disparo de e-mails
3. **Fidelidade dos clientes** — Novos vs. recorrentes no ano selecionado
4. **De onde vem nosso dinheiro?** — Gráfico de tendência mensal, mapa geográfico B2C, top 10 cursos e empresas

**Tema:** claro/escuro com toggle no header, persistido via `localStorage`.

**Design:** dark `#0f1117`, accent `#f5a623`, B2B `#10b981`, B2C `#3b82f6`.

---

## Convenção de branches (Git)

- `main` — produção, só recebe merge de PRs
- `feature/nome` — branch por funcionalidade, abrir PR para `main`

```bash
# Iniciar nova feature
git checkout main
git pull
git checkout -b feature/nome-da-feature

# Ao terminar
git push -u origin feature/nome-da-feature
# Abrir PR no GitHub
```
