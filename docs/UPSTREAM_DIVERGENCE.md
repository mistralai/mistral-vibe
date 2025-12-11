# Upstream Divergence — ChefChat vs. mistral-vibe

This document tracks how ChefChat diverges from the original [mistral-vibe](https://github.com/mistralai/mistral-vibe) repository.

## Why We Forked

ChefChat introduces significant enhancements that require deep modifications:

1. **ModeManager Safety System**: A complete gatekeeper layer that blocks destructive operations in read-only modes (PLAN, ARCHITECT).
2. **Premium REPL Interface**: Replaced Textual UI with a `prompt_toolkit` + `rich` based REPL.
3. **Easter Eggs & UX Polish**: Added `/chef`, `/wisdom`, `/roast`, `/stats` commands and enhanced visual feedback.

These changes make merging upstream non-trivial but worthwhile.

---

## Modified Files

| File | Reason | Merge Strategy |
|------|--------|----------------|
| `vibe/core/agent.py` | Gatekeeper logic for tool blocking via `ModeManager` | Manual diff — preserve our `_should_execute_tool` checks |
| `vibe/core/system_prompt.py` | Mode-specific prompts | Cherry-pick upstream improvements, keep mode logic |
| `vibe/cli/repl.py` | Complete rewrite for Rich/prompt_toolkit | No merge needed — this is our custom code |
| `vibe/cli/entrypoint.py` | Removed textual_ui, REPL is default | Manual merge for any new CLI args |
| `vibe/cli/mode_manager.py` | Our custom addition | No upstream equivalent |
| `vibe/cli/mode_errors.py` | Our custom addition | No upstream equivalent |
| `vibe/cli/easter_eggs.py` | Our custom addition | No upstream equivalent |
| `vibe/cli/ui_components.py` | Our custom addition | No upstream equivalent |
| `vibe/cli/plating.py` | Our custom addition | No upstream equivalent |

---

## Removed Files

| File/Directory | Reason |
|----------------|--------|
| `vibe/cli/textual_ui/` | Deprecated — replaced by `repl.py` |

---

## New Files (ChefChat Additions)

| File | Purpose |
|------|---------|
| `vibe/cli/mode_manager.py` | Mode state machine (NORMAL, AUTO, PLAN, YOLO, ARCHITECT) |
| `vibe/cli/mode_errors.py` | Mode violation error handling |
| `vibe/cli/easter_eggs.py` | Fun commands (/chef, /wisdom, /roast) |
| `vibe/cli/ui_components.py` | Rich UI components (HeaderDisplay, StatusBar, etc.) |
| `vibe/cli/plating.py` | Work presentation formatting |
| `vibe/core/error_handler.py` | Centralized error handling |
| `vibe/utils/async_helpers.py` | Async utilities |
| `tests/chef_unit/test_modes_and_safety.py` | Mode system tests |

---

## Merge Checklist

Before pulling from upstream:

### 1. Pre-Merge
- [ ] Stash or commit all local changes
- [ ] Run all tests: `pytest tests/chef_unit/`
- [ ] Note current test count

### 2. Fetch & Review
```bash
git fetch upstream
git diff HEAD..upstream/main -- vibe/core/agent.py
git diff HEAD..upstream/main -- vibe/core/system_prompt.py
```

### 3. High-Risk Files
Pay special attention to:
- **`agent.py`**: Ensure `_should_execute_tool()` gatekeeper logic is preserved
- **`system_prompt.py`**: Keep mode-specific prompt injection
- **`config.py`**: Check for new config options to expose

### 4. Post-Merge Verification
- [ ] All tests pass: `pytest tests/chef_unit/`
- [ ] Test PLAN mode: Verify write operations are blocked
- [ ] Test REPL: Ensure visual rendering is intact
- [ ] Test mode cycling: Shift+Tab works correctly
- [ ] Run `/chef` command: Should display kitchen status

---

## Safety Guarantees We MUST Preserve

1. **Gatekeeper Layer**: `Agent._should_execute_tool()` ALWAYS consults ModeManager
2. **Deny-by-default**: Write tools blocked in PLAN/ARCHITECT modes
3. **No bypasses**: No "approved=True" hacks (intentionally removed)

---

## Contact

For merge assistance or questions about ChefChat internals, see:
- `MASTERPLAN.md` — Complete architecture vision
- `docs/ARCHITECTURE.md` — Component breakdown
- `tests/chef_unit/test_modes_and_safety.py` — Test examples
