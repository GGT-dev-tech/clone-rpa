"""
Script auxiliar: gera um relatório de diagnóstico do ambiente Windows
antes de iniciar o mapeamento, verificando se todas as dependências
estão instaladas e se o ERP está acessível.

Uso:
    python tools/check_environment.py
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# Força stdout/stderr em UTF-8 no Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Adiciona src ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

REQUIRED_PACKAGES = {
    "pywinauto": "0.6.8",
    "pyautogui": "0.9.54",
    "cv2": None,          # opencv-python-headless
    "PIL": None,          # Pillow
    "websockets": "12.0",
    "requests": "2.31",
    "pydantic": "2.0",
    "pydantic_settings": "2.0",
    "tenacity": "8.0",
    "structlog": "24.0",
    "rich": "13.0",
    "yaml": None,         # PyYAML
}


def main() -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        console = Console()
    except ImportError:
        print("rich não instalado — execute: pip install rich")
        sys.exit(1)

    console.print("\n[bold cyan]=== auto-adm Worker — Diagnóstico de Ambiente ===[/bold cyan]\n")

    table = Table(box=box.ROUNDED)
    table.add_column("Pacote", style="cyan")
    table.add_column("Status", width=8)
    table.add_column("Versão instalada", style="dim")
    table.add_column("Versão mínima", style="dim")

    all_ok = True
    for pkg, min_ver in REQUIRED_PACKAGES.items():
        try:
            mod = importlib.import_module(pkg)
            ver = getattr(mod, "__version__", "n/a")
            table.add_row(pkg, "[green]OK[/green]", ver, min_ver or "-")
        except ImportError:
            table.add_row(pkg, "[red]FALTA[/red]", "-", min_ver or "-")
            all_ok = False

    console.print(table)

    # Verifica Python
    py_ver = sys.version_info
    py_ok = py_ver >= (3, 11)
    py_color = 'green' if py_ok else 'red'
    console.print(
        f"\nPython: [{py_color}]{sys.version}[/{py_color}]"
    )

    # Verifica control_map
    map_dir = Path(__file__).parent.parent / "control_maps"
    yaml_files = list(map_dir.glob("*.yaml"))
    if yaml_files:
        console.print(f"\nControl maps encontrados: [green]{len(yaml_files)}[/green]")
        import yaml
        for f in yaml_files:
            with open(f, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            unmapped = _count_unmapped(data)
            status = "[red]INCOMPLETO[/red]" if unmapped else "[green]COMPLETO[/green]"
            console.print(f"  - {f.name} [{status}] ({unmapped} campos '???')")
    else:
        console.print(
            "\n[yellow]Nenhum control_map.yaml encontrado.[/yellow]\n"
            "Execute: python tools/inspect_erp.py -> Modo 5"
        )

    console.print()
    if all_ok and py_ok:
        console.print("[bold green]>>> Ambiente OK - pronto para mapeamento![/bold green]")
    else:
        console.print("[bold red]>>> ERROS ENCONTRADOS - corrija antes de continuar.[/bold red]")
        sys.exit(1)


def _count_unmapped(obj) -> int:
    """Conta recursivamente quantos valores '???' existem no dict."""
    count = 0
    if isinstance(obj, dict):
        for v in obj.values():
            count += _count_unmapped(v)
    elif isinstance(obj, list):
        for item in obj:
            count += _count_unmapped(item)
    elif obj == "???":
        count += 1
    return count


if __name__ == "__main__":
    main()
