#!/usr/bin/env python3
"""ChefChat Visual Taste Test 🍽️
=================================

Level 2: Visual Inspection Script

Renders UI components in all modes WITHOUT making API calls.
Use this to verify styling, colors, and layout.

Usage:
    python scripts/visual_taste_test.py
    # or
    uv run python scripts/visual_taste_test.py
"""

from __future__ import annotations

from pathlib import Path
import sys

# Ensure vibe package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from vibe.cli.easter_eggs import (
    get_kitchen_status,
    get_modes_display,
    get_random_roast,
    get_random_wisdom,
)
from vibe.cli.mode_errors import create_write_blocked_error
from vibe.cli.mode_manager import MODE_CONFIGS, MODE_TIPS, ModeManager, VibeMode
from vibe.cli.plating import generate_plating, generate_taste_test
from vibe.cli.ui_components import (
    COLORS,
    ApprovalDialog,
    HeaderData,
    HeaderDisplay,
    ModeTransitionDisplay,
    ResponseDisplay,
    StatusBar,
)

console = Console()


def section_header(title: str) -> None:
    """Print a section header."""
    console.print()
    console.print(
        Rule(f"[bold {COLORS['primary']}]{title}[/]", style=COLORS["secondary"])
    )
    console.print()


def test_headers_all_modes() -> None:
    """Render the header component in all 5 modes."""
    section_header("🧪 TEST 1: Headers in All Modes")

    for mode in VibeMode:
        config = MODE_CONFIGS[mode]

        data = HeaderData(
            model="mistral-large-latest",
            mode_indicator=mode.value.upper(),
            mode_emoji=config.emoji,
            workdir="~/chefchat/ChefChat",
            version="1.0.0",
            context_used=12500,
            context_max=32000,
        )

        console.print(f"[dim]Mode: {mode.value.upper()}[/dim]")
        console.print(HeaderDisplay(data).render())
        console.print()


def test_mode_transitions() -> None:
    """Render mode transition displays."""
    section_header("🧪 TEST 2: Mode Transitions")

    manager = ModeManager(initial_mode=VibeMode.NORMAL)

    # Simulate cycling through all modes
    transitions = [
        ("NORMAL", "AUTO"),
        ("AUTO", "PLAN"),
        ("PLAN", "YOLO"),
        ("YOLO", "ARCHITECT"),
        ("ARCHITECT", "NORMAL"),
    ]

    for old, new in transitions:
        # Set manager to new mode for correct config
        new_mode = VibeMode(new.lower())
        manager.set_mode(new_mode)

        # Use actual MODE_TIPS for this mode instead of placeholders
        tips = MODE_TIPS.get(new_mode, [])

        console.print(
            ModeTransitionDisplay.render(
                old_mode=old,
                new_mode=new,
                new_emoji=manager.config.emoji,
                description=manager.config.description,
                tips=tips,
            )
        )


def test_response_display() -> None:
    """Render AI response styling."""
    section_header("🧪 TEST 3: Response Display (Plating)")

    # Sample markdown response
    sample_response = """
## Analysis Complete 🔍

I've reviewed the codebase and found the following:

### Key Findings

1. **Architecture**: The mode system is well-structured
2. **Safety**: Gatekeeper logic prevents writes in PLAN mode
3. **UI**: Rich components render correctly

```python
def example_function():
    return "This is syntax highlighted"
```

### Next Steps

- Run the test suite
- Verify visual styling
- Check error handling
"""

    console.print(ResponseDisplay.render_response(Markdown(sample_response)))
    console.print()

    # Tool calls
    console.print("[dim]Tool call indicators:[/dim]")
    console.print(ResponseDisplay.render_tool_call("read_file"))
    console.print(ResponseDisplay.render_tool_result(True))
    console.print(ResponseDisplay.render_tool_call("write_file"))
    console.print(ResponseDisplay.render_tool_result(False, "Blocked by PLAN mode"))


def test_error_panels() -> None:
    """Render error panels for mode violations."""
    section_header("🧪 TEST 4: Mode Violation Error Panels")

    # Test error in PLAN mode
    error = create_write_blocked_error(
        "write_file", VibeMode.PLAN, {"path": "/src/main.py"}
    )

    console.print(
        Panel(
            Markdown(error.to_display_message()),
            title=f"[{COLORS['error']}]⛔ Mode Violation[/{COLORS['error']}]",
            border_style=COLORS["error"],
            padding=(1, 2),
        )
    )
    console.print()

    # Test error in ARCHITECT mode
    error2 = create_write_blocked_error(
        "bash", VibeMode.ARCHITECT, {"command": "rm -rf /"}
    )

    console.print(
        Panel(
            Markdown(error2.to_display_message()),
            title=f"[{COLORS['error']}]⛔ Mode Violation[/{COLORS['error']}]",
            border_style=COLORS["error"],
            padding=(1, 2),
        )
    )


def test_approval_dialog() -> None:
    """Render the tool approval dialog."""
    section_header("🧪 TEST 5: Approval Dialog")

    # Sample tool call
    args = {
        "path": "/home/chef/project/src/main.py",
        "content": "def hello():\n    print('Hello, World!')",
    }

    args_syntax = Syntax(
        '{\n  "path": "/home/chef/project/src/main.py",\n  "content": "def hello():..."\n}',
        "json",
        theme="monokai",
        line_numbers=False,
    )

    console.print(ApprovalDialog.render("write_file", args_syntax))


def test_status_bar() -> None:
    """Render the status bar."""
    section_header("🧪 TEST 6: Status Bar")

    console.print("[dim]Auto-approve OFF:[/dim]")
    console.print(StatusBar.render(auto_approve=False))
    console.print()

    console.print("[dim]Auto-approve ON:[/dim]")
    console.print(StatusBar.render(auto_approve=True))


def test_easter_eggs() -> None:
    """Render easter egg commands output."""
    section_header("🧪 TEST 7: Easter Eggs")

    manager = ModeManager(initial_mode=VibeMode.YOLO)

    # /chef command
    console.print("[dim]/chef command:[/dim]")
    status = get_kitchen_status(manager)
    console.print(
        Panel(
            Markdown(status),
            title=f"[{COLORS['primary']}]👨‍🍳 Kitchen Status[/{COLORS['primary']}]",
            border_style=COLORS["primary"],
        )
    )
    console.print()

    # /modes command
    console.print("[dim]/modes command:[/dim]")
    modes = get_modes_display(manager)
    console.print(
        Panel(
            Markdown(modes),
            title=f"[{COLORS['primary']}]🔄 Available Modes[/{COLORS['primary']}]",
            border_style=COLORS["secondary"],
        )
    )
    console.print()

    # /wisdom command
    console.print("[dim]/wisdom command:[/dim]")
    wisdom = get_random_wisdom()
    console.print(
        Panel(
            Markdown(wisdom),
            title=f"[{COLORS['primary']}]🧠 Chef's Wisdom[/{COLORS['primary']}]",
            border_style=COLORS["secondary"],
        )
    )
    console.print()

    # /roast command
    console.print("[dim]/roast command:[/dim]")
    roast = get_random_roast()
    console.print(
        Panel(
            Markdown(roast),
            title=f"[{COLORS['error']}]🔥 Chef Ramsay Says[/{COLORS['error']}]",
            border_style=COLORS["error"],
        )
    )


def test_plating() -> None:
    """Render plating displays."""
    section_header("🧪 TEST 8: Plating Presentation")

    # Mock stats
    class MockStats:
        steps = 5
        session_total_llm_tokens = 12500
        session_cost = 0.0125
        tool_calls_succeeded = 3

    for mode in [VibeMode.NORMAL, VibeMode.YOLO]:
        console.print(f"[dim]Plating in {mode.value.upper()} mode:[/dim]")
        manager = ModeManager(initial_mode=mode)
        plating = generate_plating(manager, MockStats())
        console.print(plating)
        console.print()


def test_taste_test() -> None:
    """Render taste test (code review) output."""
    section_header("🧪 TEST 9: Taste Test (Code Review)")

    manager = ModeManager(initial_mode=VibeMode.PLAN)

    taste = generate_taste_test(
        file_path="src/mode_manager.py",
        mode_manager=manager,
        severity=4,  # Good rating
    )

    console.print(Panel(Markdown(taste), border_style=COLORS["secondary"]))


def main() -> None:
    """Run all visual tests."""
    console.print()
    console.print(
        Panel(
            Text.from_markup(
                f"[bold {COLORS['primary']}]🍽️ ChefChat Visual Taste Test[/]\n\n"
                f"[{COLORS['text']}]Rendering all UI components for manual inspection.\n"
                f"No API calls are made during this test.[/]"
            ),
            border_style=COLORS["primary"],
            padding=(1, 2),
        )
    )

    try:
        test_headers_all_modes()
        test_mode_transitions()
        test_response_display()
        test_error_panels()
        test_approval_dialog()
        test_status_bar()
        test_easter_eggs()
        test_plating()
        test_taste_test()

        section_header("✅ Visual Taste Test Complete")

        console.print(
            Panel(
                Text.from_markup(
                    f"[{COLORS['success']}]All UI components rendered successfully![/]\n\n"
                    f"[{COLORS['text']}]Review the output above to verify:[/]\n"
                    f"  • Colors match the ChefChat design system\n"
                    f"  • Emojis display correctly\n"
                    f"  • Panels have proper borders\n"
                    f"  • Error panels are visually distinct\n"
                    f"  • Mode indicators show correct state"
                ),
                title=f"[{COLORS['success']}]Chef's Verdict[/{COLORS['success']}]",
                border_style=COLORS["success"],
                padding=(1, 2),
            )
        )

    except Exception as e:
        console.print(
            Panel(
                f"[{COLORS['error']}]Error during visual test: {e}[/]",
                border_style=COLORS["error"],
            )
        )
        raise


if __name__ == "__main__":
    main()
