"""
WebSocket Client — mantém a conexão persistente com a API na Railway.

Características:
  - Reconexão automática com backoff exponencial + jitter
  - Heartbeat (pong em resposta ao ping do servidor)
  - Enfileiramento de mensagens de saída durante desconexões
  - Limite configurável de tentativas de reconexão
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)

MessageHandler = Callable[[str], Awaitable[None]]


class WorkerWebSocketClient:
    """
    Cliente WebSocket com reconexão automática.

    Uso típico:
        client = WorkerWebSocketClient(on_message=dispatcher.dispatch)
        await client.run_forever()
    """

    def __init__(self, on_message: MessageHandler) -> None:
        self._on_message = on_message
        self._settings = get_settings()
        self._outbound_queue: asyncio.Queue[str] = asyncio.Queue()
        self._websocket = None
        self._running = False

    async def send(self, data: dict) -> None:
        """
        Enfileira uma mensagem para envio ao servidor.
        Thread-safe — pode ser chamado de qualquer corrotina.
        """
        await self._outbound_queue.put(json.dumps(data))

    async def run_forever(self) -> None:
        """
        Loop principal de conexão — reconecta automaticamente.
        Só para quando self._running é False.
        """
        self._running = True
        settings = self._settings
        attempt = 0
        wait = settings.WS_RECONNECT_INITIAL_WAIT

        while self._running:
            attempt += 1
            max_attempts = settings.WS_RECONNECT_MAX_ATTEMPTS

            if max_attempts > 0 and attempt > max_attempts:
                logger.error(
                    "Número máximo de reconexões atingido",
                    max_attempts=max_attempts,
                )
                break

            try:
                logger.info("Conectando ao servidor", url=str(settings.API_WS_URL), attempt=attempt)

                headers = {
                    "X-Worker-API-Key": settings.WORKER_API_KEY.get_secret_value(),
                    "X-Worker-ID": settings.WORKER_ID,
                }

                async with websockets.connect(
                    str(settings.API_WS_URL),
                    additional_headers=headers,
                    ping_interval=None,  # Gerenciamos heartbeat manualmente
                    close_timeout=10,
                ) as ws:
                    self._websocket = ws
                    attempt = 0  # Reset da contagem em conexão bem-sucedida
                    wait = settings.WS_RECONNECT_INITIAL_WAIT
                    logger.info("Conexão WebSocket estabelecida")

                    await asyncio.gather(
                        self._receive_loop(ws),
                        self._send_loop(ws),
                    )

            except ConnectionClosed as exc:
                logger.warning(
                    "Conexão fechada",
                    code=exc.rcvd.code if exc.rcvd else None,
                    reason=exc.rcvd.reason if exc.rcvd else None,
                )
            except WebSocketException as exc:
                logger.warning("WebSocket error", error=str(exc))
            except OSError as exc:
                logger.warning("Erro de rede", error=str(exc))
            except Exception as exc:
                logger.exception("Erro inesperado no WebSocket", error=str(exc))
            finally:
                self._websocket = None

            if not self._running:
                break

            # Backoff exponencial com jitter
            jitter = random.uniform(0, wait * 0.2)
            sleep_time = min(wait + jitter, settings.WS_RECONNECT_MAX_WAIT)
            logger.info(
                "Aguardando antes de reconectar",
                seconds=round(sleep_time, 1),
                next_attempt=attempt + 1,
            )
            await asyncio.sleep(sleep_time)
            wait = min(wait * 2, settings.WS_RECONNECT_MAX_WAIT)

    async def _receive_loop(self, ws) -> None:
        """Recebe mensagens do servidor e as despacha para o handler."""
        async for raw in ws:
            logger.debug("Mensagem recebida", size=len(raw))

            # Responde a ping do servidor
            if raw.strip() == '{"type":"ping"}':
                await ws.send('{"type":"pong"}')
                continue

            try:
                await self._on_message(raw)
            except Exception as exc:
                logger.exception("Erro no handler de mensagem", error=str(exc))

    async def _send_loop(self, ws) -> None:
        """Drena a fila de saída e envia mensagens ao servidor."""
        while True:
            try:
                message = await asyncio.wait_for(
                    self._outbound_queue.get(),
                    timeout=1.0,
                )
                await ws.send(message)
                self._outbound_queue.task_done()
            except asyncio.TimeoutError:
                continue  # Nada na fila — itera
            except Exception as exc:
                logger.error("Erro ao enviar mensagem", error=str(exc))
                raise  # Propaga para fechar a conexão e reconectar

    async def stop(self) -> None:
        """Para o loop de reconexão graciosamente."""
        self._running = False
        if self._websocket:
            await self._websocket.close()
