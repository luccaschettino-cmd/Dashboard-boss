# Boss Dashboard — Painel Operacional

Dashboard interno da **Boss Consultoria e Treinamentos** para acompanhamento de matrículas, certificados, fila de recertificação e disparo de e-mails automáticos para alunos com certificado vencendo.

---

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3.14 + Flask |
| Banco local | SQLite (modo WAL) |
| Frontend | HTML/CSS/JS vanilla + Chart.js |
| E-mail | SMTP Gmail (`smtp.gmail.com:587`) com senha de app |
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
EAD_TOKEN=seu_token_aqui           # Token da API da plataforma EAD
EMAIL_REMETENTE=seu@gmail.com      # Conta Gmail usada para envio
EMAIL_SENHA=sua_senha_app_aqui     # Senha de aplicativo do Gmail (não a senha normal)
```

> **Gmail:** acesse myaccount.google.com → Segurança → Verificação em duas etapas → Senhas de app. Gere uma senha para "Outro (nome personalizado)" e use no lugar da senha normal. A conta `bosstreinamentos@hotmail.com` recebe cópia (CC) de todos os envios.

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

**Deduplicação cross-ano:** a função `norma_base(titulo)` normaliza o nome do curso removendo o ano e sufixos de duração (ex: "2025", "- 3 anos", "16h"). A deduplicação usa a chave `(aluno_id, norma_base(titulo))`, garantindo que um aluno que refez o NR-33 em 2026 não apareça na fila caso o certificado de 2026 ainda esteja válido — mesmo sendo um `curso_id` diferente.

**Exclusões:** NR-35 (Trabalho em Altura) está excluído da fila de recertificação por não ter periodicidade legal definida.

**Filtro de data:** o sync completo e rápido busca registros a partir de `2023-01-01` para evitar processamento de dados muito antigos.

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
3. Envia via SMTP Gmail, com CC automático para `bosstreinamentos@hotmail.com`, e registra na tabela
4. Roda em thread background com progresso em tempo real
5. Aceita parâmetro `limite` no body JSON para enviar em lotes (ex: `{"limite": 100}`)

**Faixas de envio** (cada aluno recebe no máximo 1 e-mail por faixa por ciclo):
- Faixa 90: vence em 61–90 dias
- Faixa 60: vence em 31–60 dias
- Faixa 30: vence em até 30 dias

**Rotas relacionadas:**

| Rota | Descrição |
|------|-----------|
| `POST /api/enviar_recert` | Inicia envio em background |
| `GET /api/email/status` | Progresso atual (polling) |
| `GET /api/email/stats` | Status da fila atual: já notificados / pendentes / total na fila / último envio |
| `POST /api/enviar_recert/teste` | Envia e-mail de teste para um endereço específico (`{"email": "dest@exemplo.com"}`) |

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
| `/api/email/stats` | GET | Status da fila atual de recertificação vs e-mails já enviados |
| `/api/enviar_recert/teste` | POST | Envia e-mail de teste para endereço específico |

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
