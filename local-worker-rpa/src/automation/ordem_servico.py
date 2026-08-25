"""
Fluxo 2: Inserir Observações e Anexos em Ordem de Serviço (OS).

Toda localização de controles é feita via ControlLocator (YAML-driven).
Ajuste os valores em:
  control_maps/ssplus_default.yaml → flows.OBSERVACAO_OS

Sequência:
  1. Baixar anexos das URLs → disco temporário
  2. Navegar à tela de OS (menu ou hotkey)
  3. Buscar a OS pelo ID
  4. Colar texto de observação (via clipboard — seguro para acentos)
  5. Para cada anexo: clicar "Anexar" → diálogo de arquivo nativo
  6. Salvar o registro
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from src.automation.base_automation import BaseAutomation, ERPSession
from src.core.logger import get_logger
from src.utils.file_downloader import FileDownloader
from src.utils.retry import ERPElementNotFoundError, ERPValidationError, rpa_retry_with_screenshot

logger = get_logger(__name__)

_FLOW = "OBSERVACAO_OS"


@dataclass(frozen=True)
class ObservacaoOSInput:
    os_id: str
    texto_observacao: str
    anexo_urls: list[str] = field(default_factory=list)


@dataclass
class ObservacaoOSResult:
    os_id: str
    observacao_salva: bool
    anexos_inseridos: int
    anexos_falhos: list[str] = field(default_factory=list)


class OrdemServicoAutomation(BaseAutomation):
    """Automatiza inserção de observações e anexos em Ordens de Serviço."""

    def __init__(self, erp_session: ERPSession, downloader: FileDownloader) -> None:
        super().__init__(erp_session)
        self._downloader = downloader

    def execute(self, **kwargs) -> dict:  # type: ignore[override]
        input_data = ObservacaoOSInput(**kwargs)
        result = self._executar_observacao_os(input_data)
        return {
            "os_id": result.os_id,
            "observacao_salva": result.observacao_salva,
            "anexos_inseridos": result.anexos_inseridos,
            "anexos_falhos": result.anexos_falhos,
        }

    @rpa_retry_with_screenshot(max_attempts=3, wait_min=2.0, wait_max=8.0)
    def _executar_observacao_os(self, data: ObservacaoOSInput) -> ObservacaoOSResult:
        """Executa o fluxo completo com retry."""

        # 1. Download dos anexos ANTES de abrir o ERP
        arquivos_locais: list[Path] = []
        anexos_falhos: list[str] = []

        for url in data.anexo_urls:
            try:
                path = self._downloader.download(url)
                arquivos_locais.append(path)
            except Exception as exc:
                self._logger.warning("Falha ao baixar anexo", url=url, error=str(exc))
                anexos_falhos.append(url)

        # 2. Navegar à tela de OS
        with self.erp_action_context("navegar_os") as window:
            self._navegar_menu(window)

        # 3. Buscar OS
        with self.erp_action_context("buscar_os") as window:
            self._buscar_os(window, data.os_id)

        # 4. Inserir observação
        with self.erp_action_context("inserir_observacao") as window:
            self._inserir_observacao(window, data.texto_observacao)

        # 5. Inserir cada anexo
        anexos_inseridos = 0
        for arquivo in arquivos_locais:
            try:
                with self.erp_action_context(f"inserir_anexo_{arquivo.name}") as window:
                    self._inserir_anexo(window, arquivo)
                    anexos_inseridos += 1
            except Exception as exc:
                self._logger.error("Falha ao inserir anexo", arquivo=str(arquivo), error=str(exc))
                anexos_falhos.append(str(arquivo))

        # 6. Salvar
        with self.erp_action_context("salvar_os") as window:
            self._salvar(window)

        self._logger.info(
            "OS atualizada com sucesso",
            os_id=data.os_id,
            anexos_ok=anexos_inseridos,
            anexos_falhos=len(anexos_falhos),
        )
        return ObservacaoOSResult(
            os_id=data.os_id,
            observacao_salva=True,
            anexos_inseridos=anexos_inseridos,
            anexos_falhos=anexos_falhos,
        )

    def _navegar_menu(self, window) -> None:
        """Navega ao menu de OS via click ou hotkey (YAML-driven)."""
        hotkeys = self.loc.get_menu_hotkeys(_FLOW)

        try:
            self.loc.click(window, f"{_FLOW}.menu.servicos")
            time.sleep(0.3)
            self.loc.click(window, f"{_FLOW}.menu.ordem_servico")
            time.sleep(0.5)
        except ERPElementNotFoundError:
            hk_srv = hotkeys.get("hotkey_servicos")
            hk_os = hotkeys.get("hotkey_os")

            if not all([hk_srv, hk_os]):
                raise ERPElementNotFoundError(
                    f"Menu de OS não encontrado. Configure hotkeys em "
                    f"'flows.{_FLOW}.menu' no control_map.yaml."
                )
            window.type_keys(hk_srv)
            time.sleep(0.2)
            window.type_keys(hk_os)
            time.sleep(0.5)

    def _buscar_os(self, window, os_id: str) -> None:
        """Pesquisa a OS pelo código e abre o registro."""
        self._logger.debug("Buscando OS", os_id=os_id)

        self.loc.type_text(window, f"{_FLOW}.tela_pesquisa.campo_os_id", os_id)
        self.loc.click(window, f"{_FLOW}.tela_pesquisa.botao_pesquisar")
        time.sleep(1.0)

        lista_spec = self.loc.get_spec(f"{_FLOW}.tela_pesquisa.lista_resultados")
        try:
            lookup: dict = {"control_type": lista_spec.control_type or "List"}
            if lista_spec.auto_id:
                lookup["auto_id"] = lista_spec.auto_id
            if lista_spec.class_name:
                lookup["class_name"] = lista_spec.class_name

            lista = window.child_window(**lookup)
            lista.wait("visible", timeout=5.0)
            items = lista.items()

            if not items:
                raise ERPValidationError(f"OS '{os_id}' não encontrada no ERP.")

            items[0].double_click_input()
            time.sleep(0.8)
        except ERPValidationError:
            raise
        except Exception as exc:
            raise ERPElementNotFoundError(
                f"Falha ao selecionar OS '{os_id}' na lista: {exc}"
            ) from exc

    def _inserir_observacao(self, window, texto: str) -> None:
        """
        Insere o texto de observação via clipboard (seguro para acentos).
        O campo pode ter múltiplos títulos alternativos — todos definidos no YAML.
        """
        self._logger.debug("Inserindo observação", chars=len(texto))

        # type_text com use_clipboard=True usa paste via Ctrl+V
        sucesso = self.loc.type_text(
            window,
            f"{_FLOW}.tela_os.campo_observacao",
            texto,
            use_clipboard=True,
            clear_first=True,
        )

        if not sucesso:
            raise ERPElementNotFoundError(
                f"Campo de observação não encontrado na tela de OS. "
                f"Verifique 'flows.{_FLOW}.tela_os.campo_observacao' no YAML."
            )

    def _inserir_anexo(self, window, arquivo: Path) -> None:
        """
        Clica em 'Anexar' e seleciona o arquivo no diálogo nativo do Windows.
        As especificações do diálogo são lidas do YAML.
        """
        self._logger.debug("Inserindo anexo", arquivo=arquivo.name)

        self.loc.click(window, f"{_FLOW}.tela_os.botao_anexar")
        time.sleep(0.8)

        # Lê spec do diálogo de arquivo do YAML
        try:
            dialog_title_re = self.loc.get_value(f"{_FLOW}.dialog_arquivo.title_re")
            dialog_class = self.loc.get_value(f"{_FLOW}.dialog_arquivo.class_name")
            botao_abrir_title = self.loc.get_value(f"{_FLOW}.dialog_arquivo.botao_abrir.title")
        except Exception:
            dialog_title_re = "Abrir|Open"
            dialog_class = "#32770"
            botao_abrir_title = "&Abrir"

        try:
            from pywinauto import Desktop

            dialogo = Desktop(backend="win32").window(
                title_re=dialog_title_re,
                class_name=dialog_class,
            )
            dialogo.wait("visible", timeout=6.0)

            # Campo "Nome do arquivo:" — usa auto_id do YAML
            campo_auto_id = None
            try:
                campo_auto_id = self.loc.get_value(
                    f"{_FLOW}.dialog_arquivo.campo_nome.auto_id"
                )
            except Exception:
                campo_auto_id = "1148"  # Windows 10/11 padrão

            campo_nome = dialogo.child_window(auto_id=campo_auto_id, control_type="Edit")
            campo_nome.set_text(str(arquivo.resolve()))
            time.sleep(0.2)

            dialogo.child_window(title=botao_abrir_title).click()
            time.sleep(1.0)

        except Exception as exc:
            raise ERPElementNotFoundError(
                f"Diálogo de arquivo não apareceu para '{arquivo.name}': {exc}\n"
                f"Verifique 'flows.{_FLOW}.dialog_arquivo' no YAML."
            ) from exc

    def _salvar(self, window) -> None:
        """Salva o registro via botão ou hotkey do YAML."""
        clicked = self.loc.click(window, f"{_FLOW}.tela_os.botao_salvar")
        if not clicked:
            try:
                hotkey = self.loc.get_value(f"{_FLOW}.tela_os.hotkey_salvar")
                if hotkey and hotkey != "???":
                    window.type_keys(hotkey)
            except Exception:
                window.type_keys("^s")

        time.sleep(1.0)
        self._session.dismiss_popup_if_present()
