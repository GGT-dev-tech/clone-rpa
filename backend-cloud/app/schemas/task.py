"""
Schemas de Task — Pydantic v2 com tipagem estrita.
Define os contratos de dados entre Frontend, API e Worker Local.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


# ---------------------------------------------------------------------------
# Enumeradores
# ---------------------------------------------------------------------------

class TaskType(StrEnum):
    BAIXA_CONTAS_PAGAR = "BAIXA_CONTAS_PAGAR"
    OBSERVACAO_OS = "OBSERVACAO_OS"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Payloads específicos por tipo de tarefa
# ---------------------------------------------------------------------------

class BaixaContasPagarPayload(BaseModel):
    """Dados necessários para liquidar uma duplicata no Contas a Pagar do ERP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    duplicata_id: Annotated[str, Field(min_length=1, max_length=100)]
    data_pagamento: date
    valor: Annotated[Decimal, Field(gt=0, decimal_places=2)]
    conta: Annotated[str, Field(min_length=1, max_length=50)]
    observacao: str | None = Field(default=None, max_length=500)
    codigo_coi: str | None = Field(default=None, max_length=20)


class ObservacaoOSPayload(BaseModel):
    """Dados para inserir observações e anexos em uma Ordem de Serviço."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    os_id: Annotated[str, Field(min_length=1, max_length=100)]
    texto_observacao: Annotated[str, Field(min_length=1, max_length=4000)]
    anexo_urls: list[Annotated[str, Field(pattern=r"^https?://")]] = Field(
        default_factory=list, max_length=20
    )


TaskPayload = Annotated[
    Union[BaixaContasPagarPayload, ObservacaoOSPayload],
    Field(discriminator=None),  # discriminado pelo task_type no schema pai
]


# ---------------------------------------------------------------------------
# Schema principal da Tarefa
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    """Schema de entrada para criar uma nova tarefa (Frontend → API)."""

    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    payload: BaixaContasPagarPayload | ObservacaoOSPayload

    @model_validator(mode="after")
    def validate_payload_type(self) -> "TaskCreate":
        match self.task_type:
            case TaskType.BAIXA_CONTAS_PAGAR:
                if not isinstance(self.payload, BaixaContasPagarPayload):
                    raise ValueError(
                        "Para BAIXA_CONTAS_PAGAR, o payload deve ser BaixaContasPagarPayload."
                    )
            case TaskType.OBSERVACAO_OS:
                if not isinstance(self.payload, ObservacaoOSPayload):
                    raise ValueError(
                        "Para OBSERVACAO_OS, o payload deve ser ObservacaoOSPayload."
                    )
        return self


class TaskPublic(BaseModel):
    """Schema de resposta pública (API → Frontend)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_type: TaskType
    status: TaskStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    attempt_count: int
    error_message: str | None
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Mensagem WebSocket: API → Worker
# ---------------------------------------------------------------------------

class WorkerTaskMessage(BaseModel):
    """
    Mensagem enviada via WebSocket para o Worker Local executar.
    Inclui a chave de idempotência pré-calculada para o guard local.
    """

    model_config = ConfigDict(frozen=True)

    task_id: UUID = Field(default_factory=uuid4)
    task_type: TaskType
    payload: BaixaContasPagarPayload | ObservacaoOSPayload

    @computed_field  # type: ignore[misc]
    @property
    def idempotency_key(self) -> str:
        """SHA-256 do task_id — imutável, serve como chave de deduplicação."""
        return hashlib.sha256(str(self.task_id).encode()).hexdigest()

    def to_wire(self) -> str:
        """Serializa para envio via WebSocket (JSON string)."""
        return self.model_dump_json()

    @classmethod
    def from_wire(cls, raw: str) -> "WorkerTaskMessage":
        return cls.model_validate_json(raw)


# ---------------------------------------------------------------------------
# Mensagem WebSocket: Worker → API (status update)
# ---------------------------------------------------------------------------

class WorkerStatusUpdate(BaseModel):
    """Relatório de execução enviado pelo Worker de volta para a API."""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: Literal[
        TaskStatus.PROCESSING,
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.RETRYING,
    ]
    attempt: int = Field(ge=1)
    error: str | None = None
    screenshot_b64: str | None = Field(
        default=None,
        description="Screenshot em base64 capturado no momento da falha.",
    )
    executed_at: datetime = Field(default_factory=datetime.utcnow)
