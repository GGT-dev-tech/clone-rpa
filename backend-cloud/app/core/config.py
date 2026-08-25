"""
Configurações centralizadas via Pydantic Settings v2.
Lê variáveis de ambiente e arquivo .env automaticamente.
"""
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Ambiente
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False

    # Aplicação
    APP_NAME: str = "auto-adm API"
    APP_VERSION: str = "1.0.0"
    SECRET_KEY: SecretStr
    WORKER_API_KEY: SecretStr  # chave compartilhada com o Worker local

    # Banco de Dados
    DATABASE_URL: PostgresDsn
    DB_ECHO_SQL: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_URL: RedisDsn
    REDIS_TASK_QUEUE: str = "auto_adm:tasks"
    REDIS_RESULT_TTL_SECONDS: int = 86_400  # 24h

    # CORS
    ALLOWED_ORIGINS: list[AnyHttpUrl] = []

    # WebSocket Worker
    WS_HEARTBEAT_INTERVAL: int = 30  # segundos
    WS_MAX_RECONNECT_ATTEMPTS: int = 10

    # Upload / Anexos
    MAX_ATTACHMENT_SIZE_MB: int = 10
    ATTACHMENT_STORAGE_URL: str = ""  # S3 ou R2 URL base

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list) -> list:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton — instância cacheada das configurações."""
    return Settings()  # type: ignore[call-arg]
