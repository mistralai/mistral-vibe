# Hooks

Hooks allow you to run custom shell commands at specific points during Vibe's execution, similar to git hooks. You can use hooks to:

- Block or allow specific tool executions
- Modify tool inputs before execution
- Add context or warnings to the conversation
- Log tool usage
- Validate user prompts
- Perform custom actions at session start/end

## Quick Start

Add hooks to your `~/.vibe/config.toml`:

```toml
[hooks]
enabled = true

# Block dangerous rm commands
[[hooks.hooks.PreToolUse]]
type = "command"
command = "uv run ~/.vibe/hooks/validate_bash.py"
matcher = "bash"
timeout = 10
```

Create the hook script `~/.vibe/hooks/validate_bash.py`:

```python
#!/usr/bin/env -S uv run
import json
import sys

# Read input from stdin
data = json.load(sys.stdin)

# Check the bash command
command = data.get("tool_input", {}).get("command", "")

if "rm -rf" in command:
    # Block the command
    print(json.dumps({
        "permission_decision": "deny",
        "reason": "Dangerous rm command blocked for safety"
    }))
else:
    # Allow the command
    print(json.dumps({
        "permission_decision": "allow"
    }))
```

## Hook Events

Hooks can be triggered at the following events:

| Event | Description | Can Block? |
|-------|-------------|------------|
| `PreToolUse` | Before a tool is executed | Yes |
| `PostToolUse` | After a tool completes | No |
| `UserPromptSubmit` | When user submits a prompt | Yes |
| `SessionStart` | When a session begins | No |
| `SessionEnd` | When a session ends | No |

## Configuration

Hooks are configured in the `[hooks]` section of your `config.toml`:

```toml
[hooks]
enabled = true  # Enable/disable all hooks

# PreToolUse hooks - run before tool execution
[[hooks.hooks.PreToolUse]]
type = "command"
command = "uv run ~/.vibe/hooks/pre_tool.py"
matcher = "*"      # Match all tools (regex pattern)
timeout = 60       # Timeout in seconds (1-600)

# PostToolUse hooks - run after tool execution
[[hooks.hooks.PostToolUse]]
type = "command"
command = "uv run ~/.vibe/hooks/log_tool.py"
matcher = "bash|write_file"  # Match bash or write_file tools

# SessionStart hooks
[[hooks.hooks.SessionStart]]
type = "command"
command = "echo 'Session started'"

# SessionEnd hooks
[[hooks.hooks.SessionEnd]]
type = "command"
command = "uv run ~/.vibe/hooks/session_summary.py"

# UserPromptSubmit hooks
[[hooks.hooks.UserPromptSubmit]]
type = "command"
command = "uv run ~/.vibe/hooks/validate_prompt.py"
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | `"command"` | Hook type (currently only "command") |
| `command` | string | **required** | Shell command to execute |
| `matcher` | string | `"*"` | Regex pattern to match tool names |
| `timeout` | float | `60` | Timeout in seconds (1-600) |

### Matcher Patterns

The `matcher` field uses regex patterns to filter which tools trigger the hook:

- `"*"` - Match all tools
- `"bash"` - Match only the bash tool
- `"bash|write_file"` - Match bash or write_file
- `"mcp_.*"` - Match all MCP tools

## Hook Input/Output

Hooks receive JSON input via stdin and should output JSON to stdout.

### Input Format

All hooks receive a base set of fields:

```json
{
  "session_id": "abc-123-def",
  "cwd": "/path/to/project",
  "hook_event_name": "pre_tool_use"
}
```

#### PreToolUse Input

```json
{
  "session_id": "abc-123-def",
  "cwd": "/path/to/project",
  "hook_event_name": "pre_tool_use",
  "tool_name": "bash",
  "tool_input": {
    "command": "ls -la"
  }
}
```

#### PostToolUse Input

```json
{
  "session_id": "abc-123-def",
  "cwd": "/path/to/project",
  "hook_event_name": "post_tool_use",
  "tool_name": "bash",
  "tool_input": {
    "command": "ls -la"
  },
  "tool_result": "file1.txt\nfile2.txt",
  "tool_error": null
}
```

#### UserPromptSubmit Input

```json
{
  "session_id": "abc-123-def",
  "cwd": "/path/to/project",
  "hook_event_name": "user_prompt_submit",
  "user_prompt": "Help me write a function"
}
```

#### SessionEnd Input

```json
{
  "session_id": "abc-123-def",
  "cwd": "/path/to/project",
  "hook_event_name": "session_end",
  "message_count": 15,
  "total_tokens": 10000
}
```

### Output Format

#### PreToolUse Output

```json
{
  "permission_decision": "allow|deny|ask",
  "updated_input": {"command": "modified command"},
  "reason": "Reason for blocking",
  "system_message": "Message to inject into conversation",
  "continue": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `permission_decision` | string | `"allow"`, `"deny"`, or `"ask"` |
| `updated_input` | object | Modified tool input arguments |
| `reason` | string | Reason shown when blocking |
| `system_message` | string | Message injected into conversation |
| `continue` | bool | Whether to continue (default: true) |

#### PostToolUse Output

```json
{
  "system_message": "Tool completed successfully",
  "continue": true
}
```

#### UserPromptSubmit Output

```json
{
  "modified_prompt": "Enhanced: Help me write a function",
  "system_message": "Context added",
  "continue": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `modified_prompt` | string | Modified user prompt |
| `system_message` | string | Message injected into conversation |
| `continue` | bool | If false, blocks the prompt |

## Exit Codes

- **Exit 0**: Success - stdout is parsed as JSON
- **Exit 2**: Blocking error - the hook signals an error that blocks execution
- **Other**: Non-blocking error - hook failed but execution continues

## Environment Variables

Hooks receive the following environment variables:

| Variable | Description |
|----------|-------------|
| `VIBE_HOOK_EVENT` | The hook event name |
| `VIBE_SESSION_ID` | Current session ID |

## Examples

### Block Dangerous Commands

```python
#!/usr/bin/env -S uv run
"""Block dangerous bash commands."""
import json
import re
import sys

DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"dd\s+if=",
    r"mkfs\.",
    r":(){ :|:& };:",
]

data = json.load(sys.stdin)
command = data.get("tool_input", {}).get("command", "")

for pattern in DANGEROUS_PATTERNS:
    if re.search(pattern, command):
        print(json.dumps({
            "permission_decision": "deny",
            "reason": f"Dangerous command pattern detected: {pattern}"
        }))
        sys.exit(0)

print(json.dumps({"permission_decision": "allow"}))
```

### Log All Tool Usage

```python
#!/usr/bin/env -S uv run
"""Log all tool usage to a file."""
import json
import sys
from datetime import datetime

data = json.load(sys.stdin)

log_entry = {
    "timestamp": datetime.now().isoformat(),
    "tool": data.get("tool_name"),
    "input": data.get("tool_input"),
    "result": data.get("tool_result"),
}

with open("/tmp/vibe_tool_log.jsonl", "a") as f:
    f.write(json.dumps(log_entry) + "\n")

print(json.dumps({}))
```

### Require Confirmation for File Writes

```python
#!/usr/bin/env -S uv run
"""Force user confirmation for file writes."""
import json
import sys

data = json.load(sys.stdin)
tool_name = data.get("tool_name", "")

if tool_name in ["write_file", "search_replace"]:
    print(json.dumps({
        "permission_decision": "ask",
        "system_message": "This operation will modify files. Please review carefully."
    }))
else:
    print(json.dumps({"permission_decision": "allow"}))
```

### Add Context to Prompts

```python
#!/usr/bin/env -S uv run
"""Add project context to user prompts."""
import json
import sys
import os

data = json.load(sys.stdin)
prompt = data.get("user_prompt", "")

# Add current git branch to context
try:
    import subprocess
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"],
        stderr=subprocess.DEVNULL
    ).decode().strip()
    context = f"[Current branch: {branch}] "
except:
    context = ""

print(json.dumps({
    "modified_prompt": context + prompt
}))
```

### Session Statistics

```python
#!/usr/bin/env -S uv run
"""Log session statistics on end."""
import json
import sys
from datetime import datetime

data = json.load(sys.stdin)

stats = {
    "session_id": data.get("session_id"),
    "end_time": datetime.now().isoformat(),
    "message_count": data.get("message_count"),
    "total_tokens": data.get("total_tokens"),
}

with open("/tmp/vibe_sessions.jsonl", "a") as f:
    f.write(json.dumps(stats) + "\n")

print(json.dumps({}))
```

## Best Practices

1. **Always exit 0**: Even on errors, exit 0 and return an empty JSON object `{}`. Use exit 2 only when you explicitly want to block execution.

2. **Handle errors gracefully**: Wrap your hook logic in try/except to prevent crashes:
   ```python
   try:
       # Your hook logic
       result = {"permission_decision": "allow"}
   except Exception as e:
       # Log error but allow operation
       result = {"system_message": f"Hook warning: {e}"}
   print(json.dumps(result))
   ```

3. **Keep hooks fast**: Hooks run synchronously, so slow hooks will delay tool execution. Use the `timeout` field to prevent hung hooks.

4. **Use matchers effectively**: Filter hooks to only run on relevant tools to reduce overhead.

5. **Test your hooks**: Test hooks thoroughly before deploying, as buggy hooks can break your workflow.

## Troubleshooting

### Hook not running

- Check that `hooks.enabled = true` in your config
- Verify the `matcher` pattern matches the tool name
- Check file permissions on the hook script

### Hook timing out

- Increase the `timeout` value in the hook config
- Optimize your hook script for faster execution

### Invalid JSON output

- Ensure your hook outputs valid JSON to stdout
- Check for any debug/print statements that might corrupt the output
- Redirect any debug output to stderr

### Hook blocking unexpectedly

- Check the `permission_decision` value in your hook output
- Verify the exit code is 0 (not 2)
