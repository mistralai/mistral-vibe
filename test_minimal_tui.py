#!/usr/bin/env python3
"""Minimal test to isolate TUI launch issues."""

import asyncio
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Static

# Test 1: Basic Textual app
class BasicTestApp(App):
    """Most basic test app."""
    
    def compose(self) -> ComposeResult:
        yield Static("Basic test works!")

# Test 2: App with CSS
class CSSTestApp(App):
    """Test app with CSS path."""
    
    CSS_PATH = Path(__file__).parent / "chefchat/interface/styles.tcss"
    
    def compose(self) -> ComposeResult:
        yield Static("CSS test works!")

# Test 3: App with imports
class ImportTestApp(App):
    """Test app with ChefChat imports."""
    
    def compose(self) -> ComposeResult:
        yield Static("Import test works!")
    
    def on_mount(self) -> None:
        # Try importing ChefChat modules
        try:
            from chefchat.interface.constants import StationStatus
            self.query_one(Static).update("Imports work!")
        except Exception as e:
            self.query_one(Static).update(f"Import failed: {e}")

if __name__ == "__main__":
    import sys
    
    test = sys.argv[1] if len(sys.argv) > 1 else "basic"
    
    match test:
        case "basic":
            app = BasicTestApp()
        case "css":
            app = CSSTestApp()
        case "import":
            app = ImportTestApp()
        case _:
            print("Usage: test_minimal_tui.py [basic|css|import]")
            sys.exit(1)
    
    app.run()
