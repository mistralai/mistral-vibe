#!/usr/bin/env python3
"""Test TUI environment detection."""

import os
import sys

print("=== Environment Check ===")
print(f"TERM: {os.environ.get('TERM', 'not set')}")
print(f"TERM_PROGRAM: {os.environ.get('TERM_PROGRAM', 'not set')}")
print(f"COLORTERM: {os.environ.get('COLORTERM', 'not set')}")
print(f"WINDSURF_CASCADE_TERMINAL: {os.environ.get('WINDSURF_CASCADE_TERMINAL', 'not set')}")
print(f"isatty stdin: {sys.stdin.isatty()}")
print(f"isatty stdout: {sys.stdout.isatty()}")
print(f"isatty stderr: {sys.stderr.isatty()}")

# Try importing and testing Textual
print("\n=== Textual Test ===")
try:
    from textual.app import App
    from textual.widgets import Static
    
    class TestApp(App):
        def compose(self):
            yield Static("Textual works!")
    
    # Set environment for IDE
    os.environ["TEXTUAL"] = ""
    os.environ["FORCE_COLOR"] = "1"
    
    print("Attempting to run Textual app...")
    app = TestApp()
    app.run()
    print("Textual app completed successfully")
except Exception as e:
    print(f"Textual error: {e}")
    import traceback
    traceback.print_exc()
