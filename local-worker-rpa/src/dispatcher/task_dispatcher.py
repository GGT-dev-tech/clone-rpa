"""
Task Dispatcher — recebe mensagens do WebSocket e roteia para
a automação correta, gerenciando o ciclo de vida completo da task:

  1. Verifica idempotência (SQLite local)
  2. Notifica API: PROCESSING
  3. Executa automação RPA
  4. Notifica API: SUCCESS ou FAILED
  5. Persiste resultado no SQLite
"""
from __future__ import annotations

import asyncio
from typing import Callable, Awaitable

from src.automation.base_automation import ERPSession
from src.automation.contas_pagar import BaixaContasPagarInput, ContasPagarAutomation
from src.automation.ordem_servico import ObservacaoOSInput, OrdemServicoAutomation
from src.core.config import get_settings
from src.core.idempotency import IdempotencyGuard
from src.core.logger import get_logger
from src.utils.file_downloader import FileDownloader
from src.utils.retry import RPAError
from src.utils.screenshot import capture_screenshot, screenshot_to_base64

# Re-use schemas from shared location (copy or install as package)
# Aqui importamos localmente para manter o worker independente:
import json
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

logger = get_logger(__name__)


class TaskType(StrEnum):
    BAIXA_CONTAS_PAGAR = "BAIXA_CONTAS_PAGAR"
    OBSERVACAO_OS = "OBSERVACAO_OS"


@dataclass
class IncomingTask:
    task_id: UUID
    task_type: TaskType
    idempotency_key: str
    payload: dict

    @classmethod
    def from_json(cls, raw: str) -> "IncomingTask":
        data = json.loads(raw)
        return cls(
            task_id=UUID(data["task_id"]),
            task_type=TaskType(data["task_type"]),
            idempotency_key=data["idempotency_key"],
            payload=data["payload"],
        )


# Tipo para o callback de envio de status de volta ao WS
StatusCallback = Callable[[dict], Awaitable[None]]


class TaskDispatcher:
    """
    Roteador e executor de tarefas RPA.
    Stateless por design — todo estado é mantido pelo IdempotencyGuard.
    """

    def __init__(
        self,
        idempotency_guard: IdempotencyGuard,
        erp_session: ERPSession,
        file_downloader: FileDownloader,
    ) -> None:
        self._guard = idempotency_guard
        self._erp_session = erp_session
        self._downloader = file_downloader
        self._settings = get_settings()

    async def dispatch(self, raw_message: str, send_status: StatusCallback) -> None:
        """
        Ponto de entrada principal — chamado para cada mensagem recebida do WS.
        Executa em thread pool para não bloquear o loop asyncio com o pywinauto síncrono.
        """
        # Ignora ping/pong
        if raw_message.strip() in ('{"type":"ping"}', "ping"):
            return

        try:
            task = IncomingTask.from_json(raw_message)
        except Exception as exc:
            logger.error("Mensagem inválida recebida", error=str(exc), raw=raw_message[:200])
            return

        # -------- GUARD DE IDEMPOTÊNCIA --------
        if self._guard.is_already_executed(task.task_id):
            logger.info(
                "Tarefa já executada — ACK sem reprocessar",
                task_id=str(task.task_id),
            )
            await send_status({
                "task_id": str(task.task_id),
                "status": "SUCCESS",
                "attempt": 0,
                "error": None,
            })
            return

        # -------- MARCA COMO PROCESSANDO --------
        attempt = (self._guard.get_record(task.task_id)
                   and self._guard.get_record(task.task_id).attempt_count or 0) + 1
        self._guard.mark_processing(task.task_id, attempt)

        await send_status({
            "task_id": str(task.task_id),
            "status": "PROCESSING",
            "attempt": attempt,
            "error": None,
        })

        # -------- EXECUTA EM THREAD POOL --------
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                self._execute_sync,
                task,
            )

            self._guard.mark_success(task.task_id, attempt)
            await send_status({
                "task_id": str(task.task_id),
                "status": "SUCCESS",
                "attempt": attempt,
                "error": None,
            })
            logger.info("Tarefa executada com sucesso", task_id=str(task.task_id), result=result)

        except RPAError as exc:
            error_msg = str(exc)
            self._guard.mark_failed(task.task_id, attempt, error_msg)

            # Captura screenshot para diagnóstico
            screenshot_b64: str | None = None
            try:
                path = capture_screenshot(prefix=f"fail_{task.task_id}")
                screenshot_b64 = screenshot_to_base64(path)
            except Exception:
                pass

            await send_status({
                "task_id": str(task.task_id),
                "status": "FAILED",
                "attempt": attempt,
                "error": error_msg,
                "screenshot_b64": screenshot_b64,
            })
            logger.error(
                "Tarefa falhou após RPA error",
                task_id=str(task.task_id),
                error=error_msg,
            )

        except Exception as exc:
            error_msg = f"Erro inesperado: {exc}"
            self._guard.mark_failed(task.task_id, attempt, error_msg)
            await send_status({
                "task_id": str(task.task_id),
                "status": "FAILED",
                "attempt": attempt,
                "error": error_msg,
            })
            logger.exception("Erro inesperado no dispatch", task_id=str(task.task_id))

    def _execute_sync(self, task: IncomingTask) -> dict:
        """
        Executa a automação de forma síncrona (chamado via run_in_executor).
        pywinauto não é async-safe — deve rodar fora do event loop.
        """
        match task.task_type:
            case TaskType.BAIXA_CONTAS_PAGAR:
                automation = ContasPagarAutomation(self._erp_session)
                input_data = BaixaContasPagarInput(**task.payload)
                return automation.execute(
                    duplicata_id=input_data.duplicata_id,
                    data_pagamento=input_data.data_pagamento,
                    valor=input_data.valor,
                    conta=input_data.conta,
                    observacao=input_data.observacao,
                )

            case TaskType.OBSERVACAO_OS:
                automation = OrdemServicoAutomation(self._erp_session, self._downloader)
                input_data = ObservacaoOSInput(**task.payload)
                return automation.execute(
                    os_id=input_data.os_id,
                    texto_observacao=input_data.texto_observacao,
                    anexo_urls=input_data.anexo_urls,
                )

            case _:
                raise ValueError(f"TaskType desconhecido: {task.task_type}")
