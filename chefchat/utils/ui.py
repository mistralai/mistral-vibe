from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

# Strakke theme voor de chef
chef_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "chef": "bold magenta"
})

console = Console(theme=chef_theme)

def print_chef(msg: str) -> None:
    """Spreek de chef aan."""
    console.print(f"[chef]CHEF:[/chef] {msg}")

def print_success(msg: str) -> None:
    """Als het gelukt is."""
    console.print(f"[success]LEKKA:[/success] {msg}")

def print_error(msg: str):  # noqa: ANN201
    """Als het misgaat."""
    console.print(f"[error]AI NEEF:[/error] {msg}")
