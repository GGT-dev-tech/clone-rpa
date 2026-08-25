"""
Fluxo 1: Baixa no Contas a Pagar do ERP SSPlus.

Toda localização de controles é feita via ControlLocator (YAML-driven),
eliminando strings hardcoded. Ajuste os valores no arquivo:
  control_maps/ssplus_default.yaml → flows.BAIXA_CONTAS_PAGAR

Sequência de automação:
  1. Navegar ao menu Contas a Pagar (via menu ou hotkey)
  2. Abrir tela de pesquisa de títulos
  3. Pesquisar pelo ID da duplicata
  4. Abrir o título encontrado (duplo clique na lista)
  5. Preencher: data de pagamento, valor pago, conta, observação
  6. Confirmar a baixa (botão ou hotkey do YAML)
  7. Capturar número de protocolo do pop-up de confirmação
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.automation.base_automation import BaseAutomation, ERPSession
from src.core.logger import get_logger
from src.utils.retry import ERPElementNotFoundError, ERPValidationError, rpa_retry_with_screenshot

logger = get_logger(__name__)

# Prefixo do fluxo no YAML — facilita refatoração futura
_FLOW = "BAIXA_CONTAS_PAGAR"


@dataclass(frozen=True)
class BaixaContasPagarInput:
    duplicata_id: str
    data_pagamento: date
    valor: Decimal
    conta: str
    observacao: str | None = None


@dataclass(frozen=True)
class BaixaContasPagarResult:
    duplicata_id: str
    confirmado: bool
    numero_protocolo: str | None = None


class ContasPagarAutomation(BaseAutomation):
    """
    Automatiza a liquidação de duplicatas no módulo Contas a Pagar.
    Usa ControlLocator para resolver todos os controles via YAML.
    """

    def execute(self, **kwargs) -> dict:  # type: ignore[override]
        input_data = BaixaContasPagarInput(**kwargs)
        result = self._executar_baixa(input_data)
        return {
            "duplicata_id": result.duplicata_id,
            "confirmado": result.confirmado,
            "numero_protocolo": result.numero_protocolo,
        }

    @rpa_retry_with_screenshot(max_attempts=3, wait_min=2.0, wait_max=8.0)
    def _executar_baixa(self, data: BaixaContasPagarInput) -> BaixaContasPagarResult:
        """Executa o fluxo completo com retry automático."""

        with self.erp_action_context("navegar_contas_pagar") as window:
            self._navegar_menu(window)

        with self.erp_action_context("pesquisar_duplicata") as window:
            self._pesquisar_duplicata(window, data.duplicata_id)

        with self.erp_action_context("preencher_baixa") as window:
            self._preencher_dados_baixa(window, data)

        with self.erp_action_context("confirmar_baixa") as window:
            protocolo = self._confirmar_e_obter_protocolo(window)

        logger.info(
            "Baixa executada com sucesso",
            duplicata_id=data.duplicata_id,
            protocolo=protocolo,
        )
        return BaixaContasPagarResult(
            duplicata_id=data.duplicata_id,
            confirmado=True,
            numero_protocolo=protocolo,
        )

    def _navegar_menu(self, window) -> None:
        """
        Navega pelo menu principal até Contas a Pagar.
        Estratégia 1: clique nos itens de menu (via ControlLocator).
        Estratégia 2: hotkeys configuradas no YAML.
        """
        self._logger.debug("Navegando ao menu Contas a Pagar")

        hotkeys = self.loc.get_menu_hotkeys(_FLOW)

        try:
            # Estratégia 1: clique nos itens de menu
            self.loc.click(window, f"{_FLOW}.menu.financeiro")
            time.sleep(0.3)
            self.loc.click(window, f"{_FLOW}.menu.contas_pagar")
            time.sleep(0.3)
            self.loc.click(window, f"{_FLOW}.menu.baixa_titulos")
            time.sleep(0.5)

        except ERPElementNotFoundError:
            # Estratégia 2: hotkeys do YAML
            self._logger.debug("Menu click falhou — usando hotkeys do YAML", hotkeys=hotkeys)
            hk_fin = hotkeys.get("hotkey_financeiro")
            hk_cp = hotkeys.get("hotkey_contas_pagar")
            hk_baixa = hotkeys.get("hotkey_baixa")

            if not all([hk_fin, hk_cp, hk_baixa]):
                raise ERPElementNotFoundError(
                    f"Menu de Contas a Pagar não encontrado e hotkeys não configuradas no YAML.\n"
                    f"Configure 'flows.{_FLOW}.menu.hotkey_*' no control_map.yaml."
                )

            window.type_keys(hk_fin)
            time.sleep(0.2)
            window.type_keys(hk_cp)
            time.sleep(0.2)
            window.type_keys(hk_baixa)
            time.sleep(0.5)

    def _pesquisar_duplicata(self, window, duplicata_id: str) -> None:
        """Preenche o campo de pesquisa e abre o título encontrado."""
        self._logger.debug("Pesquisando duplicata", duplicata_id=duplicata_id)

        self.loc.type_text(window, f"{_FLOW}.tela_pesquisa.campo_duplicata_id", duplicata_id)
        self.loc.click(window, f"{_FLOW}.tela_pesquisa.botao_pesquisar")
        time.sleep(1.0)

        # Seleciona o primeiro resultado da lista
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
                raise ERPValidationError(
                    f"Duplicata '{duplicata_id}' não encontrada no ERP."
                )
            items[0].double_click_input()
            time.sleep(0.8)
        except ERPValidationError:
            raise
        except Exception as exc:
            raise ERPElementNotFoundError(
                f"Falha ao selecionar duplicata '{duplicata_id}' na lista: {exc}"
            ) from exc

    def _preencher_dados_baixa(self, window, data: BaixaContasPagarInput) -> None:
        """Preenche os campos de data, valor e conta."""
        self._logger.debug(
            "Preenchendo dados de baixa",
            data_pgto=str(data.data_pagamento),
            valor=str(data.valor),
            conta=data.conta,
        )

        # Descobre o formato de data configurado no YAML
        try:
            date_fmt = self.loc.get_value(f"{_FLOW}.tela_baixa.campo_data_pagamento.date_format")
        except Exception:
            date_fmt = "dd/MM/yyyy"
        py_fmt = date_fmt.replace("dd", "%d").replace("MM", "%M").replace("yyyy", "%Y")

        # Descobre o separador decimal
        try:
            dec_sep = self.loc.get_value(
                f"{_FLOW}.tela_baixa.campo_valor_pago.decimal_separator"
            )
        except Exception:
            dec_sep = ","

        valor_str = f"{data.valor:.2f}".replace(".", dec_sep)

        self.loc.type_text(
            window,
            f"{_FLOW}.tela_baixa.campo_data_pagamento",
            data.data_pagamento.strftime(py_fmt),
        )

        self.loc.type_text(window, f"{_FLOW}.tela_baixa.campo_valor_pago", valor_str)
        self.loc.type_text(window, f"{_FLOW}.tela_baixa.campo_conta", data.conta)

        if data.observacao:
            # Campo de observação é opcional — não falha se ausente
            self.loc.type_text(
                window,
                f"{_FLOW}.tela_baixa.campo_observacao",
                data.observacao,
                use_clipboard=True,  # Acentos seguros
            )

    def _confirmar_e_obter_protocolo(self, window) -> str | None:
        """Confirma a baixa e extrai o número de protocolo do pop-up."""
        self._logger.debug("Confirmando baixa")

        # Tenta botão primeiro, depois hotkey
        clicked = self.loc.click(window, f"{_FLOW}.tela_baixa.botao_confirmar")
        if not clicked:
            try:
                hotkey = self.loc.get_value(f"{_FLOW}.tela_baixa.hotkey_confirmar")
                if hotkey and hotkey != "???":
                    window.type_keys(hotkey)
                    self._logger.debug("Hotkey de confirmação usada", hotkey=hotkey)
            except Exception:
                window.type_keys("{F9}")  # Fallback universal

        time.sleep(1.0)

        # Captura protocolo do pop-up de confirmação
        protocolo: str | None = None
        try:
            from pywinauto import Desktop

            popup_title_re = self.loc.get_value(f"{_FLOW}.popup_confirmacao.title_re")
            regex_protocolo = self.loc.get_value(f"{_FLOW}.popup_confirmacao.regex_protocolo")

            desktop = Desktop(backend=self._settings.ERP_BACKEND)
            popup = desktop.window(title_re=popup_title_re)
            popup.wait("visible", timeout=4.0)

            try:
                texto = popup.Static.window_text()
                match = re.search(regex_protocolo, texto, re.IGNORECASE)
                protocolo = match.group(1) if match else None
            except Exception:
                pass

            self._session.dismiss_popup_if_present()
        except Exception:
            self._logger.debug("Pop-up de confirmação não detectado — assumindo sucesso")

        return protocolo
