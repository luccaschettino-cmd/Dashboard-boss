# Boss Treinamentos — Dashboard

## Instalação

```powershell
# 1. Entre na pasta
cd C:\Users\Jasmine\Desktop\boss_dashboard\boss_dashboard

# 2. Instale as dependências (só na primeira vez)
pip install flask requests python-dotenv

# 3. Crie o arquivo .env com seu token
copy .env.example .env
# Abra o .env no Notepad e substitua o token

# 4. Inicie o dashboard
python app.py
```

## Uso

Abra o navegador em: **http://localhost:5000**

Para parar o servidor: `Ctrl+C` no terminal.

## Estrutura do projeto

```
boss_dashboard/
├── app.py              ← backend Flask + lógica de dados
├── .env                ← token da API (NÃO compartilhe)
├── .env.example        ← modelo do .env
└── templates/
    └── index.html      ← dashboard visual
```

## O que o dashboard mostra

- **6 KPIs principais** — total ativo, B2B, B2C, empresas, novas matrículas do mês
- **Fila de vencimentos** — certificados que vencem em 30/60/90 dias
- **Tendência mensal** — comparativo B2B vs B2C dos últimos 6 meses
- **Distribuição por UF** — onde estão os alunos B2C
- **Top 10 clientes B2B** — por volume de matrículas ativas
- **Top 10 cursos** — mais matriculados

## Lógica B2B vs B2C

- **B2B** = matrícula com `grupo_nome` preenchido
- **B2C** = matrícula sem `grupo_nome`
- Nome da empresa extraído do padrão: `DD/MM - empresa - cursos - nome`
