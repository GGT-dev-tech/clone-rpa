"""
Logger estruturado usando structlog + rich para saída amigável no console
e arquivo rotativo para produção.

Uso:
    from src.core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("tarefa_iniciada", task_id=str(task.task_id), tipo=task.task_type)
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog
from rich.logging import RichHandler


def setup_logging(log_level: str = "INFO", log_dir: Path | None = None) -> None:
    """
    Configura o pipeline de logging:
    - Console: rich (colorido, formatado para humanos)
    - Arquivo: JSON rotativo (para ingestão em ferramentas de observabilidade)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [
        RichHandler(
            level=level,
            show_time=True,
            show_path=True,
            rich_tracebacks=True,
            markup=True,
        )
    ]

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_dir / "worker.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        format="%(message)s",
        datefmt="[%X]",
    )

    # Silencia bibliotecas verbosas
    for noisy_lib in ("websockets", "asyncio", "urllib3"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Retorna um logger estruturado nomeado."""
    return structlog.get_logger(name)
