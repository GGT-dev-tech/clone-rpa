"""
Entrypoint do Worker RPA Local.

Inicializa todos os componentes, registra signal handlers para
shutdown gracioso e inicia o loop principal de conexão WebSocket.

Para rodar:
    python -m src.main

Para empacotar como .exe:
    pyinstaller --onefile --name auto-adm-worker src/main.py
"""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

# Adiciona o diretório raiz do projeto ao path (necessário para imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automation.base_automation import ERPSession
from src.core.config import get_settings
from src.core.idempotency import IdempotencyGuard
from src.core.logger import get_logger, setup_logging
from src.dispatcher.task_dispatcher import TaskDispatcher
from src.transport.ws_client import WorkerWebSocketClient
from src.utils.file_downloader import FileDownloader


async def main() -> None:
    settings = get_settings()
    setup_logging(log_level=settings.LOG_LEVEL, log_dir=settings.LOG_DIR)

    logger = get_logger("main")
    logger.info(
        "auto-adm Worker iniciando",
        worker_id=settings.WORKER_ID,
        erp=settings.ERP_WINDOW_TITLE,
    )

    # -------- Inicialização de Componentes --------
    idempotency_guard = IdempotencyGuard(db_path=settings.STATE_DB_PATH)
    erp_session = ERPSession()
    file_downloader = FileDownloader()

    dispatcher = TaskDispatcher(
        idempotency_guard=idempotency_guard,
        erp_session=erp_session,
        file_downloader=file_downloader,
    )

    ws_client = WorkerWebSocketClient(
        on_message=lambda raw: dispatcher.dispatch(
            raw_message=raw,
            send_status=ws_client.send,  # type: ignore[arg-type]
        )
    )

    # -------- Signal Handlers para shutdown gracioso --------
    loop = asyncio.get_running_loop()

    def _shutdown_handler(sig_name: str) -> None:
        logger.info("Sinal recebido — iniciando shutdown", sinal=sig_name)
        asyncio.create_task(ws_client.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig.name: _shutdown_handler(s))
        except NotImplementedError:
            # Windows não suporta add_signal_handler plenamente
            signal.signal(sig, lambda *_: _shutdown_handler(sig.name))

    # -------- Limpeza periódica de arquivos temporários --------
    async def _cleanup_loop() -> None:
        while True:
            await asyncio.sleep(3600)  # A cada hora
            removed = file_downloader.cleanup_old_files()
            logger.info("Limpeza de temporários", arquivos_removidos=removed)

    # -------- Loop Principal --------
    try:
        await asyncio.gather(
            ws_client.run_forever(),
            _cleanup_loop(),
        )
    finally:
        idempotency_guard.close()
        logger.info("Worker encerrado.")


if __name__ == "__main__":
    asyncio.run(main())
