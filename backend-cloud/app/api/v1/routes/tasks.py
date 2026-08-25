"""
Rotas REST para gerenciamento de Tarefas.
"""
from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.schemas.task import TaskCreate, TaskPublic, TaskStatus
from app.services.task_service import TaskService

router = APIRouter()


@router.post("/", response_model=TaskPublic, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    task_service: TaskService = Depends(TaskService),
) -> TaskPublic:
    """
    Cria uma nova tarefa e a coloca na fila para o Worker RPA.
    A idempotency_key é derivada do payload serializado — tarefas duplicadas
    retornam a tarefa existente com HTTP 200 ao invés de criar nova.
    """
    idempotency_key = hashlib.sha256(
        body.model_dump_json(sort_keys=True).encode()
    ).hexdigest()

    existing = await task_service.get_by_idempotency_key(idempotency_key)
    if existing:
        return existing  # Idempotente — retorna a existente

    task = await task_service.create_and_enqueue(body, idempotency_key)
    return task


@router.get("/", response_model=list[TaskPublic])
async def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    task_service: TaskService = Depends(TaskService),
) -> list[TaskPublic]:
    """Lista todas as tarefas, com filtro opcional por status."""
    return await task_service.list_tasks(
        status_filter=status_filter, limit=limit, offset=offset
    )


@router.get("/{task_id}", response_model=TaskPublic)
async def get_task(
    task_id: UUID,
    task_service: TaskService = Depends(TaskService),
) -> TaskPublic:
    """Retorna os detalhes de uma tarefa específica."""
    task = await task_service.get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarefa não encontrada.")
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(
    task_id: UUID,
    task_service: TaskService = Depends(TaskService),
) -> None:
    """Cancela uma tarefa PENDING. Tarefas em execução não podem ser canceladas."""
    cancelled = await task_service.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tarefa não pode ser cancelada no estado atual.",
        )
