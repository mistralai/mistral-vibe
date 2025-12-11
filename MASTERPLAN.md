# 🔥 ChefChat Superior Fork — MASTERPLAN

**Version:** 1.0
**Date:** 2025-12-10
**Architect:** Elite Software Architect & Senior Product Manager
**Uitvoerder:** Claude 3 Opus (Opus-ready Action Items)
**Taal:** Nederlands (proza) + English (technical terms)

---

## 🎯 1. Executive Summary: The "Vibe" Vision

### De Visie
ChefChat is niet zomaar een fork van Mistral Vibe CLI. Het is **de definitieve AI-agent CLI-ervaring**:
- **Technisch superieur:** Sneller, veiliger, schonere code dan het origineel.
- **Delightfully fun:** Een CLI die je *wilt* gebruiken — met spinners, kleuren, emoji's, interactieve prompts en Easter eggs die wél bijdragen aan de workflow.
- **Production-ready:** Zero crashes, zero security holes, 100% testbaar.

### Waarom deze fork bestaat
De Mistral Vibe CLI is een solide basis, maar heeft:
1. **Legacy code** (`textual_ui`) die niemand meer gebruikt.
2. **Upstream divergence** die toekomstige merges pijnlijk maakt.
3. **Visuele inconsistenties** tussen `rich` en `prompt_toolkit`.
4. **Een gebrek aan "wow-factor"** — het is functioneel, maar niet *fun*.

ChefChat lost dit op. Wij bouwen de CLI die developers *verdienen*.

---

## 🏗️ 2. Architecture Overhaul

### 2.1 Core Technical Decisions

| **Onderdeel**             | **Huidige situatie**                          | **Nieuwe aanpak**                                      |
|---------------------------|-----------------------------------------------|--------------------------------------------------------|
| **UI Library**            | Hybride: `rich` + `prompt_toolkit` + `textual` | **Consolidatie:** `rich` + `prompt_toolkit` ONLY      |
| **Legacy Code**           | `vibe/cli/textual_ui/` nog aanwezig           | **VERWIJDEREN** (deprecated, niet-REPL interface)      |
| **Upstream Sync**         | Moeilijk door custom patches                 | **Fork-aware strategy:** Modular patches in `/patches` |
| **Error Handling**        | Gemengd: soms crasht, soms stille fails      | **Centralized ErrorHandler** met Rich traceback        |
| **Performance**           | Geen optimalisaties                           | **Async where it matters** (tool calls, API requests)  |
| **Configuration**         | Pydantic models, maar verspreid               | **Single Source of Truth:** `vibe/core/config.py`      |

### 2.2 Nieuwe Directory Structuur (Post-Upgrade)

```
ChefChat/
├── vibe/
│   ├── acp/                    # Agent-Client Protocol (unchanged)
│   ├── cli/
│   │   ├── autocompletion/
│   │   ├── plating.py          # UI helpers (existing)
│   │   ├── easter_eggs.py      # Commands: /chef, /wisdom, /roast
│   │   ├── mode_errors.py      # Mode violation handlers
│   │   ├── repl.py             # Main REPL (ENHANCED in Phase 3)
│   │   └── entrypoint.py       # CLI entry (no more --textual fallback)
│   ├── core/
│   │   ├── agent.py            # Agent logic (gatekeeper intact)
│   │   ├── config.py           # Config (SINGLE SOURCE OF TRUTH)
│   │   ├── error_handler.py    # 🆕 Centralized error handling
│   │   ├── mode_manager.py     # Mode logic (existing, validated)
│   │   ├── system_prompt.py    # System prompts
│   │   ├── llm/
│   │   ├── prompts/
│   │   └── tools/
│   ├── setup/
│   └── utils/
│       ├── async_helpers.py    # 🆕 Async utilities
│       └── ui_helpers.py       # 🆕 Reusable UI components (spinners, bars)
├── tests/
│   ├── chef_unit/              # Unit tests (existing)
│   └── integration/            # 🆕 Integration tests for REPL flows
├── scripts/
│   ├── visual_taste_test.py    # UI inspection (existing)
│   └── benchmark.py            # 🆕 Performance benchmarks
├── docs/
│   ├── ARCHITECTURE.md         # 🆕 Fork architecture
│   └── UPSTREAM_DIVERGENCE.md  # 🆕 What we changed & why
├── MASTERPLAN.md               # Dit document
├── TESTING_MENU.md
└── README.md
```

**Verwijderd:**
- `vibe/cli/textual_ui/` → Volledig weg. REPL is de toekomst.

**Toegevoegd:**
- `vibe/core/error_handler.py` → Centrale error handling.
- `vibe/utils/async_helpers.py` → Async tooling.
- `vibe/utils/ui_helpers.py` → Reusable UI components.
- `tests/integration/` → End-to-end REPL flows.
- `scripts/benchmark.py` → Performance tracking.
- `docs/ARCHITECTURE.md` + `UPSTREAM_DIVERGENCE.md` → Voor toekomstige maintainers.

---

## 🎨 3. The "Fun" Upgrade

ChefChat moet niet alleen werken — het moet *schitteren*. Hieronder de concrete UX-verbeteringen.

### 3.1 Visual Enhancements (Rich Integration)

| **Feature**                  | **Implementation**                                                                 |
|------------------------------|------------------------------------------------------------------------------------|
| **Startup Banner**           | Rich `Panel` met ASCII-art Chef's hat + versie + mode indicator                  |
| **Input Prompt**             | `prompt_toolkit` styled: `🧑‍🍳 [PLAN] ›` (met mode-kleur)                        |
| **Spinners**                 | `rich.spinner.Spinner` tijdens API calls ("🍳 Slicing onions...")                |
| **Progress Bars**            | `rich.progress.Progress` voor lange taken (batch tool calls)                      |
| **Mode Transitions**         | Full-screen `Panel` met mode-tips (niet placeholder text!)                       |
| **Error Panels**             | `rich.panel.Panel` met `border_style="red"` + stack trace toggle                 |
| **Tool Execution Feedback**  | Real-time `Live` display: `[✓] File created`, `[✗] Permission denied`            |
| **Footer Status Bar**        | `rich.table.Table`: Mode | Token count | Time elapsed                            |

### 3.2 Interactieve Prompts

| **Use Case**              | **Tool**                          | **Example**                                      |
|---------------------------|-----------------------------------|--------------------------------------------------|
| **Tool Approval**         | `prompt_toolkit.PromptSession`    | `⚠️ Edit file.py? [y/n/view/skip-all]`          |
| **Multi-select (future)** | `questionary` (optioneel)         | Selecteer tools om toe te staan in PLAN mode    |
| **Mode Cycling**          | `Shift+Tab` (existing)            | Visual "Mode Switcher" panel                     |

### 3.3 Easter Eggs (Boosted)

De bestaande `/chef`, `/wisdom`, `/roast` zijn goed — maar:
1. **Maak ze visueel rijker:** Gebruik `rich.align.Align` voor centered quotes.
2. **Voeg `/stats` toe:** Token usage, uptime, tools run (Chef's "Menu du Jour").
3. **Voeg `/history` toe:** Recent commands (met syntax highlighting).

---

## 📋 4. Action Plan (Step-by-Step)

Dit is de gedetailleerde roadmap voor Opus. Elke taak is atomair en testbaar.

---

### **PHASE 1: Foundation & Cleanup**
*Doel: Fix audit warnings, remove legacy code, establish clean base.*

#### Task 1.1: Remove Legacy TUI
**Files:**
- Delete: `vibe/cli/textual_ui/` (entire directory)
- Modify: `vibe/cli/entrypoint.py`

**Steps:**
1. Open `vibe/cli/entrypoint.py`.
2. Find the fallback logic that launches `textual_ui` (likely in the `main()` function).
3. **Remove** all references to `textual_ui` imports and the fallback branch.
4. Ensure `repl.py` is the ONLY interactive mode launched via `--repl`.
5. Update the `--help` text to reflect this change.

**Verification:**
```bash
uv run vibe --help
# Should NOT mention "textual" mode anymore
```

#### Task 1.2: Create Centralized Error Handler
**File:** `vibe/core/error_handler.py` (NEW)

**Implementation:**
```python
from rich.console import Console
from rich.panel import Panel
from rich.traceback import Traceback
from typing import Optional

console = Console()

class ChefErrorHandler:
    """Centralized error handling with Rich formatting."""

    @staticmethod
    def display_error(
        error: Exception,
        context: str = "Operation",
        show_traceback: bool = False
    ) -> None:
        """Display a formatted error panel."""
        error_panel = Panel(
            f"[red bold]{type(error).__name__}[/red bold]\n{str(error)}",
            title=f"❌ {context} Failed",
            border_style="red"
        )
        console.print(error_panel)

        if show_traceback:
            console.print(Traceback.from_exception(type(error), error, error.__traceback__))

    @staticmethod
    def display_warning(message: str, context: str = "Warning") -> None:
        """Display a formatted warning."""
        warning_panel = Panel(
            message,
            title=f"⚠️  {context}",
            border_style="yellow"
        )
        console.print(warning_panel)
```

**Integration Points:**
- `vibe/cli/repl.py` → Catch exceptions in main loop, call `ChefErrorHandler.display_error()`
- `vibe/core/agent.py` → Replace print statements with `ChefErrorHandler.display_warning()`

#### Task 1.3: Document Upstream Divergence
**File:** `docs/UPSTREAM_DIVERGENCE.md` (NEW)

**Content:**
```markdown
# Upstream Divergence — ChefChat vs. mistral-vibe

## Why We Forked
ChefChat introduces the `ModeManager` safety system, which requires deep modifications to the Agent core. This makes merging upstream changes non-trivial.

## Modified Files
| File | Reason | Merge Strategy |
|------|--------|----------------|
| `vibe/core/agent.py` | Gatekeeper logic for tool blocking | Manual diff, preserve our `_should_execute_tool` |
| `vibe/core/system_prompt.py` | Mode-specific prompts | Cherry-pick upstream improvements, keep mode logic |
| `vibe/cli/repl.py` | Complete rewrite for Rich/prompt_toolkit | No merge needed (our custom code) |

## Merge Checklist
Before pulling from upstream:
1. Run all tests: `pytest tests/chef_unit`
2. Compare diffs for `agent.py` and `system_prompt.py`
3. Test PLAN mode: Verify write operations are blocked
4. Test REPL: Ensure visual rendering is intact
```

**Update README.md:**
Add a section:
```markdown
## 🍴 Fork Status
ChefChat is a production-ready fork of [mistral-vibe](https://github.com/WantedChef/ChefChat).
See [docs/UPSTREAM_DIVERGENCE.md](docs/UPSTREAM_DIVERGENCE.md) for merge guidelines.
```

#### Task 1.4: Add Integration Test for PLAN Mode Safety
**File:** `tests/chef_unit/test_plan_mode_safety.py` (NEW)

**Implementation:**
```python
import pytest
from unittest.mock import Mock, patch
from vibe.core.agent import Agent, ToolDecision
from vibe.core.mode_manager import ModeManager, Mode

def test_plan_mode_blocks_write_tools():
    """Verify Agent ALWAYS blocks write tools in PLAN mode."""
    # Setup
    mode_manager = ModeManager()
    mode_manager.set_mode(Mode.PLAN)

    agent = Agent(mode_manager=mode_manager)

    # Mock tool call (write operation)
    mock_tool = Mock()
    mock_tool.name = "edit_file"
    mock_tool.is_write_operation = True

    # Execute
    decision = agent._should_execute_tool(mock_tool)

    # Verify
    assert decision == ToolDecision.SKIP, "Write tools MUST be blocked in PLAN mode"

def test_execute_mode_allows_write_tools():
    """Verify Agent allows write tools in EXECUTE mode."""
    mode_manager = ModeManager()
    mode_manager.set_mode(Mode.EXECUTE)

    agent = Agent(mode_manager=mode_manager)
    mock_tool = Mock()
    mock_tool.name = "edit_file"
    mock_tool.is_write_operation = True

    # In EXECUTE mode, decision depends on user approval, not blanket SKIP
    decision = agent._should_execute_tool(mock_tool)
    assert decision != ToolDecision.SKIP
```

**Run:**
```bash
pytest tests/chef_unit/test_plan_mode_safety.py -v
```

---

### **PHASE 2: Optimization & Logic**
*Doel: Performance wins, async operations, cleaner code.*

#### Task 2.1: Implement Async Helpers
**File:** `vibe/utils/async_helpers.py` (NEW)

**Implementation:**
```python
import asyncio
from typing import TypeVar, Callable, List, Any
from rich.progress import Progress, SpinnerColumn, TextColumn

T = TypeVar('T')

async def run_with_spinner(
    coro: Callable[..., T],
    message: str = "Processing..."
) -> T:
    """Run an async task with a Rich spinner."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True
    ) as progress:
        task = progress.add_task(message, total=None)
        result = await coro
        progress.remove_task(task)
        return result

async def batch_execute(tasks: List[Callable], max_concurrent: int = 5) -> List[Any]:
    """Execute multiple tasks with concurrency limit."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def limited_task(task):
        async with semaphore:
            return await task()

    return await asyncio.gather(*[limited_task(t) for t in tasks])
```

**Usage in `vibe/core/agent.py`:**
- Wrap API calls to Mistral in `run_with_spinner()` for visual feedback.
- Use `batch_execute()` for parallel tool calls (when safe).

#### Task 2.2: Optimize Tool Execution Feedback
**File:** `vibe/core/agent.py`

**Modification:**
After a tool executes, use `rich.live.Live` to show real-time status:

```python
from rich.live import Live
from rich.table import Table

def _execute_tool_with_feedback(self, tool):
    """Execute tool with live visual feedback."""
    table = Table.grid()
    table.add_row("[cyan]Tool:[/cyan]", tool.name)
    table.add_row("[cyan]Status:[/cyan]", "[yellow]Running...[/yellow]")

    with Live(table, refresh_per_second=4) as live:
        try:
            result = tool.execute()
            table.rows[1] = ("[cyan]Status:[/cyan]", "[green]✓ Success[/green]")
            live.update(table)
            return result
        except Exception as e:
            table.rows[1] = ("[cyan]Status:[/cyan]", f"[red]✗ {str(e)}[/red]")
            live.update(table)
            raise
```

#### Task 2.3: Refactor Config Loading
**File:** `vibe/core/config.py`

**Goal:** Ensure ALL config is loaded from this ONE file. No scattered Pydantic models.

**Steps:**
1. Audit all imports: `grep -r "from pydantic import" vibe/`
2. Consolidate models in `config.py`.
3. Create a singleton `get_config()` function:

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def get_config() -> ChefChatConfig:
    """Get the global config (cached)."""
    return ChefChatConfig.load_from_file()
```

4. Replace direct config loads everywhere with `get_config()`.

#### Task 2.4: Performance Benchmarking Script
**File:** `scripts/benchmark.py` (NEW)

**Implementation:**
```python
#!/usr/bin/env python3
"""Benchmark ChefChat performance."""
import time
from vibe.core.agent import Agent
from vibe.core.mode_manager import ModeManager

def benchmark_mode_switch():
    """Measure mode switching speed."""
    mm = ModeManager()
    start = time.perf_counter()
    for _ in range(1000):
        mm.set_mode(Mode.PLAN)
        mm.set_mode(Mode.EXECUTE)
    end = time.perf_counter()
    print(f"Mode switches (1000x): {(end - start) * 1000:.2f}ms")

def benchmark_tool_gatekeeper():
    """Measure gatekeeper decision speed."""
    agent = Agent()
    start = time.perf_counter()
    for _ in range(1000):
        agent._should_execute_tool(mock_tool)
    end = time.perf_counter()
    print(f"Gatekeeper checks (1000x): {(end - start) * 1000:.2f}ms")

if __name__ == "__main__":
    benchmark_mode_switch()
    benchmark_tool_gatekeeper()
```

**Run:**
```bash
python scripts/benchmark.py
# Target: < 1ms per operation
```

---

### **PHASE 3: The "Vibe" Injection (UI/UX Upgrades)**
*Doel: Make it BEAUTIFUL and FUN.*

#### Task 3.1: Enhanced REPL Startup
**File:** `vibe/cli/repl.py`

**Modify `main()` function:**

```python
from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich import box

console = Console()

def show_startup_banner():
    """Display the ChefChat startup banner."""
    banner_text = """
    👨‍🍳 ChefChat v2.0
    The Tastiest AI Agent CLI
    """

    panel = Panel(
        Align.center(banner_text),
        box=box.DOUBLE,
        border_style="cyan",
        subtitle="Type /chef for help · Shift+Tab to switch modes"
    )
    console.print(panel)
    console.print()

# Call at startup:
show_startup_banner()
```

#### Task 3.2: Dynamic Footer Status Bar
**File:** `vibe/cli/repl.py`

**Add a `StatusBar` class:**

```python
from rich.table import Table
from rich.live import Live

class StatusBar:
    def __init__(self, mode_manager, start_time):
        self.mode_manager = mode_manager
        self.start_time = start_time
        self.token_count = 0

    def render(self) -> Table:
        """Render the status bar as a Rich Table."""
        elapsed = int(time.time() - self.start_time)
        mode_color = {"PLAN": "blue", "EXECUTE": "green", "ARCHITECT": "magenta"}

        table = Table.grid(padding=1)
        table.add_column(justify="left")
        table.add_column(justify="center")
        table.add_column(justify="right")

        table.add_row(
            f"[{mode_color.get(self.mode_manager.current_mode.name, 'white')}]● {self.mode_manager.current_mode.name}[/]",
            f"🔤 {self.token_count} tokens",
            f"⏱️  {elapsed}s"
        )
        return table
```

**Update main loop:**
```python
status_bar = StatusBar(mode_manager, time.time())

while True:
    # Display status bar before prompt
    console.print(status_bar.render())
    user_input = session.prompt(...)
```

#### Task 3.3: Rich Mode Transition Panels
**File:** `vibe/cli/repl.py`

**Replace placeholder text in mode transitions:**

**Current (BAD):**
```python
print(f"Switched to {mode.name} mode")
```

**New (GOOD):**
```python
from vibe.cli.plating import MODE_TIPS  # Assume we define this

def show_mode_transition(mode: Mode):
    """Show a full-screen mode transition panel."""
    tips = MODE_TIPS.get(mode.name, "No tips available")

    panel = Panel(
        f"[bold]Welcome to {mode.name} Mode[/bold]\n\n{tips}",
        title=f"🔄 Mode Switch",
        border_style="magenta",
        box=box.HEAVY
    )
    console.print(panel)
```

**Define `MODE_TIPS` in `vibe/cli/plating.py`:**
```python
MODE_TIPS = {
    "PLAN": "📋 Read-only mode. Perfect for exploring ideas.\n• Tools are blocked\n• Use /chef to see options",
    "EXECUTE": "⚡ Full access. Tools will request approval.\n• Be careful with destructive operations\n• Use Ctrl+C to abort",
    "ARCHITECT": "🏗️  Design mode. Sketch the blueprint.\n• Read-only, like PLAN\n• Focus on architecture"
}
```

#### Task 3.4: Spinner Integration for API Calls
**File:** `vibe/core/agent.py`

**Wrap Mistral API calls:**

```python
from rich.spinner import Spinner
from rich.live import Live

def _call_mistral_api(self, prompt):
    """Call Mistral API with a spinner."""
    with Live(Spinner("dots", text="🍳 Thinking..."), transient=True):
        response = self.mistral_client.chat(prompt)
    return response
```

#### Task 3.5: Easter Egg Visual Boost
**File:** `vibe/cli/easter_eggs.py`

**Enhance `/wisdom` command:**

**Current:**
```python
def show_wisdom():
    print(random.choice(CHEF_WISDOM))
```

**New:**
```python
from rich.align import Align
from rich.panel import Panel

def show_wisdom():
    """Display centered Chef wisdom."""
    wisdom = random.choice(CHEF_WISDOM)
    panel = Panel(
        Align.center(f"[italic]{wisdom}[/italic]"),
        title="🧠 Chef's Wisdom",
        border_style="blue",
        box=box.ROUNDED
    )
    console.print(panel)
```

**Add new `/stats` command:**

```python
def show_stats(session_start_time, token_count, tools_executed):
    """Display session statistics."""
    uptime = int(time.time() - session_start_time)

    table = Table(title="📊 Menu du Jour (Session Stats)", box=box.SIMPLE)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Uptime", f"{uptime}s")
    table.add_row("Tokens Used", str(token_count))
    table.add_row("Tools Executed", str(tools_executed))

    console.print(table)
```

**Register in REPL:**
```python
if user_input == "/stats":
    show_stats(start_time, token_count, tools_run)
    continue
```

#### Task 3.6: Improve Error Panels
**File:** `vibe/cli/repl.py`

**Use the `ChefErrorHandler` from Phase 1:**

```python
from vibe.core.error_handler import ChefErrorHandler

try:
    # ... REPL logic
except KeyboardInterrupt:
    console.print("\n[yellow]👋 Bon appétit![/yellow]")
    break
except Exception as e:
    ChefErrorHandler.display_error(e, context="REPL", show_traceback=True)
```

---

### **PHASE 4: Testing & Documentation**
*Doel: Ensure quality, document everything.*

#### Task 4.1: Integration Tests for REPL
**File:** `tests/integration/test_repl_flows.py` (NEW)

**Implementation:**
```python
import pytest
from unittest.mock import patch
from vibe.cli.repl import main

@patch("vibe.cli.repl.session.prompt")
def test_mode_cycling_via_shift_tab(mock_prompt):
    """Test that Shift+Tab cycles modes correctly."""
    # Simulate user pressing Shift+Tab, then /quit
    mock_prompt.side_effect = [KeyBinding("s-tab"), "/quit"]

    # Run REPL
    main()

    # Verify mode changed (would require inspecting ModeManager state)
    # This is a skeleton — full impl requires state inspection

@patch("vibe.cli.repl.session.prompt")
def test_easter_egg_commands(mock_prompt):
    """Test /chef, /wisdom, /roast commands."""
    mock_prompt.side_effect = ["/chef", "/wisdom", "/roast", "/quit"]

    with patch("vibe.cli.easter_eggs.show_wisdom") as mock_wisdom:
        main()
        mock_wisdom.assert_called_once()
```

**Run:**
```bash
pytest tests/integration/ -v
```

#### Task 4.2: Update README.md
**File:** `README.md`

**Add sections:**

```markdown
## 🎨 What Makes ChefChat Special?

### Delightful UX
- 🌈 **Rich Terminal UI**: Spinners, progress bars, formatted panels
- 🎯 **Mode System**: PLAN (read-only), EXECUTE (full access), ARCHITECT (design)
- 🎭 **Easter Eggs**: `/chef`, `/wisdom`, `/roast`, `/stats`
- ⚡ **Real-time Feedback**: Watch tools execute with live status updates

### Technical Excellence
- 🔒 **Safety First**: Gatekeeper prevents destructive ops in PLAN mode
- ⚡ **Async Optimized**: Parallel tool execution where safe
- 📊 **Performance Tracked**: Built-in benchmarking script
- 🧪 **Fully Tested**: Unit + integration tests with pytest

## 🚀 Quick Start

\```bash
# Install
uv pip install -e .

# Run
uv run vibe --repl

# Try it out
> /chef           # See all commands
> Shift+Tab       # Cycle modes
> Your prompt here
\```

## 🍴 Fork Status
ChefChat is a production-ready fork of [mistral-vibe](https://github.com/mistralai/mistral-vibe).
See [docs/UPSTREAM_DIVERGENCE.md](docs/UPSTREAM_DIVERGENCE.md) for technical details.
```

#### Task 4.3: Create Architecture Document
**File:** `docs/ARCHITECTURE.md` (NEW)

**Content:**
```markdown
# ChefChat Architecture

## Component Overview

### Core
- `agent.py`: Orchestrates tool execution, calls ModeManager gatekeeper
- `mode_manager.py`: Safety system (PLAN/EXECUTE/ARCHITECT)
- `config.py`: Single source of truth for configuration
- `error_handler.py`: Centralized error display with Rich

### CLI
- `repl.py`: Main interactive loop (prompt_toolkit + Rich)
- `plating.py`: UI helper functions (panels, tables, formatting)
- `easter_eggs.py`: Fun commands (/chef, /wisdom, /roast, /stats)
- `mode_errors.py`: Handles mode violation messages

### Utils
- `async_helpers.py`: Async utilities (spinners, batch execution)
- `ui_helpers.py`: Reusable UI components

## Data Flow

\```
User Input (REPL)
  ↓
Agent.process()
  ↓
ModeManager.should_block_tool()?
  ↓ NO (EXECUTE) / ↓ YES (PLAN)
Tool Execution   /   Display Error Panel
  ↓                   ↓
Rich Feedback       User sees why
\```

## Safety Guarantees
1. **Gatekeeper Layer**: `Agent._should_execute_tool()` ALWAYS consults ModeManager
2. **Deny-by-default**: Write tools blocked in PLAN/ARCHITECT modes
3. **No bypasses**: No "approved=True" hacks (removed in audit fix)
```

#### Task 4.4: Final Quality Checklist
**File:** `QUALITY_CHECKLIST.md` (NEW)

**Content:**
```markdown
# Pre-Release Quality Checklist

## Code Quality
- [ ] No `TODO` comments in production code
- [ ] All functions have docstrings
- [ ] Type hints on all public functions
- [ ] Passes `ruff check vibe/`
- [ ] Passes `mypy vibe/`

## Testing
- [ ] All unit tests pass: `pytest tests/chef_unit/`
- [ ] All integration tests pass: `pytest tests/integration/`
- [ ] Manual REPL smoke test: `/chef`, mode cycling, tool execution
- [ ] Benchmark script runs: `python scripts/benchmark.py`

## Documentation
- [ ] README.md updated with new features
- [ ] ARCHITECTURE.md complete
- [ ] UPSTREAM_DIVERGENCE.md complete
- [ ] Inline comments added for complex logic

## UX
- [ ] Startup banner displays correctly
- [ ] Footer status bar updates in real-time
- [ ] Mode transitions show helpful tips
- [ ] Errors display with Rich panels (not raw tracebacks)
- [ ] Spinners appear during API calls

## Performance
- [ ] App starts in < 2 seconds
- [ ] Mode switching < 1ms
- [ ] Gatekeeper checks < 1ms
```

---

## 📊 5. File Structure Target

Dit is de eindstructuur na volledige implementatie van het masterplan:

```
ChefChat/
├── .github/
│   └── workflows/
│       ├── tests.yml
│       └── lint.yml
├── .gemini/
├── docs/
│   ├── ARCHITECTURE.md          # 🆕 Component breakdown
│   └── UPSTREAM_DIVERGENCE.md   # 🆕 Fork divergence docs
├── vibe/
│   ├── acp/
│   │   ├── entrypoint.py
│   │   └── tools/
│   ├── cli/
│   │   ├── autocompletion/
│   │   ├── easter_eggs.py       # ✨ Enhanced with /stats
│   │   ├── entrypoint.py        # 🔧 No more --textual
│   │   ├── mode_errors.py
│   │   ├── plating.py           # ✨ MODE_TIPS added
│   │   └── repl.py              # 🎨 FULLY UPGRADED (banner, status bar, spinners)
│   ├── core/
│   │   ├── agent.py             # 🔧 Async helpers, live feedback
│   │   ├── config.py            # 🔧 Singleton get_config()
│   │   ├── error_handler.py     # 🆕 Centralized error handling
│   │   ├── mode_manager.py
│   │   ├── system_prompt.py
│   │   ├── llm/
│   │   ├── prompts/
│   │   └── tools/
│   ├── setup/
│   └── utils/
│       ├── async_helpers.py     # 🆕 Async utilities
│       └── ui_helpers.py        # 🆕 Reusable UI components
├── tests/
│   ├── chef_unit/
│   │   ├── test_modes_and_safety.py
│   │   └── test_plan_mode_safety.py  # 🆕 Audit compliance test
│   └── integration/
│       └── test_repl_flows.py        # 🆕 End-to-end tests
├── scripts/
│   ├── visual_taste_test.py
│   └── benchmark.py                  # 🆕 Performance benchmarks
├── MASTERPLAN.md                     # 📋 Dit document
├── CHEFCHAT_AUDIT_REPORT.md
├── QUALITY_CHECKLIST.md              # 🆕 Pre-release checklist
├── TESTING_MENU.md
├── README.md                          # 🔧 Updated with new features
├── pyproject.toml
└── uv.lock

REMOVED:
❌ vibe/cli/textual_ui/ (entire directory)
```

---

## ✅ Success Metrics

Na voltooiing van dit masterplan moet ChefChat:

1. **Zero crashes** bij normale operaties
2. **< 2s startup time** op moderne hardware
3. **100% test pass rate** (unit + integration)
4. **Visuele "wow-factor"** — nieuwe gebruikers zeggen "This is beautiful"
5. **Veilig** — Geen writes in PLAN mode, zero bypasses
6. **Documented** — Elke nieuwe dev begrijpt de architectuur in < 30 min

---

## 🎬 Final Notes for Opus

Hey Claude,

Je gaat dit plan uitvoeren in fases. Werk **sequentieel** door de tasks heen:
1. Voltooi Phase 1 volledig voordat je naar Phase 2 gaat.
2. Test elke wijziging met `pytest` of handmatige verificatie.
3. Commit na elke logische groep taken (bijv. na Task 1.4, commit "Phase 1 complete").

**Pro tips:**
- Als een file niet bestaat en je moet hem aanmaken: gebruik `write_to_file`.
- Als een file wél bestaat en je moet iets toevoegen: gebruik `replace_file_content` of `multi_replace`.
- Bij twijfel over waar een functie thuishoort: check de "File Structure Target" sectie.

**Belangrijkste regel:**
> Make it work, make it right, make it beautiful — in die volgorde.

Veel succes! 🚀

---

**END OF MASTERPLAN**
