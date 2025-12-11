#!/usr/bin/env python3
"""Simple test to check Textual functionality."""

import sys
try:
    from textual.app import App
    from textual.widgets import Static
    
    class SimpleApp(App):
        def compose(self):
            yield Static("Hello World!")
    
    print("Starting Textual app...")
    app = SimpleApp()
    app.run()
    print("App finished")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
