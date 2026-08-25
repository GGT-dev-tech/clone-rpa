"""
=============================================================================
FERRAMENTA DE INSPEÇÃO DE CONTROLES WIN32 — ERP SSPlus
=============================================================================

Uso (com o ERP aberto):
    python tools/inspect_erp.py

Modos disponíveis:
  [1] Listar todas as janelas abertas
  [2] Dump de controles de uma janela (dump completo)
  [3] Hover mode — mova o mouse sobre os campos do ERP
  [4] Buscar controle por texto/AutomationId
  [5] Gerar template YAML de mapeamento
  [6] Validar um control_map.yaml existente
  [q] Sair
=============================================================================
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import time
import json
from pathlib import Path
from typing import Any

# Garante que o pacote src/ seja encontrável
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import pywinauto
    from pywinauto import Desktop, Application
    from pywinauto.controls.hwndwrapper import HwndWrapper
    from pywinauto.findwindows import ElementNotFoundError
except ImportError:
    print("ERRO: pywinauto nao instalado. Execute: pip install pywinauto")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.syntax import Syntax
    from rich import box
except ImportError:
    print("ERRO: rich nao instalado. Execute: pip install rich")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERRO: PyYAML nao instalado. Execute: pip install pyyaml")
    sys.exit(1)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

console = Console()
CONTROL_MAP_DIR = Path(__file__).parent.parent / "control_maps"
DEFAULT_BACKEND = "win32"  # padrão — mude para "uia" se necessário


# ---------------------------------------------------------------------------
# Helpers de inspeção
# ---------------------------------------------------------------------------

def _control_to_dict(ctrl) -> dict[str, Any]:
    """Extrai as propriedades relevantes de um controle pywinauto."""
    info: dict[str, Any] = {}
    try:
        info["title"] = ctrl.window_text()
    except Exception:
        info["title"] = ""
    try:
        info["control_type"] = ctrl.element_info.control_type
    except Exception:
        info["control_type"] = ""
    try:
        info["class_name"] = ctrl.element_info.class_name
    except Exception:
        info["class_name"] = ""
    try:
        info["auto_id"] = ctrl.element_info.automation_id
    except Exception:
        info["auto_id"] = ""
    try:
        rect = ctrl.rectangle()
        info["rect"] = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
    except Exception:
        info["rect"] = ""
    try:
        info["visible"] = ctrl.is_visible()
        info["enabled"] = ctrl.is_enabled()
    except Exception:
        info["visible"] = False
        info["enabled"] = False
    return info


def _render_controls_table(controls: list[dict], title: str = "Controles") -> Table:
    """Renderiza lista de controles como tabela Rich."""
    table = Table(
        title=title,
        box=box.ROUNDED,
        show_lines=True,
        highlight=True,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Título / Text", style="cyan", max_width=30)
    table.add_column("ControlType", style="green", max_width=22)
    table.add_column("ClassName", style="yellow", max_width=22)
    table.add_column("AutomationId", style="magenta", max_width=22)
    table.add_column("Vis", width=3)
    table.add_column("Hab", width=3)
    table.add_column("Rect", style="dim", max_width=24)

    for i, c in enumerate(controls):
        table.add_row(
            str(i),
            c.get("title", "")[:30],
            c.get("control_type", ""),
            c.get("class_name", ""),
            c.get("auto_id", ""),
            "✓" if c.get("visible") else "✗",
            "✓" if c.get("enabled") else "✗",
            c.get("rect", ""),
        )
    return table


# ---------------------------------------------------------------------------
# Modo 1: Listar janelas abertas
# ---------------------------------------------------------------------------

def list_windows(backend: str = "win32") -> list[dict]:
    """Lista todas as janelas top-level visíveis."""
    desktop = Desktop(backend=backend)
    windows = desktop.windows(visible_only=True)
    result = []
    for w in windows:
        try:
            info = {
                "title": w.window_text(),
                "class_name": w.element_info.class_name,
                "pid": w.element_info.process_id,
                "handle": hex(w.handle) if hasattr(w, "handle") else "",
            }
            if info["title"].strip():
                result.append(info)
        except Exception:
            continue
    return result


def cmd_list_windows() -> None:
    """
    Lista todas as janelas abertas no Windows.
    Nao pede nenhuma entrada — exibe direto.
    """
    console.print(Panel("[bold cyan]Janelas abertas no Windows[/bold cyan]"))
    console.print("[dim]Buscando janelas visiveis...[/dim]")

    windows = list_windows(DEFAULT_BACKEND)

    if not windows:
        console.print("[red]Nenhuma janela visivel encontrada.[/red]")
        return

    table = Table(box=box.ROUNDED, show_lines=False)
    table.add_column("#", width=4, style="dim")
    table.add_column("Titulo da Janela", style="cyan", min_width=30)
    table.add_column("ClassName", style="yellow", max_width=25)
    table.add_column("PID", style="dim", width=8)

    for i, w in enumerate(windows):
        table.add_row(str(i), w["title"], w["class_name"], str(w["pid"]))

    console.print(table)
    console.print(f"\n[dim]Total: {len(windows)} janelas visiveis[/dim]")
    console.print(
        "\n[yellow]DICA:[/yellow] Use o [bold]Modo 2[/bold] e informe parte do titulo "
        "para ver os controles internos de uma janela especifica."
    )


# ---------------------------------------------------------------------------
# Modo 2: Dump completo de controles de uma janela
# ---------------------------------------------------------------------------

def cmd_dump_window() -> None:
    """
    Faz dump de todos os controles de uma janela especifica.
    Digite parte do titulo da janela quando solicitado.
    """
    console.print(Panel(
        "[bold cyan]Dump de controles de uma janela[/bold cyan]\n"
        "[dim]Digite parte do titulo da janela do ERP (ex: SSPlus, Contas, OS)[/dim]"
    ))

    title_re = Prompt.ask("Parte do titulo da janela")

    console.print(f"\n[yellow]Buscando janela com '{title_re}'...[/yellow]")

    try:
        desktop = Desktop(backend=DEFAULT_BACKEND)
        window = desktop.window(title_re=f".*{title_re}.*")
        window.wait("visible", timeout=5)
    except Exception as exc:
        console.print(f"[red]Janela nao encontrada: {exc}[/red]")
        console.print("[dim]Dica: use o Modo 1 para ver os titulos exatos das janelas abertas.[/dim]")
        return

    console.print(f"[green]Janela encontrada: [bold]{window.window_text()}[/bold][/green]\n")

    # Coleta todos os descendentes
    try:
        descendants = window.descendants(depth=6)
    except Exception:
        descendants = window.children()

    controls = []
    for ctrl in descendants:
        try:
            info = _control_to_dict(ctrl)
            if not info.get("visible"):
                continue
            if not info.get("title") and not info.get("auto_id") and not info.get("class_name"):
                continue  # ignora controles sem identificacao
            controls.append(info)
        except Exception:
            continue

    console.print(_render_controls_table(
        controls,
        title=f"Controles visiveis de '{window.window_text()}'"
    ))
    console.print(f"\n[dim]Total: {len(controls)} controles visiveis[/dim]")
    console.print(
        "\n[yellow]DICA:[/yellow] Anote o [bold]title[/bold] e/ou [bold]AutomationId[/bold] "
        "de cada campo e preencha no ssplus_default.yaml"
    )

    if Confirm.ask("\nSalvar dump como JSON?", default=False):
        import json
        output_path = CONTROL_MAP_DIR / f"dump_{title_re.replace(' ', '_')}.json"
        CONTROL_MAP_DIR.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(controls, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Salvo em: {output_path}[/green]")


# ---------------------------------------------------------------------------
# Modo 3: Hover mode — inspeciona controle sob o cursor
# ---------------------------------------------------------------------------

def cmd_hover_inspect() -> None:
    """
    Monitora o controle sob o cursor em tempo real.
    IMPORTANTE: apenas MOVA o mouse — nao clique!
    """
    console.print(Panel(
        "[bold cyan]Hover Mode — Inspecionar controles sob o cursor[/bold cyan]\n"
        "\n"
        "[bold yellow]Como usar:[/bold yellow]\n"
        "  1. Esta janela do terminal pode ficar em segundo plano\n"
        "  2. Va ate a janela do ERP SSPlus\n"
        "  3. MOVA o mouse lentamente sobre os campos, botoes e menus\n"
        "  4. Volte aqui para ver as informacoes capturadas\n"
        "  5. Pressione [bold]Ctrl+C[/bold] para parar\n"
        "\n"
        "[red]NAO CLIQUE[/red] — o mouse apenas passando por cima ja captura."
    ))

    input("\nPressione ENTER para comecar a monitorar...")
    console.print("\n[green]Monitorando... mova o mouse sobre os campos do ERP.[/green]")
    console.print("[dim](Ctrl+C para parar)[/dim]\n")

    last_sig = ""
    counter = 0
    try:
        import pyautogui
        while True:
            x, y = pyautogui.position()
            try:
                desktop = Desktop(backend=DEFAULT_BACKEND)
                ctrl = desktop.from_point(x, y)
                info = _control_to_dict(ctrl)
                sig = f"{info['title']}|{info['control_type']}|{info['auto_id']}|{info['class_name']}"
                if sig != last_sig and (info['title'] or info['auto_id'] or info['class_name']):
                    last_sig = sig
                    counter += 1
                    console.print(
                        f"[dim][{counter:03d}] pos=({x},{y})[/dim]  "
                        f"[cyan]title=[/cyan][bold]\"{info['title']}\"|[/bold]  "
                        f"[green]type=[/green]{info['control_type']}  "
                        f"[yellow]class=[/yellow]{info['class_name']}  "
                        f"[magenta]auto_id=[/magenta][bold]\"{info['auto_id']}\"[/bold]"
                    )
            except Exception:
                pass
            import time
            time.sleep(0.25)
    except KeyboardInterrupt:
        console.print(f"\n[dim]Hover mode encerrado. {counter} controles capturados.[/dim]")
        console.print(
            "\n[yellow]Proximo passo:[/yellow] edite [bold]control_maps/ssplus_default.yaml[/bold]\n"
            "com os valores de [bold]title[/bold] e [bold]auto_id[/bold] que voce anotou."
        )


# ---------------------------------------------------------------------------
# Modo 4: Buscar controle por texto ou AutomationId
# ---------------------------------------------------------------------------

def cmd_search_control() -> None:
    console.print(Panel("[bold cyan]Busca de Controle[/bold cyan]"))
    backend = Prompt.ask("Backend", choices=["win32", "uia"], default="win32")
    window_re = Prompt.ask("Substring do título da janela")
    search_term = Prompt.ask("Texto ou AutomationId a buscar (substring, case-insensitive)")

    try:
        desktop = Desktop(backend=backend)
        window = desktop.window(title_re=f".*{window_re}.*")
        window.wait("visible", timeout=5)
    except Exception as exc:
        console.print(f"[red]Janela não encontrada: {exc}[/red]")
        return

    term_lower = search_term.lower()
    found = []
    for ctrl in window.descendants(depth=8):
        try:
            info = _control_to_dict(ctrl)
            if (
                term_lower in info.get("title", "").lower()
                or term_lower in info.get("auto_id", "").lower()
                or term_lower in info.get("class_name", "").lower()
            ):
                found.append(info)
        except Exception:
            continue

    if found:
        console.print(_render_controls_table(found, f"Resultados para '{search_term}'"))
    else:
        console.print(f"[red]Nenhum controle encontrado com '{search_term}'[/red]")


# ---------------------------------------------------------------------------
# Modo 5: Assistente de mapeamento YAML
# ---------------------------------------------------------------------------

_FLOW_TEMPLATES = {
    "BAIXA_CONTAS_PAGAR": {
        "_comment": "Mapeamento para o fluxo de Baixa no Contas a Pagar",
        "menu": {
            "financeiro": {"title": "???", "control_type": "MenuItem"},
            "contas_pagar": {"title": "???", "control_type": "MenuItem"},
            "baixa_titulos": {"title": "???", "control_type": "MenuItem"},
        },
        "tela_pesquisa": {
            "campo_duplicata_id": {"title": "???", "control_type": "Edit", "auto_id": "???"},
            "botao_pesquisar": {"title": "???", "control_type": "Button"},
            "lista_resultados": {"control_type": "List", "auto_id": "???"},
        },
        "tela_baixa": {
            "campo_data_pagamento": {"title": "???", "control_type": "Edit", "auto_id": "???"},
            "campo_valor_pago": {"title": "???", "control_type": "Edit", "auto_id": "???"},
            "campo_conta": {"title": "???", "control_type": "Edit", "auto_id": "???"},
            "campo_observacao": {"title": "???", "control_type": "Edit", "auto_id": "???"},
            "botao_confirmar": {"title": "???", "control_type": "Button"},
            "hotkey_confirmar": "F9",
        },
        "popup_confirmacao": {
            "title_re": "Confirmação|Sucesso",
            "regex_protocolo": r"Protocolo[:\s]+(\w+)",
        },
    },
    "OBSERVACAO_OS": {
        "_comment": "Mapeamento para o fluxo de Observação em Ordem de Serviço",
        "menu": {
            "servicos": {"title": "???", "control_type": "MenuItem"},
            "ordem_servico": {"title": "???", "control_type": "MenuItem"},
        },
        "tela_pesquisa": {
            "campo_os_id": {"title": "???", "control_type": "Edit", "auto_id": "???"},
            "botao_pesquisar": {"title": "???", "control_type": "Button"},
            "lista_resultados": {"control_type": "List", "auto_id": "???"},
        },
        "tela_os": {
            "campo_observacao": {
                "titles_to_try": ["???", "???"],
                "control_type": "Edit",
                "auto_id": "???",
            },
            "botao_anexar": {"title": "???", "control_type": "Button"},
            "botao_salvar": {"title": "???", "control_type": "Button"},
            "hotkey_salvar": "^s",
        },
        "dialog_arquivo": {
            "title_re": "Abrir|Open",
            "class_name": "#32770",
            "campo_nome": {"control_type": "Edit"},
            "botao_abrir": {"title": "&Abrir"},
        },
    },
}

def cmd_generate_yaml() -> None:
    console.print(Panel(
        "[bold cyan]Assistente de Geração de Control Map YAML[/bold cyan]\n"
        "Use junto com o Modo 3 (Hover) ou Modo 4 (Busca) para preencher os valores.\n"
        "[dim]Este assistente gera um template YAML com os campos que precisam ser mapeados.[/dim]"
    ))

    flow = Prompt.ask(
        "Qual fluxo mapear?",
        choices=list(_FLOW_TEMPLATES.keys()),
        default="BAIXA_CONTAS_PAGAR",
    )

    erp_name = Prompt.ask("Nome do ERP/perfil", default="ssplus_default")
    backend = Prompt.ask("Backend pywinauto", choices=["win32", "uia"], default="win32")
    window_title_re = Prompt.ask("Regex do título da janela principal do ERP", default="SSPlus")

    output = {
        "erp_profile": erp_name,
        "backend": backend,
        "main_window_title_re": f".*{window_title_re}.*",
        "flows": {
            flow: _FLOW_TEMPLATES[flow]
        },
        "known_popups": {
            "dismiss_titles": ["Aviso", "Atenção", "Informação", "Erro", "Confirmação"],
            "dismiss_buttons": ["OK", "&OK", "Fechar", "Cancelar", "&Fechar"],
        },
    }

    CONTROL_MAP_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CONTROL_MAP_DIR / f"{erp_name}.yaml"

    yaml_str = yaml.dump(output, allow_unicode=True, sort_keys=False, indent=2)

    console.print(Panel(Syntax(yaml_str, "yaml", theme="monokai"), title=f"Gerado: {output_path.name}"))

    if Confirm.ask(f"\nSalvar em {output_path}?", default=True):
        output_path.write_text(yaml_str, encoding="utf-8")
        console.print(f"[green]✓ Salvo em: {output_path}[/green]")
        console.print(
            "\n[bold yellow]PRÓXIMOS PASSOS:[/bold yellow]\n"
            f"  1. Abra o ERP SSPlus\n"
            f"  2. Use o Modo 3 (hover) ou Modo 4 (busca) para descobrir os valores reais\n"
            f"  3. Substitua todos os [bold red]\"???\"[/bold red] no arquivo {output_path.name}\n"
            f"  4. Execute o Modo 6 para validar o mapeamento\n"
        )


# ---------------------------------------------------------------------------
# Modo 6: Validar control_map.yaml
# ---------------------------------------------------------------------------

def cmd_validate_map() -> None:
    console.print(Panel("[bold cyan]Validação de Control Map YAML[/bold cyan]"))

    yaml_files = list(CONTROL_MAP_DIR.glob("*.yaml"))
    if not yaml_files:
        console.print(f"[red]Nenhum arquivo .yaml encontrado em {CONTROL_MAP_DIR}[/red]")
        return

    for i, f in enumerate(yaml_files):
        console.print(f"  [{i}] {f.name}")

    idx = IntPrompt.ask("Selecione o arquivo", default=0)
    map_path = yaml_files[idx]

    with open(map_path, encoding="utf-8") as f:
        control_map = yaml.safe_load(f)

    console.print(f"\n[yellow]Carregado: {map_path.name}[/yellow]")

    backend = control_map.get("backend", "win32")
    title_re = control_map.get("main_window_title_re", ".*SSPlus.*")

    try:
        desktop = Desktop(backend=backend)
        window = desktop.window(title_re=title_re)
        window.wait("visible", timeout=8)
        console.print(f"[green]✓ Janela encontrada: {window.window_text()!r}[/green]")
    except Exception as exc:
        console.print(f"[red]✗ Janela não encontrada ({title_re}): {exc}[/red]")
        return

    # Valida cada controle mapeado
    errors = 0
    successes = 0

    def _validate_control(spec: dict, path: str) -> None:
        nonlocal errors, successes
        if not isinstance(spec, dict) or "_comment" in path:
            return

        title = spec.get("title", "")
        auto_id = spec.get("auto_id", "")
        ctrl_type = spec.get("control_type", "")

        if not title and not auto_id:
            return  # Nada para validar
        if title == "???" or auto_id == "???":
            console.print(f"  [yellow]⚠ {path}: valor '???' ainda não mapeado[/yellow]")
            return

        try:
            lookup: dict = {}
            if ctrl_type:
                lookup["control_type"] = ctrl_type
            if title:
                lookup["title"] = title
            if auto_id:
                lookup["auto_id"] = auto_id

            ctrl = window.child_window(**lookup)
            ctrl.wait("exists", timeout=3)
            console.print(f"  [green]✓ {path}[/green]")
            successes += 1
        except Exception as exc:
            console.print(f"  [red]✗ {path}: {exc}[/red]")
            errors += 1

    # Percorre o YAML recursivamente
    def _walk(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            if "control_type" in obj or "auto_id" in obj or "title" in obj:
                _validate_control(obj, prefix)
            else:
                for k, v in obj.items():
                    _walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{prefix}[{i}]")

    flows = control_map.get("flows", {})
    _walk(flows, "flows")

    console.print(f"\n[bold]Resultado: [green]{successes} OK[/green] | [red]{errors} FALHAS[/red][/bold]")
    if errors == 0:
        console.print("[bold green]✓ Mapeamento válido! Pronto para uso.[/bold green]")
    else:
        console.print("[bold red]✗ Corrija os controles marcados acima e re-valide.[/bold red]")


# ---------------------------------------------------------------------------
# Menu principal
# ---------------------------------------------------------------------------

COMMANDS = {
    "1": ("Listar janelas abertas", cmd_list_windows),
    "2": ("Dump de controles de uma janela", cmd_dump_window),
    "3": ("Hover mode — inspecionar sob o cursor", cmd_hover_inspect),
    "4": ("Buscar controle por texto/AutomationId", cmd_search_control),
    "5": ("Gerar template YAML de mapeamento", cmd_generate_yaml),
    "6": ("Validar control_map.yaml existente", cmd_validate_map),
}


def main() -> None:
    console.print(Panel(
        "[bold magenta]auto-adm — Inspector de Controles Win32[/bold magenta]\n"
        "[dim]Ferramenta para mapear controles do ERP SSPlus[/dim]",
        expand=False,
    ))

    while True:
        console.print()
        for key, (desc, _) in COMMANDS.items():
            console.print(f"  [[cyan]{key}[/cyan]] {desc}")
        console.print("  [[red]q[/red]] Sair")
        console.print()

        choice = Prompt.ask("Escolha", choices=[*COMMANDS.keys(), "q"])

        if choice == "q":
            console.print("[dim]Até logo![/dim]")
            break

        _, cmd_fn = COMMANDS[choice]
        try:
            cmd_fn()
        except KeyboardInterrupt:
            console.print("\n[dim]Interrompido.[/dim]")
        except Exception as exc:
            console.print(f"\n[red]Erro: {exc}[/red]")


if __name__ == "__main__":
    main()
