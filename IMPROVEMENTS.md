# Fork Improvements

This document tracks improvements and bug fixes implemented in this fork that address upstream issues.

## Bug Fixes

### ✅ Fixed #213 - Mode Switch Toggle Not Functioning
**Status:** Fixed
**Upstream Issue:** https://github.com/mistralai/mistral-vibe/issues/213
**Problem:** When switching modes via shift+tab, the UI would update but the agent would continue operating in the previous mode. This happened because `self._mode` was updated AFTER the async `reload_with_initial_messages()` call, allowing messages to be processed with the old mode still active.

**Fix:** Moved `self._mode = new_mode` to execute immediately before the reload operation in `vibe/core/agent.py:847`.

**Files Changed:**
- `vibe/core/agent.py`

---

### ✅ Fixed #217 - Session Log Updates After Every Turn
**Status:** Fixed
**Upstream Issue:** https://github.com/mistralai/mistral-vibe/issues/217
**Problem:** Session logs were only saved when the conversation loop completed (finish_reason == "stop"), not after each turn involving tool calls. This made real-time auditing impossible during long agentic tasks.

**Fix:** Added `save_interaction()` call inside the conversation loop in `vibe/core/agent.py:277`, ensuring logs are updated after every turn regardless of whether tool calls are made.

**Files Changed:**
- `vibe/core/agent.py`

---

### ✅ Fixed #186 - Bash Tool Uses sh Instead of bash
**Status:** Fixed
**Upstream Issue:** https://github.com/mistralai/mistral-vibe/issues/186
**Problem:** The bash tool was using Python's default `/bin/sh` instead of `/bin/bash`, breaking bash-specific features like `source` command.

**Fix:** Added `executable="/bin/bash"` parameter to `asyncio.create_subprocess_shell()` call in `vibe/core/tools/builtins/bash.py:247`.

**Files Changed:**
- `vibe/core/tools/builtins/bash.py`

---

## Pending Improvements

### 🔄 #218 - API Key Validation Before Chat Mode
**Status:** Planned
**Upstream Issue:** https://github.com/mistralai/mistral-vibe/issues/218
**Description:** Validate API keys during setup wizard before allowing users to enter chat mode.

---

### 🔄 #191 - Custom Slash Commands Support
**Status:** Planned
**Upstream Issue:** https://github.com/mistralai/mistral-vibe/issues/191
**Description:** Add support for user-defined custom slash commands for better integration and extensibility.

---

### 🔄 #190 - Web Fetch Feature
**Status:** Planned
**Upstream Issue:** https://github.com/mistralai/mistral-vibe/issues/190
**Description:** Implement web_fetch tool similar to Qwen Code's implementation for fetching web page content.

**Note:** This feature already exists in the fork! The `web_fetch` tool is already implemented and available.

---

## Contributing

If you've implemented a fix or improvement, please:
1. Document it in this file
2. Reference the upstream issue number
3. Describe the problem, solution, and files changed
4. Keep the format consistent

## Testing

All bug fixes should include:
- Manual testing to verify the fix
- Regression testing to ensure no breakage
- Documentation updates where applicable
