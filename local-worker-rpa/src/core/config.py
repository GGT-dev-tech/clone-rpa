"""
Configurações do Worker Local via Pydantic Settings v2.
Lidas de variáveis de ambiente ou arquivo .env.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AnyWebsocketUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Identidade ---
    WORKER_ID: str = "worker-01"
    WORKER_API_KEY: SecretStr  # deve bater com o backend

    # --- Conectividade com a API Central ---
    API_WS_URL: AnyWebsocketUrl   # ex: wss://auto-adm.up.railway.app/ws/worker
    API_BASE_URL: str             # ex: https://auto-adm.up.railway.app

    # --- Reconexão ---
    WS_RECONNECT_INITIAL_WAIT: float = 2.0   # segundos
    WS_RECONNECT_MAX_WAIT: float = 60.0
    WS_RECONNECT_MAX_ATTEMPTS: int = 0       # 0 = infinito

    # --- ERP SSPlus ---
    ERP_EXECUTABLE_PATH: Path = Path(r"C:\SSPlus\SSPlus.exe")
    ERP_WINDOW_TITLE: str = "SSPlus"         # substring do título da janela
    ERP_STARTUP_TIMEOUT: float = 30.0        # aguarda ERP abrir
    ERP_ACTION_TIMEOUT: float = 10.0         # timeout por ação individual
    ERP_BACKEND: Literal["win32", "uia"] = "win32"

    # --- Retry de ações RPA ---
    RPA_MAX_RETRIES: int = 3
    RPA_RETRY_WAIT_MIN: float = 2.0
    RPA_RETRY_WAIT_MAX: float = 8.0

    # --- Armazenamento local ---
    TEMP_DIR: Path = Path(r"C:\auto-adm\temp")
    STATE_DB_PATH: Path = Path(r"C:\auto-adm\worker_state.db")
    LOG_DIR: Path = Path(r"C:\auto-adm\logs")
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("TEMP_DIR", "STATE_DB_PATH", "LOG_DIR", mode="before")
    @classmethod
    def ensure_path(cls, v: str | Path) -> Path:
        return Path(v)

    def ensure_dirs(self) -> None:
        """Cria diretórios locais necessários se não existirem."""
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)


_settings: WorkerSettings | None = None


def get_settings() -> WorkerSettings:
    global _settings
    if _settings is None:
        _settings = WorkerSettings()  # type: ignore[call-arg]
        _settings.ensure_dirs()
    return _settings
