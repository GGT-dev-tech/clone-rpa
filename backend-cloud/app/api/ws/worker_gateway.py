"""
WebSocket Gateway — endpoint que o Worker Local mantém conectado.

Fluxo:
  1. Worker conecta via WSS com header X-Worker-API-Key
  2. API autentica e registra a conexão
  3. TaskDispatcher envia mensagens via fila Redis → WebSocket
  4. Worker envia de volta WorkerStatusUpdate
  5. API persiste o status e notifica o Frontend (via SSE / outro WS)
"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status

from app.core.config import get_settings
from app.core.redis import get_redis
from app.schemas.task import WorkerStatusUpdate, WorkerTaskMessage
from app.services.task_service import TaskService

router = APIRouter()
logger = logging.getLogger(__name__)

# Registro simples de conexões ativas (em prod: use Redis pub/sub)
_active_workers: dict[str, WebSocket] = {}


async def _authenticate_worker(websocket: WebSocket) -> bool:
    """Valida o API Key do Worker no header HTTP do handshake."""
    settings = get_settings()
    api_key = websocket.headers.get("x-worker-api-key", "")
    return api_key == settings.WORKER_API_KEY.get_secret_value()


@router.websocket("/ws/worker")
async def worker_gateway(
    websocket: WebSocket,
    task_service: TaskService = Depends(TaskService),
) -> None:
    """
    Endpoint WebSocket exclusivo para o Worker Local.
    O Worker se conecta aqui e recebe tarefas em tempo real.
    """
    settings = get_settings()

    # --- Autenticação no handshake ---
    if not await _authenticate_worker(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.warning("Worker tentou conectar com API Key inválida.")
        return

    await websocket.accept()
    worker_id = websocket.headers.get("x-worker-id", "default")
    _active_workers[worker_id] = websocket
    logger.info("Worker '%s' conectado.", worker_id)

    redis = await get_redis()

    try:
        # Cria duas corrotinas concorrentes:
        # (a) pump_tasks: lê Redis queue e envia ao Worker
        # (b) receive_updates: recebe status updates do Worker
        await asyncio.gather(
            _pump_tasks(websocket, redis, settings),
            _receive_updates(websocket, task_service),
        )
    except WebSocketDisconnect:
        logger.info("Worker '%s' desconectou.", worker_id)
    except Exception as exc:
        logger.exception("Erro no gateway do Worker '%s': %s", worker_id, exc)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        _active_workers.pop(worker_id, None)


async def _pump_tasks(websocket: WebSocket, redis, settings) -> None:
    """
    Loop: lê tarefas da fila Redis e as envia ao Worker via WebSocket.
    Usa BLPOP (blocking left pop) para eficiência — sem polling cego.
    """
    queue_key = settings.REDIS_TASK_QUEUE
    heartbeat_interval = settings.WS_HEARTBEAT_INTERVAL

    while True:
        # BLPOP com timeout para permitir heartbeat periódico
        result = await redis.blpop(queue_key, timeout=heartbeat_interval)

        if result is None:
            # Timeout — envia ping para manter a conexão viva
            await websocket.send_text(json.dumps({"type": "ping"}))
            continue

        _, raw_message = result
        try:
            task_msg = WorkerTaskMessage.model_validate_json(raw_message)
            await websocket.send_text(task_msg.to_wire())
            logger.info("Tarefa %s despachada ao Worker.", task_msg.task_id)
        except Exception as exc:
            logger.error("Falha ao despachar tarefa: %s | raw=%s", exc, raw_message)


async def _receive_updates(websocket: WebSocket, task_service: TaskService) -> None:
    """
    Loop: recebe mensagens do Worker e persiste o status no banco.
    """
    while True:
        raw = await websocket.receive_text()

        # Ignora pong / mensagens de controle
        if raw.strip() in ('{"type":"pong"}', "pong"):
            continue

        try:
            update = WorkerStatusUpdate.model_validate_json(raw)
            await task_service.update_from_worker(update)
            logger.info(
                "Task %s → status=%s (tentativa=%d)",
                update.task_id,
                update.status,
                update.attempt,
            )
        except Exception as exc:
            logger.error("Status update inválido do Worker: %s | raw=%s", exc, raw)
