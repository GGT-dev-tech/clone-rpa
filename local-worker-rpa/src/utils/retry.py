"""
Utilitário de Retry com backoff exponencial e jitter para ações RPA.

Encapsula `tenacity` com logging estruturado e callbacks de screenshot
para capturar o estado visual do ERP no momento da falha.
"""
from __future__ import annotations

import functools
from typing import Any, Callable, Type

from tenacity import (
    RetryCallState,
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.core.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceções de RPA
# ---------------------------------------------------------------------------

class RPAError(Exception):
    """Base para todos os erros de automação RPA."""


class ERPWindowNotFoundError(RPAError):
    """A janela principal do ERP não foi encontrada."""


class ERPElementNotFoundError(RPAError):
    """Um elemento específico (campo, botão) não foi encontrado."""


class ERPTimeoutError(RPAError):
    """Timeout aguardando o ERP responder."""


class ERPUnexpectedPopupError(RPAError):
    """Um pop-up inesperado bloqueou o fluxo."""


class ERPValidationError(RPAError):
    """O ERP exibiu uma mensagem de erro de validação."""


# ---------------------------------------------------------------------------
# Callbacks de retry
# ---------------------------------------------------------------------------

def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """Callback chamado antes de cada retry — loga detalhes da falha."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "RPA retry agendado",
        attempt=retry_state.attempt_number,
        wait_seconds=round(retry_state.next_action.sleep, 2) if retry_state.next_action else None,
        error_type=type(exc).__name__ if exc else "unknown",
        error=str(exc)[:300] if exc else None,
    )


def _take_screenshot_on_failure(retry_state: RetryCallState) -> None:
    """
    Após a última tentativa falhar, captura screenshot para diagnóstico.
    Importação lazy para evitar dependência circular.
    """
    try:
        from src.utils.screenshot import capture_screenshot
        path = capture_screenshot(prefix="rpa_error")
        logger.error("Screenshot de falha capturado", path=str(path))
    except Exception as sc_err:
        logger.warning("Falha ao capturar screenshot", error=str(sc_err))


# ---------------------------------------------------------------------------
# Decorator de retry configurável
# ---------------------------------------------------------------------------

def rpa_retry(
    max_attempts: int = 3,
    wait_min: float = 2.0,
    wait_max: float = 8.0,
    retryable_exceptions: tuple[Type[Exception], ...] = (
        ERPWindowNotFoundError,
        ERPElementNotFoundError,
        ERPTimeoutError,
        ERPUnexpectedPopupError,
    ),
) -> Callable:
    """
    Decorator que aplica retry com backoff exponencial + jitter.

    Uso:
        @rpa_retry(max_attempts=3)
        def preencher_campo(self, valor: str): ...
    """
    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=wait_min, max=wait_max, jitter=1.0),
            retry=retry_if_exception_type(retryable_exceptions),
            before_sleep=_log_retry_attempt,
            reraise=True,
        )
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return wrapper

    return decorator


def rpa_retry_with_screenshot(
    max_attempts: int = 3,
    wait_min: float = 2.0,
    wait_max: float = 8.0,
) -> Callable:
    """
    Variante de rpa_retry que também captura screenshot na última falha.
    Ideal para ações críticas como confirmar uma baixa de pagamento.
    """
    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=wait_min, max=wait_max, jitter=1.0),
            retry=retry_if_exception_type((RPAError,)),
            before_sleep=_log_retry_attempt,
            after=_take_screenshot_on_failure,
            reraise=True,
        )
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return wrapper

    return decorator
