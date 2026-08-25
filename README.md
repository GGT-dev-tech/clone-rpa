# auto-adm

> Plataforma de automação RPA para integração com ERP SSPlus legado via Worker Windows local.

## Arquitetura

```
React App (Frontend) → FastAPI/Railway (Cloud) → Redis Queue → Worker Python (Windows) → ERP SSPlus
```

## Estrutura do Monorepo

| Diretório | Descrição |
|---|---|
| `backend-cloud/` | API FastAPI + PostgreSQL + Redis (hospedada no Railway) |
| `frontend-app/` | React + Vite + Tailwind (interface do usuário) |
| `local-worker-rpa/` | Worker Python executado na máquina Windows do cliente |

## Quick Start — Desenvolvimento

### 1. Subir infraestrutura local (PostgreSQL + Redis)

```bash
docker-compose up -d postgres redis
```

### 2. Backend API

```bash
cd backend-cloud
cp .env.example .env        # Preencha as variáveis
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend-app
npm install
npm run dev                 # http://localhost:5173
```

### 4. Worker Local (máquina Windows com ERP)

```bash
cd local-worker-rpa
cp .env.example .env        # Preencha WORKER_API_KEY e API_WS_URL
poetry install
python -m src.main
```

## Build do Worker como .exe

```powershell
cd local-worker-rpa
.\build.ps1
# Saída: dist\auto-adm-worker.exe
```

## Fluxo de Tarefas

1. Usuário cria tarefa no Frontend (React)
2. Frontend chama `POST /api/v1/tasks` na API FastAPI
3. API valida, persiste no PostgreSQL e enfileira no Redis
4. Worker (conectado via WebSocket) recebe a tarefa
5. Worker verifica idempotência (SQLite local) — se já executada, faz ACK
6. Worker executa automação `pywinauto` no ERP SSPlus
7. Worker envia status de volta via WebSocket
8. API atualiza o registro no banco e notifica o Frontend

## Variáveis de Ambiente Críticas

| Variável | Onde | Descrição |
|---|---|---|
| `WORKER_API_KEY` | backend + worker | Chave compartilhada para autenticar o WebSocket |
| `DATABASE_URL` | backend | PostgreSQL connection string |
| `REDIS_URL` | backend | Redis connection string |
| `API_WS_URL` | worker | URL WSS do Gateway do Worker na Railway |
| `ERP_WINDOW_TITLE` | worker | Substring do título da janela do ERP |

## ⚠️ Importante — Adaptação ao SSPlus Real

Os títulos de controles (campos, botões, menus) nos arquivos de automação
são **placeholders genéricos** e devem ser ajustados ao SSPlus real usando:

```python
# Ferramenta de inspeção de controles Win32
from pywinauto import Desktop
app = Desktop(backend="win32")
app.inspect()               # Abre o inspetor visual
```

Ou use o **Spy++** (incluído no Visual Studio) para identificar os
`AutomationId`, `ClassName` e títulos exatos de cada controle.
