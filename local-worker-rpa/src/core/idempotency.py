"""
Idempotency Guard — SQLite local para garantir que cada task_id
seja executado **exatamente uma vez**, mesmo após reconexões ou
re-envios da fila Redis.

Schema:
    executed_tasks(
        task_id       TEXT PRIMARY KEY,
        status        TEXT,          -- SUCCESS | FAILED
        attempt_count INTEGER,
        error_message TEXT,
        executed_at   TEXT           -- ISO-8601
    )
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Literal
from uuid import UUID

from src.core.logger import get_logger

logger = get_logger(__name__)

ExecutionStatus = Literal["SUCCESS", "FAILED", "PROCESSING"]


@dataclass(frozen=True)
class ExecutionRecord:
    task_id: str
    status: ExecutionStatus
    attempt_count: int
    error_message: str | None
    executed_at: datetime


class IdempotencyGuard:
    """
    Guarda estado de execução em SQLite local.
    Thread-safe via check_same_thread=False + WAL mode.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS executed_tasks (
            task_id       TEXT    PRIMARY KEY,
            status        TEXT    NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            executed_at   TEXT    NOT NULL
        );
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit — controlamos manualmente
        )
        self._conn.executescript(self._DDL)
        logger.info("IdempotencyGuard inicializado", db_path=str(db_path))

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Cursor, None, None]:
        """Gerenciador de transação explícita."""
        self._conn.execute("BEGIN IMMEDIATE")
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def is_already_executed(self, task_id: UUID) -> bool:
        """
        Retorna True se a tarefa já foi executada com sucesso.
        Tarefas com status FAILED podem ser retentadas.
        """
        row = self._conn.execute(
            "SELECT status FROM executed_tasks WHERE task_id = ?",
            (str(task_id),),
        ).fetchone()

        if row is None:
            return False

        already_done = row[0] == "SUCCESS"
        if already_done:
            logger.warning(
                "Tarefa já executada — ignorando",
                task_id=str(task_id),
                status=row[0],
            )
        return already_done

    def mark_processing(self, task_id: UUID, attempt: int) -> None:
        """Registra que a tarefa está em processamento (upsert)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO executed_tasks (task_id, status, attempt_count, executed_at)
                VALUES (?, 'PROCESSING', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = 'PROCESSING',
                    attempt_count = excluded.attempt_count,
                    executed_at = excluded.executed_at
                """,
                (str(task_id), attempt, now),
            )
        logger.debug("Tarefa marcada como PROCESSING", task_id=str(task_id), attempt=attempt)

    def mark_success(self, task_id: UUID, attempt: int) -> None:
        """Marca a tarefa como concluída com sucesso."""
        now = datetime.now(timezone.utc).isoformat()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO executed_tasks (task_id, status, attempt_count, executed_at)
                VALUES (?, 'SUCCESS', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = 'SUCCESS',
                    attempt_count = excluded.attempt_count,
                    executed_at = excluded.executed_at
                """,
                (str(task_id), attempt, now),
            )
        logger.info("Tarefa concluída com sucesso", task_id=str(task_id), attempt=attempt)

    def mark_failed(self, task_id: UUID, attempt: int, error: str) -> None:
        """Marca a tarefa como falha (permite nova tentativa futura)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO executed_tasks (task_id, status, attempt_count, error_message, executed_at)
                VALUES (?, 'FAILED', ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = 'FAILED',
                    attempt_count = excluded.attempt_count,
                    error_message = excluded.error_message,
                    executed_at = excluded.executed_at
                """,
                (str(task_id), attempt, error[:2000], now),
            )
        logger.warning(
            "Tarefa marcada como FAILED",
            task_id=str(task_id),
            attempt=attempt,
            error=error[:200],
        )

    def get_record(self, task_id: UUID) -> ExecutionRecord | None:
        """Busca o registro de execução de uma tarefa."""
        row = self._conn.execute(
            "SELECT task_id, status, attempt_count, error_message, executed_at "
            "FROM executed_tasks WHERE task_id = ?",
            (str(task_id),),
        ).fetchone()

        if row is None:
            return None

        return ExecutionRecord(
            task_id=row[0],
            status=row[1],
            attempt_count=row[2],
            error_message=row[3],
            executed_at=datetime.fromisoformat(row[4]),
        )

    def close(self) -> None:
        self._conn.close()
        logger.debug("IdempotencyGuard encerrado")
