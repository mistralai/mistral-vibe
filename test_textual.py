#!/usr/bin/env python3
"""Minimal Textual test to diagnose TUI launch issues."""

from textual.app import App, ComposeResult
from textual.widgets import Static

class TestApp(App):
    """Minimal test app."""
    
    def compose(self) -> ComposeResult:
        yield Static("Hello, Textual!", id="hello")

if __name__ == "__main__":
    app = TestApp()
    app.run()
