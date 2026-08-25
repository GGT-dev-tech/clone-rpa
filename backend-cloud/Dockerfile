FROM python:3.11-slim AS builder

WORKDIR /app

# Instala dependências de sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala Poetry
RUN pip install --no-cache-dir poetry==1.8.3
RUN poetry config virtualenvs.create false

# Copia somente os arquivos de dependência primeiro (cache de camadas)
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root

# Copia código-fonte
COPY app/ ./app/

# ---------- Imagem final ----------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copia dependências instaladas
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/app ./app

# Usuário não-root
RUN adduser --disabled-password --no-create-home appuser
USER appuser

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
