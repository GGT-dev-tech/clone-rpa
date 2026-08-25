"""
File Downloader — baixa arquivos de URLs remotas para um diretório
temporário local antes de injetá-los no ERP.

Estratégias:
  - Download via HTTP (requests) com verificação de tamanho máximo
  - Nomeação determinística baseada em SHA-256 da URL (evita duplicatas em disco)
  - Limpeza automática de arquivos antigos (> N horas)
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)

_MAX_RETRY_DOWNLOAD = 3
_DOWNLOAD_TIMEOUT_S = 60
_OLD_FILE_THRESHOLD_HOURS = 24


class FileDownloader:
    """
    Baixa e cacheia arquivos em disco local para uso pelo Worker RPA.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._temp_dir = self._settings.TEMP_DIR
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "auto-adm-worker/1.0",
        })

    def download(self, url: str) -> Path:
        """
        Baixa o arquivo da URL e retorna o caminho local.
        Se o arquivo já estiver em cache (mesmo hash de URL), retorna o cache.

        Args:
            url: URL pública do arquivo (suporta HTTP/HTTPS)

        Returns:
            Path absoluto do arquivo baixado no disco local

        Raises:
            RuntimeError: Se o download falhar após todas as tentativas
        """
        local_path = self._local_path_for(url)

        # Cache hit — arquivo já existe e não está muito antigo
        if local_path.exists():
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                local_path.stat().st_mtime, tz=timezone.utc
            )
            if age < timedelta(hours=_OLD_FILE_THRESHOLD_HOURS):
                logger.debug("Cache hit para download", url=url, path=str(local_path))
                return local_path

        for attempt in range(1, _MAX_RETRY_DOWNLOAD + 1):
            try:
                logger.info("Baixando arquivo", url=url, tentativa=attempt)
                response = self._session.get(
                    url,
                    timeout=_DOWNLOAD_TIMEOUT_S,
                    stream=True,
                )
                response.raise_for_status()

                self._validate_size(response)

                local_path.parent.mkdir(parents=True, exist_ok=True)
                with open(local_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                logger.info(
                    "Download concluído",
                    url=url,
                    path=str(local_path),
                    size_kb=round(local_path.stat().st_size / 1024, 1),
                )
                return local_path

            except requests.RequestException as exc:
                logger.warning(
                    "Falha no download",
                    url=url,
                    tentativa=attempt,
                    error=str(exc),
                )
                if attempt < _MAX_RETRY_DOWNLOAD:
                    time.sleep(2 ** attempt)  # Backoff: 2s, 4s
                else:
                    raise RuntimeError(
                        f"Falha ao baixar '{url}' após {_MAX_RETRY_DOWNLOAD} tentativas: {exc}"
                    ) from exc

        raise RuntimeError("Código inalcançável")

    def _local_path_for(self, url: str) -> Path:
        """
        Gera um caminho local determinístico a partir da URL.
        Preserva a extensão original do arquivo.
        """
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".bin"
        filename = f"{url_hash}{suffix}"
        return self._temp_dir / filename

    def _validate_size(self, response: requests.Response) -> None:
        """Verifica se o Content-Length não excede o limite configurado."""
        max_bytes = self._settings.MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise RuntimeError(
                f"Arquivo excede o tamanho máximo permitido "
                f"({self._settings.MAX_ATTACHMENT_SIZE_MB} MB)."
            )

    def cleanup_old_files(self, older_than_hours: int = _OLD_FILE_THRESHOLD_HOURS) -> int:
        """
        Remove arquivos temporários mais antigos que `older_than_hours` horas.
        Retorna o número de arquivos removidos.
        """
        threshold = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        removed = 0
        for f in self._temp_dir.glob("*"):
            if f.is_file():
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                if mtime < threshold:
                    try:
                        f.unlink()
                        removed += 1
                    except OSError as exc:
                        logger.warning("Falha ao remover temp file", path=str(f), error=str(exc))

        if removed:
            logger.info("Arquivos temporários removidos", count=removed)

        return removed
