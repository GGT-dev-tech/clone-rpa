"""
FastAPI Application — ponto de entrada principal do backend-cloud.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routes import tasks
from app.api.ws import worker_gateway
from app.core.config import get_settings
from app.core.redis import close_redis, init_redis
from app.db.session import close_db, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Gerencia recursos durante o ciclo de vida da aplicação."""
    settings = get_settings()
    logger.info("Iniciando %s v%s [%s]", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)

    await init_db()
    await init_redis()

    yield  # Aplicação em execução

    await close_redis()
    await close_db()
    logger.info("Aplicação encerrada.")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(o) for o in settings.ALLOWED_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Rotas REST ---
    app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["Tasks"])

    # --- WebSocket Worker ---
    app.include_router(worker_gateway.router, tags=["Worker Gateway"])

    # --- Handler de erros globais ---
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Erro não tratado em %s: %s", request.url, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Erro interno do servidor."},
        )

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict:
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()
