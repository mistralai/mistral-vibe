# 🎯 ChefChat Multi-Mode System Implementation Plan

## Version: 1.0
## Date: 2025-12-10

---

## Executive Summary

This document outlines the implementation plan for adding a 5-mode operational system to ChefChat (Mistral Vibe fork). The system will provide different tool execution permissions and behavioral adjustments per mode, with Shift+Tab cycling support.

---

## 1. Mode Definitions

| Mode | Read-Only | Auto-Approve | Communication Style | Use Case |
|------|-----------|--------------|---------------------|----------|
| **PLAN** 📋 | ✅ Yes | ❌ No | Verbose, analytical | Research & planning before coding |
| **NORMAL** ✋ | ❌ No | ❌ No | Professional, balanced | Standard safe operation |
| **AUTO** ⚡ | ❌ No | ✅ Yes | Efficient, explanatory | Trusted rapid execution |
| **YOLO** 🚀 | ❌ No | ✅ Yes | Ultra-concise | Maximum speed, minimal output |
| **ARCHITECT** 🏛️ | ✅ Yes | ❌ No | Strategic, conceptual | High-level design focus |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        ChefChat CLI                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │  ModeManager    │───▶│  ModeState      │                     │
│  │  (core/modes/)  │    │  - current_mode │                     │
│  │                 │    │  - permissions  │                     │
│  │  - cycle()      │    │  - history      │                     │
│  │  - set_mode()   │    └─────────────────┘                     │
│  │  - get_prompt() │                                            │
│  └────────┬────────┘                                            │
│           │                                                      │
│           ▼                                                      │
│  ┌────────────────────────────────────────────────────┐         │
│  │              Integration Points                     │         │
│  │                                                      │         │
│  │  1. system_prompt.py → inject mode instructions     │         │
│  │  2. agent.py → tool execution permissions           │         │
│  │  3. app.py → Shift+Tab binding, UI updates          │         │
│  │  4. mode_indicator.py → visual feedback             │         │
│  └────────────────────────────────────────────────────┘         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. File Structure

### New Files to Create

```
vibe/core/modes/
├── __init__.py           # Package exports
├── mode_manager.py       # Core ModeManager class
├── mode_types.py         # VibeMode enum and ModeState dataclass
└── mode_prompts.py       # Mode-specific system prompt injections
```

### Existing Files to Modify

| File | Change Type | Description |
|------|-------------|-------------|
| `vibe/cli/textual_ui/app.py` | **Moderate** | Replace boolean auto_approve with ModeManager |
| `vibe/cli/textual_ui/widgets/mode_indicator.py` | **Heavy** | Rewrite to support 5 modes |
| `vibe/core/agent.py` | **Moderate** | Add mode-aware tool execution |
| `vibe/core/system_prompt.py` | **Light** | Add mode injection hook |
| `vibe/core/config.py` | **Light** | Add initial_mode config option |

---

## 4. Implementation Details

### 4.1 New Module: `vibe/core/modes/mode_types.py`

```python
# Core types for the mode system
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

class VibeMode(Enum):
    """The 5 operational modes of ChefChat"""
    PLAN = "plan"           # Read-only, creates detailed plans
    NORMAL = "normal"       # Asks confirmation for each tool
    AUTO = "auto"           # Auto-approves all tools
    YOLO = "yolo"           # Ultra-fast, minimal output, auto-approve
    ARCHITECT = "architect" # High-level design focus, read-only

@dataclass
class ModeState:
    """Tracks the current mode and its properties"""
    current_mode: VibeMode
    auto_approve: bool
    read_only: bool
    started_at: datetime = field(default_factory=datetime.now)
    mode_history: list[tuple[VibeMode, datetime]] = field(default_factory=list)
```

### 4.2 New Module: `vibe/core/modes/mode_manager.py`

```python
class ModeManager:
    """Central manager for mode state and transitions"""

    # Cycle order for Shift+Tab
    CYCLE_ORDER = [
        VibeMode.NORMAL,
        VibeMode.AUTO,
        VibeMode.PLAN,
        VibeMode.YOLO,
        VibeMode.ARCHITECT,
    ]

    # Mode configurations
    MODE_CONFIG = {
        VibeMode.PLAN:      {"auto_approve": False, "read_only": True},
        VibeMode.NORMAL:    {"auto_approve": False, "read_only": False},
        VibeMode.AUTO:      {"auto_approve": True,  "read_only": False},
        VibeMode.YOLO:      {"auto_approve": True,  "read_only": False},
        VibeMode.ARCHITECT: {"auto_approve": False, "read_only": True},
    }

    def cycle_mode(self) -> tuple[VibeMode, VibeMode]:
        """Cycle to next mode, returns (old_mode, new_mode)"""

    def set_mode(self, mode: VibeMode) -> None:
        """Set a specific mode"""

    def should_block_tool(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """Check if tool should be blocked in current mode"""

    def get_system_prompt_injection(self) -> str:
        """Get mode-specific system prompt text"""
```

### 4.3 New Module: `vibe/core/modes/mode_prompts.py`

Contains the large prompt strings for each mode (from your `complete_multimode_prompt.md`):

```python
MODE_PROMPTS = {
    VibeMode.PLAN: """
<active_mode>PLAN MODE</active_mode>
You are in PLAN MODE. This means:
- You MAY ONLY use read-only tools: read_file, grep, bash (for ls/cat/grep only)
- You MUST NOT write, modify, or delete any files
- You MUST create detailed implementation plans before any changes
- You MUST wait for explicit approval before switching to execution
...
""",
    # ... other modes
}
```

### 4.4 Modify: `vibe/cli/textual_ui/widgets/mode_indicator.py`

**Current State**: Simple toggle between auto-approve on/off

**New State**: Full 5-mode display with colors and descriptions

```python
class ModeIndicator(Static):
    """Visual indicator for current operational mode"""

    MODE_DISPLAY = {
        VibeMode.PLAN:      ("📋 PLAN", "mode-plan", "Research & Planning - Read only"),
        VibeMode.NORMAL:    ("✋ NORMAL", "mode-normal", "Ask before each tool"),
        VibeMode.AUTO:      ("⚡ AUTO", "mode-auto", "Auto-approve all tools"),
        VibeMode.YOLO:      ("🚀 YOLO", "mode-yolo", "Maximum speed, minimal output"),
        VibeMode.ARCHITECT: ("🏛️ ARCHITECT", "mode-architect", "High-level design focus"),
    }

    def set_mode(self, mode: VibeMode) -> None:
        """Update display for new mode"""
```

### 4.5 Modify: `vibe/cli/textual_ui/app.py`

**Key Changes:**

1. Replace `self.auto_approve` with `self.mode_manager`
2. Update `action_cycle_mode()` to use ModeManager
3. Add mode change callback to regenerate system prompt

```python
# In __init__:
from vibe.core.modes import ModeManager, VibeMode

self.mode_manager = ModeManager(
    initial_mode=VibeMode.NORMAL if not auto_approve else VibeMode.AUTO
)

# Replace action_cycle_mode:
def action_cycle_mode(self) -> None:
    if self._current_bottom_app != BottomApp.Input:
        return

    old_mode, new_mode = self.mode_manager.cycle_mode()

    # Update mode indicator
    if self._mode_indicator:
        self._mode_indicator.set_mode(new_mode)

    # Update agent
    if self.agent:
        self.agent.auto_approve = self.mode_manager.state.auto_approve
        self.agent.mode_manager = self.mode_manager

        if self.mode_manager.state.auto_approve:
            self.agent.approval_callback = None
        else:
            self.agent.approval_callback = self._approval_callback

    # Visual feedback
    self.notify(
        f"Mode: {new_mode.value.upper()}",
        title="🔄 Mode Changed"
    )
```

### 4.6 Modify: `vibe/core/agent.py`

**Key Changes:**

1. Accept optional `ModeManager` parameter
2. Add mode-aware tool execution in `_should_execute_tool()`

```python
# In __init__:
self.mode_manager: ModeManager | None = None

# Add mode check in _should_execute_tool:
async def _should_execute_tool(
    self, tool: BaseTool, args: dict[str, Any], tool_call_id: str
) -> ToolDecision:

    # Check mode restrictions first
    if self.mode_manager:
        blocked, reason = self.mode_manager.should_block_tool(
            tool.get_name(), args
        )
        if blocked:
            return ToolDecision(
                verdict=ToolExecutionResponse.SKIP,
                feedback=reason
            )

    # Existing approval logic...
    if self.auto_approve:
        return ToolDecision(verdict=ToolExecutionResponse.EXECUTE)
    # ... rest of method
```

### 4.7 Modify: `vibe/core/system_prompt.py`

**Key Changes:**

Add mode injection at the start of the prompt:

```python
def get_universal_system_prompt(
    tool_manager: ToolManager,
    config: VibeConfig,
    mode_manager: ModeManager | None = None
) -> str:
    sections = []

    # Inject mode-specific instructions first
    if mode_manager:
        sections.append(mode_manager.get_system_prompt_injection())

    sections.append(config.system_prompt)
    # ... rest of method
```

---

## 5. Tool Blocking Logic

### Read-Only Tools (Always Allowed)

```python
READONLY_TOOLS = {
    'read_file',
    'grep',
    'list_files',
    'git_status',
    'git_log',
    'git_diff',
    'todo_read',
}
```

### Write Detection for Bash

```python
WRITE_PATTERNS = [
    'rm ', 'mv ', 'touch ', 'mkdir ',
    '>', '>>', 'sed -i',
    'git commit', 'git push', 'git checkout',
    'pip install', 'npm install',
]

def is_write_bash(command: str) -> bool:
    return any(pattern in command for pattern in WRITE_PATTERNS)
```

### Blocking Message Format

```
⛔ Tool '{tool_name}' blocked in {MODE_NAME} mode

This is a {read-only/write} operation.
Current mode only allows read-only tools.

Options:
1. Press Shift+Tab to cycle to NORMAL or AUTO mode
2. Say "approved" to execute this specific operation

Would you like me to add this operation to the plan instead?
```

---

## 6. CSS Updates for Mode Indicator

Add to `vibe/cli/textual_ui/app.tcss`:

```css
/* Mode Indicator Styles */
ModeIndicator {
    padding: 0 1;
    text-style: bold;
}

ModeIndicator.mode-plan {
    background: $primary-darken-2;
    color: $text;
}

ModeIndicator.mode-normal {
    background: $surface;
    color: $text-muted;
}

ModeIndicator.mode-auto {
    background: $success-darken-1;
    color: $text;
}

ModeIndicator.mode-yolo {
    background: $warning;
    color: $background;
}

ModeIndicator.mode-architect {
    background: $primary;
    color: $text;
}
```

---

## 7. Implementation Phases

### Phase 1: Core Mode System (Day 1)
- [ ] Create `vibe/core/modes/` package
- [ ] Implement `mode_types.py`
- [ ] Implement `mode_manager.py`
- [ ] Implement `mode_prompts.py`

### Phase 2: UI Integration (Day 2)
- [ ] Update `mode_indicator.py` widget
- [ ] Update `app.py` with ModeManager
- [ ] Update `action_cycle_mode()`
- [ ] Add CSS styles

### Phase 3: Agent Integration (Day 3)
- [ ] Add mode-aware tool blocking to `agent.py`
- [ ] Update system prompt injection
- [ ] Test all mode transitions

### Phase 4: Polish & Testing (Day 4)
- [ ] Add unit tests
- [ ] Add mode persistence (optional)
- [ ] Documentation
- [ ] Edge case handling

---

## 8. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Breaking existing auto-approve flow | Medium | High | Keep backwards compatibility with `auto_approve` flag |
| System prompt bloat | Low | Medium | Only inject mode section, not entire prompt |
| Mode confusion after cycling | Medium | Low | Clear visual feedback with notify() |
| Tool blocking too aggressive | Medium | Medium | Whitelist approach for read-only tools |

---

## 9. Testing Strategy

### Unit Tests
- ModeManager state transitions
- Tool blocking logic
- Prompt injection

### Integration Tests
- Shift+Tab cycling in TextualUI
- Agent respecting mode permissions
- Mode persistence across reloads

### Manual Tests
- [ ] Cycle through all 5 modes
- [ ] Try write operations in PLAN mode
- [ ] Try write operations in ARCHITECT mode
- [ ] Verify AUTO mode auto-approves
- [ ] Verify YOLO mode minimal output

---

## 10. Success Criteria

1. ✅ User can cycle through 5 modes with Shift+Tab
2. ✅ Each mode displays correct indicator and description
3. ✅ PLAN and ARCHITECT modes block write operations
4. ✅ AUTO and YOLO modes auto-approve all tools
5. ✅ NORMAL mode asks for confirmation
6. ✅ Mode-specific system prompt injected correctly
7. ✅ Visual feedback on mode change

---

## Appendix A: Mode-Specific System Prompts

See `Files_for_opus/complete_multimode_prompt.md` for full prompt text.

## Appendix B: Reference Implementation

See `Files_for_opus/vibe_mode_system.py` for reference Python implementation.

---

**Document Status**: Ready for Implementation
**Estimated Effort**: 3-4 days
**Priority**: High
