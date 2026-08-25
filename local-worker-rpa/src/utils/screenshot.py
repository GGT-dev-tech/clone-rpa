"""
Screenshot utility — captura a tela inteira ou janela específica
e salva com timestamp para diagnóstico de falhas RPA.
"""
from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import pyautogui

from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)


def capture_screenshot(prefix: str = "screenshot") -> Path:
    """
    Captura a tela inteira e salva como PNG no diretório de logs.

    Returns:
        Path do arquivo salvo.
    """
    settings = get_settings()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{prefix}_{timestamp}.png"
    path = settings.LOG_DIR / "screenshots" / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(str(path))
        logger.debug("Screenshot capturado", path=str(path))
    except Exception as exc:
        logger.warning("Falha ao capturar screenshot", error=str(exc))

    return path


def screenshot_to_base64(path: Path) -> str:
    """Converte screenshot em base64 para envio via WebSocket."""
    return base64.b64encode(path.read_bytes()).decode("utf-8")
