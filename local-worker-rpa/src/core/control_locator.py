"""
ControlLocator — carrega o control_map.yaml e resolve controles pywinauto
de forma declarativa, substituindo strings hardcoded nas automações.

Uso nas classes de automação:
    locator = ControlLocator.load("ssplus_default")

    # Resolve um controle pelo caminho no YAML
    ctrl = locator.find(window, "BAIXA_CONTAS_PAGAR.tela_pesquisa.campo_duplicata_id")

    # Clica em um botão mapeado
    locator.click(window, "BAIXA_CONTAS_PAGAR.tela_pesquisa.botao_pesquisar")

    # Retorna o valor de um campo escalar (ex: hotkey)
    hotkey = locator.get_value("BAIXA_CONTAS_PAGAR.tela_baixa.hotkey_confirmar")
"""
from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from pywinauto.timings import TimeoutError as PWTimeoutError

from src.core.config import get_settings
from src.core.logger import get_logger
from src.utils.retry import ERPElementNotFoundError

logger = get_logger(__name__)

try:
    import yaml
except ImportError as exc:
    raise ImportError("PyYAML é obrigatório: pip install pyyaml") from exc

CONTROL_MAPS_DIR = Path(__file__).parent.parent.parent / "control_maps"


class ControlSpec:
    """
    Representa a especificação de um único controle extraída do YAML.
    Suporta localização por title, auto_id, class_name ou lista de titles_to_try.
    """

    def __init__(self, data: dict[str, Any], path: str) -> None:
        self._data = data
        self._path = path  # Caminho no YAML para debug

    # ----- Propriedades de lookup -----

    @property
    def title(self) -> str | None:
        v = self._data.get("title")
        return None if (v is None or v == "???") else v

    @property
    def titles_to_try(self) -> list[str]:
        raw = self._data.get("titles_to_try", [])
        return [t for t in raw if t != "???"]

    @property
    def control_type(self) -> str | None:
        return self._data.get("control_type")

    @property
    def auto_id(self) -> str | None:
        v = self._data.get("auto_id")
        return None if (v is None or v == "???") else v

    @property
    def class_name(self) -> str | None:
        v = self._data.get("class_name")
        return None if (v is None or v == "???") else v

    @property
    def is_optional(self) -> bool:
        return bool(self._data.get("optional", False))

    @property
    def is_mapped(self) -> bool:
        """True se o controle foi preenchido (não contém '???')."""
        return bool(self.title or self.auto_id or self.class_name or self.titles_to_try)

    def __repr__(self) -> str:
        return (
            f"ControlSpec(path={self._path!r}, title={self.title!r}, "
            f"auto_id={self.auto_id!r}, ctrl_type={self.control_type!r})"
        )


class ControlLocator:
    """
    Resolve controles Win32 a partir do mapa YAML.
    Instância cacheada por perfil — carregada uma vez e reutilizada.
    """

    def __init__(self, profile_name: str, data: dict[str, Any]) -> None:
        self._profile = profile_name
        self._data = data
        self._settings = get_settings()
        self._timeout = self._settings.ERP_ACTION_TIMEOUT

    # ----- Factory -----

    @classmethod
    @lru_cache(maxsize=4)
    def load(cls, profile_name: str = "ssplus_default") -> "ControlLocator":
        """
        Carrega e cacheia o mapa de controles a partir do arquivo YAML.
        Re-chame com um novo `profile_name` para suportar múltiplos perfis.
        """
        path = CONTROL_MAPS_DIR / f"{profile_name}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"Control map não encontrado: {path}\n"
                "Execute 'python tools/inspect_erp.py' → Modo 5 para gerá-lo."
            )

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        logger.info("Control map carregado", profile=profile_name, path=str(path))
        return cls(profile_name, data)

    @classmethod
    def reload(cls, profile_name: str = "ssplus_default") -> "ControlLocator":
        """Força recarregamento do cache (útil após edição do YAML em runtime)."""
        cls.load.cache_clear()
        return cls.load(profile_name)

    # ----- Navegação no YAML -----

    def _resolve_path(self, dot_path: str) -> Any:
        """
        Resolve um caminho pontilhado no dicionário de flows.
        ex: "BAIXA_CONTAS_PAGAR.tela_pesquisa.campo_duplicata_id"
        """
        parts = dot_path.split(".")
        current: Any = self._data.get("flows", self._data)

        for part in parts:
            if not isinstance(current, dict):
                raise KeyError(f"Caminho inválido em '{dot_path}' na parte '{part}'")
            if part not in current:
                raise KeyError(
                    f"Chave '{part}' não encontrada no control map '{self._profile}'. "
                    f"Caminho completo: {dot_path}"
                )
            current = current[part]

        return current

    def get_spec(self, dot_path: str) -> ControlSpec:
        """Retorna a ControlSpec para o caminho informado."""
        raw = self._resolve_path(dot_path)
        if not isinstance(raw, dict):
            raise TypeError(
                f"O caminho '{dot_path}' não aponta para um controle (dict). "
                f"Valor encontrado: {raw!r}"
            )
        return ControlSpec(raw, dot_path)

    def get_value(self, dot_path: str) -> Any:
        """Retorna um valor escalar do mapa (ex: hotkey, date_format)."""
        return self._resolve_path(dot_path)

    def get_popup_config(self) -> dict:
        """Retorna a configuração de pop-ups conhecidos."""
        return self._data.get("known_popups", {})

    # ----- Resolução de controles -----

    def find(self, window, dot_path: str, timeout: float | None = None) -> Any:
        """
        Localiza um controle no pywinauto a partir do mapa YAML.
        Tenta múltiplas estratégias em ordem de preferência:
          1. auto_id  (mais estável)
          2. title + control_type
          3. titles_to_try (lista de alternativas)
          4. class_name
        """
        spec = self.get_spec(dot_path)
        timeout = timeout or self._timeout

        if not spec.is_mapped:
            if spec.is_optional:
                logger.debug("Controle opcional não mapeado — pulando", path=dot_path)
                return None
            raise ERPElementNotFoundError(
                f"Controle '{dot_path}' ainda não foi mapeado no arquivo YAML. "
                f"Execute 'python tools/inspect_erp.py' para mapeá-lo."
            )

        logger.debug("Resolvendo controle", path=dot_path, spec=repr(spec))

        # Estratégia 1: AutomationId (mais confiável)
        if spec.auto_id:
            try:
                lookup: dict[str, Any] = {"auto_id": spec.auto_id}
                if spec.control_type:
                    lookup["control_type"] = spec.control_type
                ctrl = window.child_window(**lookup)
                ctrl.wait("visible exists", timeout=timeout)
                logger.debug("Controle resolvido via auto_id", auto_id=spec.auto_id)
                return ctrl
            except PWTimeoutError:
                logger.debug("auto_id não encontrado, tentando title", auto_id=spec.auto_id)

        # Estratégia 2: title + control_type
        if spec.title:
            try:
                lookup = {"title": spec.title}
                if spec.control_type:
                    lookup["control_type"] = spec.control_type
                ctrl = window.child_window(**lookup)
                ctrl.wait("visible exists", timeout=timeout)
                logger.debug("Controle resolvido via title", title=spec.title)
                return ctrl
            except PWTimeoutError:
                logger.debug("title não encontrado, tentando alternativas", title=spec.title)

        # Estratégia 3: titles_to_try
        for alt_title in spec.titles_to_try:
            try:
                lookup = {"title": alt_title}
                if spec.control_type:
                    lookup["control_type"] = spec.control_type
                ctrl = window.child_window(**lookup)
                ctrl.wait("visible exists", timeout=min(timeout, 3.0))
                logger.debug("Controle resolvido via titles_to_try", title=alt_title)
                return ctrl
            except PWTimeoutError:
                continue

        # Estratégia 4: class_name
        if spec.class_name:
            try:
                ctrl = window.child_window(class_name=spec.class_name)
                ctrl.wait("visible exists", timeout=timeout)
                logger.debug("Controle resolvido via class_name", class_name=spec.class_name)
                return ctrl
            except PWTimeoutError:
                pass

        # Falhou em todas as estratégias
        if spec.is_optional:
            logger.debug("Controle opcional não encontrado", path=dot_path)
            return None

        raise ERPElementNotFoundError(
            f"Controle '{dot_path}' não encontrado no ERP com nenhuma estratégia. "
            f"Spec: {spec!r}\n"
            "Dica: abra o inspector (tools/inspect_erp.py) e valide os valores no YAML."
        )

    def click(self, window, dot_path: str, timeout: float | None = None) -> bool:
        """Encontra e clica em um controle mapeado. Retorna False se optional e ausente."""
        ctrl = self.find(window, dot_path, timeout)
        if ctrl is None:
            return False  # Optional não encontrado
        timeout = timeout or self._timeout
        try:
            ctrl.wait("enabled", timeout=timeout)
            ctrl.click_input()
            time.sleep(0.1)
            return True
        except Exception as exc:
            raise ERPElementNotFoundError(
                f"Controle '{dot_path}' encontrado mas não clicável: {exc}"
            ) from exc

    def type_text(
        self,
        window,
        dot_path: str,
        text: str,
        clear_first: bool = True,
        use_clipboard: bool = False,
        timeout: float | None = None,
    ) -> bool:
        """
        Digita texto em um controle mapeado.

        Args:
            use_clipboard: True para textos longos ou com caracteres especiais.
        """
        ctrl = self.find(window, dot_path, timeout)
        if ctrl is None:
            return False

        ctrl.set_focus()

        if clear_first:
            ctrl.set_text("")
            time.sleep(0.05)

        if use_clipboard:
            import subprocess
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True, shell=True)
            ctrl.type_keys("^a^v")
        else:
            ctrl.type_keys(text, with_spaces=True, pause=0.04)

        return True

    def get_menu_hotkeys(self, flow: str) -> dict[str, str]:
        """Retorna as hotkeys de menu configuradas para um fluxo."""
        raw = self._resolve_path(f"{flow}.menu")
        return {
            k: v for k, v in raw.items()
            if k.startswith("hotkey_") and v != "???"
        }
