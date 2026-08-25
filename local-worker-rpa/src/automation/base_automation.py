"""
Base Automation — classe abstrata com o ciclo de vida comum
para todas as automações do ERP SSPlus.

Responsabilidades:
  - Localizar e trazer a janela ERP ao foco
  - Fechar pop-ups inesperados (lidos do control_map.yaml)
  - Fornecer helpers tipados via ControlLocator
  - Capturar screenshot em caso de erro
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Generator

from pywinauto import Application, Desktop
from pywinauto.controls.hwndwrapper import HwndWrapper
from pywinauto.timings import TimeoutError as PWTimeoutError

from src.core.config import get_settings
from src.core.control_locator import ControlLocator
from src.core.logger import get_logger
from src.utils.retry import (
    ERPElementNotFoundError,
    ERPTimeoutError,
    ERPUnexpectedPopupError,
    ERPWindowNotFoundError,
    rpa_retry,
)
from src.utils.screenshot import capture_screenshot

logger = get_logger(__name__)

# Perfil padrão do control map — sobrescrevível via WorkerSettings futuramente
_DEFAULT_PROFILE = "ssplus_default"




class ERPSession:
    """
    Gerencia a conexão com o processo do ERP SSPlus.
    Reutiliza a instância já aberta — nunca abre uma nova sessão
    enquanto o ERP já estiver em execução.
    """

    def __init__(self, locator: ControlLocator | None = None) -> None:
        self._settings = get_settings()
        self._app: Application | None = None
        self._main_window: HwndWrapper | None = None
        # Locator é lazy-loaded para não bloquear se o YAML ainda não estiver pronto
        self._locator = locator

    @property
    def locator(self) -> ControlLocator:
        if self._locator is None:
            self._locator = ControlLocator.load(_DEFAULT_PROFILE)
        return self._locator

    def connect(self) -> Application:
        """
        Conecta ao ERP já em execução.
        Usa o title_re do control_map.yaml (mais preciso que a config genérica).
        """
        if self._app is not None:
            try:
                self._app.is_process_running()
                return self._app
            except Exception:
                self._app = None

        # Usa o regex do mapa YAML quando disponível, fallback para config
        try:
            title_re = self.locator._data.get(
                "main_window_title_re",
                f".*{self._settings.ERP_WINDOW_TITLE}.*",
            )
        except Exception:
            title_re = f".*{self._settings.ERP_WINDOW_TITLE}.*"

        try:
            backend = self._settings.ERP_BACKEND
            self._app = Application(backend=backend).connect(
                title_re=title_re,
                timeout=5,
            )
            logger.info("Conectado ao ERP SSPlus", title_re=title_re, backend=backend)
            return self._app
        except Exception:
            raise ERPWindowNotFoundError(
                f"ERP não encontrado com título '{title_re}'. "
                "Verifique se o ERP está aberto e se 'main_window_title_re' no YAML está correto."
            )

    def get_main_window(self) -> HwndWrapper:
        """Retorna a janela principal do ERP com foco garantido."""
        app = self.connect()
        try:
            title_re = self.locator._data.get(
                "main_window_title_re",
                f".*{self._settings.ERP_WINDOW_TITLE}.*",
            )
            window = app.window(title_re=title_re)
            window.wait("visible", timeout=self._settings.ERP_STARTUP_TIMEOUT)
            window.set_focus()
            self._main_window = window
            return window
        except PWTimeoutError as exc:
            raise ERPTimeoutError(f"Timeout aguardando janela principal do ERP: {exc}") from exc

    def dismiss_popup_if_present(self) -> bool:
        """
        Verifica se há um pop-up bloqueante e o fecha.
        Lê os títulos e botões de dismiss do control_map.yaml.
        Retorna True se um pop-up foi fechado.
        """
        try:
            popup_cfg = self.locator.get_popup_config()
            dismiss_titles: list[str] = popup_cfg.get("dismiss_titles", [])
            dismiss_buttons: list[str] = popup_cfg.get("dismiss_buttons", ["OK", "Fechar"])
        except Exception:
            # Fallback para padrões hardcoded se o YAML falhar
            dismiss_titles = ["Aviso", "Atenção", "Informação", "Erro", "Confirmação"]
            dismiss_buttons = ["OK", "&OK", "Fechar", "Cancelar"]

        try:
            desktop = Desktop(backend=self._settings.ERP_BACKEND)
            for title in dismiss_titles:
                dialogs = desktop.windows(title_re=f".*{title}.*", visible_only=True)
                for dialog in dialogs:
                    for btn_label in dismiss_buttons:
                        try:
                            dialog[btn_label].click()
                            logger.warning(
                                "Pop-up inesperado fechado",
                                titulo=title,
                                botao=btn_label,
                            )
                            time.sleep(0.3)
                            return True
                        except Exception:
                            continue
        except Exception as exc:
            logger.debug("Verificação de pop-up falhou (não crítico)", error=str(exc))

        return False


class BaseAutomation(ABC):
    """
    Classe base para todos os fluxos de automação do ERP SSPlus.

    Cada fluxo concreto (ContasPagar, OrdemServico, etc.) deve herdar desta
    classe e implementar o método `execute()`.

    O ControlLocator é compartilhado entre automações via a ERPSession,
    garantindo que o YAML seja carregado apenas uma vez.
    """

    def __init__(self, erp_session: ERPSession) -> None:
        self._session = erp_session
        self._settings = get_settings()
        self._logger = get_logger(self.__class__.__name__)

    @property
    def loc(self) -> ControlLocator:
        """Atalho para o ControlLocator da sessão ERP."""
        return self._session.locator

    @abstractmethod
    def execute(self, **kwargs) -> dict:
        """
        Executa o fluxo de automação.
        Retorna um dict com os resultados (confirmação, número de protocolo, etc.).
        """
        ...

    @contextmanager
    def erp_action_context(self, action_name: str) -> Generator[HwndWrapper, None, None]:
        """
        Context manager para qualquer ação no ERP.
        Garante: foco na janela + dismiss de pop-ups + screenshot em falha.
        """
        self._logger.debug("Iniciando ação ERP", acao=action_name)
        try:
            self._session.dismiss_popup_if_present()
            window = self._session.get_main_window()
            yield window
            self._logger.debug("Ação ERP concluída", acao=action_name)
        except (ERPWindowNotFoundError, ERPTimeoutError, ERPUnexpectedPopupError):
            raise  # Propaga para o decorator @rpa_retry tratar
        except Exception as exc:
            screenshot_path = capture_screenshot(prefix=f"error_{action_name}")
            self._logger.error(
                "Falha em ação ERP",
                acao=action_name,
                error=str(exc),
                screenshot=str(screenshot_path),
            )
            raise ERPElementNotFoundError(
                f"Falha na ação '{action_name}': {exc}"
            ) from exc

    def _wait_for_element(
        self,
        window: HwndWrapper,
        control_type: str,
        title: str | None = None,
        auto_id: str | None = None,
        timeout: float | None = None,
    ) -> HwndWrapper:
        """
        Helper tipado para aguardar e retornar um controle específico.
        Lança ERPElementNotFoundError se o controle não aparecer.
        """
        timeout = timeout or self._settings.ERP_ACTION_TIMEOUT
        try:
            spec: dict = {"control_type": control_type}
            if title:
                spec["title"] = title
            if auto_id:
                spec["auto_id"] = auto_id

            element = window.child_window(**spec)
            element.wait("visible enabled", timeout=timeout)
            return element
        except PWTimeoutError as exc:
            raise ERPElementNotFoundError(
                f"Controle '{control_type}' (title={title}) não encontrado em {timeout}s."
            ) from exc

    def _type_text(self, control: HwndWrapper, text: str, clear_first: bool = True) -> None:
        """Digita texto em um controle, limpando-o primeiro se necessário."""
        if clear_first:
            control.set_text("")
        control.type_keys(text, with_spaces=True, pause=0.05)

    def _click_button(self, window: HwndWrapper, button_title: str) -> None:
        """Clica em um botão pelo título, com retry implícito."""
        try:
            btn = window.child_window(title=button_title, control_type="Button")
            btn.wait("enabled", timeout=self._settings.ERP_ACTION_TIMEOUT)
            btn.click_input()
        except Exception as exc:
            raise ERPElementNotFoundError(
                f"Botão '{button_title}' não encontrado ou não clicável."
            ) from exc
