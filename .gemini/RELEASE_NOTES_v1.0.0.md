# ChefChat v1.0.0 - Multi-Mode Release 🍳

**Release Date:** 2024-12-10

## 🎉 What's New

This release introduces the **ChefChat Multi-Mode System** - a powerful 5-mode operational system that transforms how you interact with your AI coding assistant. Inspired by Plan Mode patterns, this system provides granular control over AI behavior and tool execution.

---

## ✨ Features

### 🔄 Multi-Mode System

Five distinct operational modes, each with its own behavior profile:

| Mode | Emoji | Description |
|------|-------|-------------|
| **PLAN** | 📋 | Read-only planning mode - research and plan before executing |
| **NORMAL** | ✋ | Standard interactive mode - confirms each tool call |
| **AUTO** | ⚡ | Auto-approve all tools - trusted execution |
| **YOLO** | 🚀 | Maximum speed mode - minimal output, auto-approve |
| **ARCHITECT** | 🏛️ | High-level design mode - read-only for architecture |

**Quick Mode Cycling:** Press `Shift+Tab` to cycle through modes:
```
NORMAL → AUTO → PLAN → YOLO → ARCHITECT → NORMAL
```

### 🛡️ Smart Tool Blocking

In read-only modes (PLAN, ARCHITECT), write operations are automatically blocked:
- ✅ Safe: `read_file`, `grep`, `git status`, `ls`, `cat`
- ❌ Blocked: `write_file`, `rm`, `git push`, `touch`

Smart bash command analysis detects write patterns even in complex commands:
- `echo hi > file` → Blocked (redirect detected)
- `git status` → Safe (read-only git command)
- `git push` → Blocked (write git command)

### 📝 Mode-Aware System Prompts

Each mode injects specific instructions into the LLM's context, ensuring the model understands and respects the current operational mode.

### 🍳 ChefChat Easter Eggs

Fun chef-themed commands for a delightful experience:

| Command | What it does |
|---------|--------------|
| `/chef` | 🍳 Kitchen status with mode info |
| `/wisdom` | 💡 Random cooking/coding wisdom |
| `/roast` | 🔥 Get roasted by Chef Ramsay |
| `/modes` | 🔄 Display all modes beautifully |
| `/plate` | 🍽️ Present your work like a finished dish |
| `/taste` | 👅 Fun code review (taste test) |
| `/timer` | ⏱️ Task time estimates |

### 🧪 Comprehensive Test Suite

85 tests covering:
- Mode cycling and transitions
- Tool permission logic
- Write operation detection
- Git command classification
- System prompt injection
- ModeAwareToolExecutor wrapper
- Integration with Agent and System Prompt

---

## 📁 New Files

```
vibe/cli/
├── mode_manager.py      # Core mode system (~970 lines)
├── mode_errors.py       # Error handling (~690 lines)
├── easter_eggs.py       # Chef wisdom & roasts (~270 lines)
├── plating.py           # Plating feature (~455 lines)
└── commands.py          # Updated with 7 new commands

tests/
└── test_mode_system.py  # 85 comprehensive tests

.gemini/
├── INTEGRATION_GUIDE.md # Integration documentation
└── implementation_plan_multimode.md
```

## 📝 Modified Files

- `vibe/cli/textual_ui/app.py` - ModeManager integration
- `vibe/cli/textual_ui/widgets/mode_indicator.py` - 5-mode display
- `vibe/cli/textual_ui/app.tcss` - Mode-specific CSS styles
- `vibe/core/agent.py` - Mode-aware tool blocking
- `vibe/core/system_prompt.py` - Mode prompt injection
- `README.md` - ChefChat documentation

---

## 🚀 Quick Start

```bash
# Run ChefChat
uv run vibe

# Press Shift+Tab to cycle modes
# Try /chef for kitchen status
# Try /roast for motivation 🔥
```

---

## 🐛 Known Issues

- One lint warning remains: `Too many return statements` in `_is_write_bash_command` (acceptable for the complexity)

---

## 🙏 Credits

Built on top of [Mistral Vibe](https://github.com/mistralai/mistral-vibe).

Chef-themed content inspired by Gordon Ramsay's legendary cooking style. 👨‍🍳

---

**Bon appétit, coders!** 🍳✨
