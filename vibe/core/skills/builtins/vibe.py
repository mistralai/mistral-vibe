from __future__ import annotations

from vibe import __version__
from vibe.core.skills.models import SkillInfo

_PROMPT_TEMPLATE = """# Vibe CLI Self-Awareness

You are running inside **Mistral Vibe**, a CLI coding agent built by Mistral AI.
This skill gives you full knowledge of the application internals so you can help
the user understand, configure, and troubleshoot their Vibe installation.

## Going Deeper

For facts not covered here, fetch the README pinned to the running version:
https://github.com/mistralai/mistral-vibe/blob/v__VIBE_VERSION__/README.md
(do not use `main` — it may not match what is installed). Point the user at
https://docs.mistral.ai/vibe/code/overview for human-readable docs.

## VIBE_HOME

The user's Vibe home directory defaults to `~/.vibe` but can be overridden via
the `VIBE_HOME` environment variable. All user-level configuration, skills, tools,
agents, prompts, logs, and session data live here.

### Directory Structure

```
~/.vibe/
  config.toml          # Main configuration file (TOML format)
  hooks.toml           # User-level hook definitions (experimental)
  .env                 # API keys and credentials (dotenv format)
  vibehistory          # Command history
  trusted_folders.toml # Trust database for project folders
  connector_bootstrap_cache.json # Short-lived connector discovery cache
  agents/              # Custom agent profiles (*.toml)
  prompts/             # Custom prompts (*.md)
  skills/              # User-level skills (each skill is a subdirectory with SKILL.md)
  tools/               # Custom tool definitions
  logs/
    vibe.log           # Main log file
    session/           # Session log files
  plans/               # Session plans

~/.agents/
  skills/              # Additional user-level skills directory
```

### Project-Local Configuration

When in a trusted folder, Vibe also looks for project-local configuration:
- `.vibe/config.toml` - Project-specific config (overrides user config)
- `.vibe/hooks.toml` - Project-specific hooks (requires trusted folder)
- `.vibe/skills/` - Project-specific skills
- `.vibe/tools/` - Project-specific tools
- `.vibe/agents/` - Project-specific agents
- `.vibe/prompts/` - Project-specific prompts
- `.agents/skills/` - Standard agent skills directory

## Lifecycle: Exit, Update, Version, Resume

### Exit

Chat input (case-insensitive): `/exit`, `exit`, `quit`, `:q`, `:quit`.
Keyboard: `Ctrl+C` / `Ctrl+D` — press twice within ~1s to quit. For `Ctrl+C`,
the first press instead interrupts the running job or clears the input if either
is present. Set `ask_confirmation_on_exit = false` to make `Ctrl+D` quit on the
first press (also toggleable in `/config`); `Ctrl+C` always requires a second
press. `Ctrl+Z` suspends on POSIX (resume with `fg`).

### Update

Vibe never updates silently. With `enable_update_checks = true` (default), it
polls PyPI for `mistral-vibe` daily and prompts on the next launch when a
newer release exists; accepting runs `uv tool upgrade mistral-vibe`, then
`brew upgrade mistral-vibe` as a fallback. Disable via `enable_update_checks
= false`. Run `vibe --check-upgrade` to check immediately, prompt to install a newer
version if one exists, and exit. Initial install: `uv tool install mistral-vibe`.

### Version

`vibe --version` (or `-v`) prints it and exits. Not shown anywhere in-session.

### Resume

- `vibe -c` / `--continue`: most recent session in this terminal (TTY-scoped;
  falls back to latest in cwd).
- `vibe --resume [SESSION_ID]`: specific session; without an id, opens a picker.
- In-session: `/resume` (alias `/continue`).

#### Session storage & folder scoping

Local sessions are written under `~/.vibe/logs/session/` (override with
`session_logging.save_dir`). Each session records the `cwd` it ran in. The
`/resume` picker, `--continue`, and bare `--resume` (no id) are **scoped to the
current folder**: only sessions whose `cwd` matches where Vibe is launched are
listed, so the same directory shows its own history and nothing else. Switch
folders to see a different set. The explicit `--resume <SESSION_ID>` form is
**not** folder-scoped: it resolves the session by id regardless of which folder
it ran in.

## Configuration (config.toml)

The configuration file uses TOML format. Settings can also be overridden via
environment variables with the `VIBE_` prefix (e.g., `VIBE_ACTIVE_MODEL=local`).

Custom prompt IDs are resolved from project-local `.vibe/prompts/` first, then
from `~/.vibe/prompts/`, and finally from the built-in bundled prompts.

### Key Settings

```toml
# Model selection
active_model = "mistral-medium-3.5"  # Model alias to use (see [[models]])

# UI preferences
disable_welcome_banner_animation = false
autocopy_to_clipboard = true
file_watcher_for_autocomplete = false
ask_confirmation_on_exit = true  # Require a second Ctrl+D to quit (Ctrl+C always confirms)

# Behavior
bypass_tool_permissions = false    # Skip tool approval prompts
system_prompt_id = "cli"          # System prompt: "cli", "lean", or custom .md filename
compaction_prompt_id = "compact"  # Compaction prompt: built-in "compact" or custom .md filename
enable_telemetry = true
enable_update_checks = true       # Daily PyPI check; prompts on next launch when a newer release exists
enable_notifications = true
enable_system_trust_store = false  # Use OS trust store for outbound HTTPS
api_timeout = 720.0               # API request timeout in seconds
api_retry_max_elapsed_time = 300.0  # Retry budget for retryable API failures in seconds
auto_compact_threshold = 200000   # Token count before auto-compaction

# Git commit behavior
include_commit_signature = true   # Add "Co-Authored-By" to commits

# System prompt composition
include_model_info = true         # Include model name in system prompt
include_project_context = true    # Include project context (git info, cwd) in system prompt
include_prompt_detail = true      # Include OS info, tool prompts, skills, and agents in system prompt

# Voice features
voice_mode_enabled = false
narrator_enabled = false
active_transcribe_model = "voxtral-realtime"
active_tts_model = "voxtral-tts"
```

### Providers

```toml
[[providers]]
name = "mistral"
api_base = "https://api.mistral.ai/v1"
api_key_env_var = "MISTRAL_API_KEY"
backend = "mistral"

[[providers]]
name = "llamacpp"
api_base = "http://127.0.0.1:8080/v1"
api_key_env_var = ""
extra_headers = { "X-Custom-Header" = "value" }  # optional per-provider HTTP headers
```

### Models

```toml
[[models]]
name = "mistral-vibe-cli-latest"
provider = "mistral"
alias = "mistral-medium-3.5"
temperature = 1.0
input_price = 1.5
output_price = 7.5
thinking = "high"                 # "off", "low", "medium", "high", "max"
auto_compact_threshold = 200000
supports_images = true            # vision-capable; allows @-mentioned images

[[models]]
name = "devstral-small-latest"
provider = "mistral"
alias = "devstral-small"
input_price = 0.1
output_price = 0.3

[[models]]
name = "devstral"
provider = "llamacpp"
alias = "local"
```

### Tool Configuration

```toml
# Additional tool search paths
tool_paths = ["/path/to/custom/tools"]

# Enable only specific tools (glob and regex supported)
enabled_tools = ["bash", "read_file", "grep"]

# Disable specific tools
disabled_tools = ["web_fetch"]

# Per-tool configuration
[tools.bash]
allowlist = ["git", "npm", "python"]
```

**Special case — `find` command:** Even if `find` is in the bash allowlist,
Vibe detects `-exec`, `-execdir`, `-ok`, and `-okdir` predicates and will
prompt for user permission before running the command.

#### File Tool Permission Resolution

File-based tools (`read`, `grep`, `write_file`, `edit`) resolve
permissions in this order (first match wins):

1. **Scratchpad** path → always allowed
2. **denylist** glob match → always denied
3. **allowlist** glob match → always allowed
4. **sensitive_patterns** match → requires approval
5. **Outside workdir** → requires approval (or denied if `permission = "never"`)
6. **Default** → uses the tool's `permission` setting

The **denylist** is checked before the allowlist — a path matching both lists
is denied. Both are checked before the outside-workdir boundary, so the
allowlist can still auto-approve access to directories outside the project.

### Skill Configuration

```toml
# Additional skill search paths
skill_paths = ["/path/to/custom/skills"]

# Enable only specific skills
enabled_skills = ["vibe", "custom-*"]

# Disable specific skills
disabled_skills = ["experimental-*"]
```

### Agent Configuration

```toml
# Additional agent search paths
agent_paths = ["/path/to/custom/agents"]

# Enable/disable agents
enabled_agents = ["default", "plan"]
disabled_agents = ["auto-approve"]

# Opt-in builtin agents (only affects agents with install_required=True, e.g. lean)
installed_agents = ["lean"]

# Agent profile to use when --agent is not passed
# (default: "default"). Valid values: "default", "plan", "accept-edits",
# "auto-approve", "lean" (only when listed in installed_agents), or any
# custom agent name from ~/.vibe/agents/ or .vibe/agents/. Subagents
# (e.g. "explore") are rejected. Applies in both interactive and programmatic
# (-p/--prompt) mode.
default_agent = "plan"
```

### MCP Servers

Hosted OAuth MCP servers can be added from inside Vibe:

```text
/mcp add https://mcp.linear.app/mcp
/mcp add https://mcp.example.com/mcp --name docs --scope read --transport http --no-login
```

`/mcp add` is OAuth-only. It writes `auth.type = "oauth"` with optional
scopes and starts login by default. It uses `transport = "streamable-http"`
unless you pass `--transport http`. Pass `--no-login` to add the server without
starting OAuth login. The shortcut supports `streamable-http` and `http`
transports. For API-key/static auth, edit `config.toml` using the static auth
example below.

```toml
[[mcp_servers]]
name = "my-server"
transport = "stdio"
command = "npx"
args = ["-y", "@my/mcp-server"]

[[mcp_servers]]
name = "remote-server"
transport = "http"
url = "https://mcp.example.com"
api_key_env = "MCP_API_KEY"

[[mcp_servers]]
name = "linear"
transport = "streamable-http"
url = "https://mcp.linear.app/mcp"

[mcp_servers.auth]
type = "oauth"
scopes = ["read", "write"]
# Optional: client_id = "pre-registered-public-client"
# Optional: client_metadata_url = "https://example.com/client-metadata.json"
# Optional: redirect_port = 47823
```

HTTP MCP servers can use either static auth or OAuth:

- Static auth: legacy `api_key_env` / `headers` keys still work and are
  promoted to `auth.type = "static"` internally.
- OAuth auth: use `auth.type = "oauth"` with `scopes`. Vibe stores tokens
  in the OS keyring under `mcp-oauth:<alias>:tokens`, dynamic client info
  under `mcp-oauth:<alias>:client_info`, and config drift fingerprints under
  `mcp-oauth:<alias>:fingerprint`.
- Headless environments without an OS keyring cannot store OAuth tokens; use
  static auth via `api_key_env` instead.
- For SSH/remote browser callbacks, forward the loopback port:
  `ssh -L 47823:127.0.0.1:47823 <host>`.

### Connectors

Mistral connectors are auto-discovered when the active provider is Mistral
and the API key env var is set. Toggle the master switch or hide individual
connectors / tools:

```toml
enable_connectors = true          # Master switch (default: true)

[[connectors]]
name = "github"
disabled = true                   # Hide all tools from this connector

[[connectors]]
name = "linear"
disabled_tools = ["delete_issue"] # Hide selected tools only
```

### Session Logging

```toml
[session_logging]
enabled = true
save_dir = ""                     # Defaults to ~/.vibe/logs/session
session_prefix = "session"
```

### Browser Sign-In

Browser sign-in lets users authenticate through the browser during onboarding.
Mistral providers use default browser sign-in URLs. Custom or renamed providers
must configure both URLs:

```toml
[[providers]]
browser_auth_base_url = "https://console.mistral.ai"
browser_auth_api_base_url = "https://console.mistral.ai/api"
```

Self-hosted deployments can point Vibe CLI upgrade and API-key links to their
Le Chat web deployment, where the Vibe API key is managed:

```toml
vibe_base_url = "https://chat.mistral.ai"
```

### Hooks (Experimental)

Hooks let users run shell commands automatically at lifecycle events.
**Experimental**, enabled with `enable_experimental_hooks = true` in
`config.toml` or `VIBE_ENABLE_EXPERIMENTAL_HOOKS=true`.

#### Config and hook types

Hooks live in `hooks.toml` files (separate from `config.toml`), discovered in
this order:

1. `<project>/.vibe/hooks.toml` — loaded first, only when the folder is
   trusted.
2. `~/.vibe/hooks.toml` — loaded second.

A duplicate `name` across the two files is reported as a config issue and the
project entry wins. Config-load errors (invalid TOML, missing required
fields) surface in the TUI as warnings and the offending hook is skipped.

```toml
[[hooks]]
name = "lint"                       # Required: unique within the file.
type = "post_agent_turn"            # Required: post_agent_turn | before_tool | after_tool.
command = "eslint --quiet ."        # Required: shell command run in cwd.
timeout = 60.0                      # Default: 60s for all hooks.
description = "Run ESLint"          # Optional.

[[hooks]]
name = "deny-rm-rf"
type = "before_tool"
match = "bash"                      # Tool-name matcher (tool hooks only, default "*").
strict = true                       # Tool hooks only: escalate any failure to deny/clear.
command = "uv run python /path/to/guard-bash"
```

| Type | When it runs |
|---|---|
| `post_agent_turn` | Once per turn, after the agent finishes responding (no pending tool calls). |
| `before_tool` | Per tool call, before the user permission prompt. |
| `after_tool` | Per tool call, **iff the tool body actually ran**. `tool_status` is `success`, `failure`, or `cancelled`. Does not fire when the tool never executed (`before_tool` denial, user denial at the approval prompt, permission `NEVER`, or cancellation before the body started). |

**Matcher syntax** (same as `enabled_tools`): fnmatch glob by default
(`"bash"`, `"read_*"`, case-insensitive), or a regex full-match when the
pattern starts with `re:` (`"re:(read_file|grep)"`). `match` is forbidden on
`post_agent_turn`.

**Tool name conventions** for matchers:
- Built-in tools use their bare name (`bash`, `read_file`, …); see the Tools
  section above for the full list.
- MCP tools: `{server-name}_{raw-tool-name}` (e.g. `linear_create-issue`).
- Connector tools: `connector_{normalized-name}_{remote-tool-name}` (e.g.
  `connector_Google_Drive_search_files`).
- Subagents all route through `task`. Match with `match = "task"` and read
  `tool_input.agent` to discriminate by subagent.

Subagent invocations inherit the parent's hook config. Their hook events are
logged to the subagent's session log and don't propagate to the parent's UI.

#### Wire protocol

Every hook is spawned in `cwd` and receives a JSON object on **stdin**
discriminated by `hook_event_name`:

```json
// post_agent_turn
{"hook_event_name": "post_agent_turn", "session_id": "...",
 "parent_session_id": null, "transcript_path": "...", "cwd": "..."}

// before_tool
{"hook_event_name": "before_tool", "session_id": "...", "parent_session_id": null,
 "transcript_path": "...", "cwd": "...",
 "tool_name": "bash", "tool_call_id": "call_42",
 "tool_input": {"command": "ls"}}

// after_tool
{"hook_event_name": "after_tool", "session_id": "...", "parent_session_id": null,
 "transcript_path": "...", "cwd": "...",
 "tool_name": "bash", "tool_call_id": "call_42",
 "tool_input": {"command": "ls"},
 "tool_status": "success",         // success | failure | cancelled
 "tool_output": {"stdout": "..."},  // structured result (success/cancelled); null otherwise
 "tool_output_text": "...",         // current text the LLM will see; mutable by prior hooks
 "tool_error": null,                // populated on failure/skipped
 "duration_ms": 42.5}
```

`parent_session_id` is set when running inside a subagent. Exceeding
`timeout` kills the whole process tree.

A hook signals back via its **exit code** and **stdout** (stderr is reserved
for diagnostics — Vibe never parses it for control):

| Exit | Stdout | Behavior |
|---|---|---|
| `0` | empty | Pass through (no action). |
| `0` | valid structured-response JSON object (schema below) | Act per the JSON fields. |
| `0` | anything else (free-form text, broken JSON, scalar/array, schema mismatch) | Failure path (see below). The parse error is in the message. |
| non-zero / timeout / spawn failure | — | Failure path. Reason taken from stderr, then stdout, then the exit code. |

Structured-response schema:

```json
{
  "decision": "allow" | "deny",          // optional; default "allow"
  "reason": "string",                     // required when decision == "deny"
  "system_message": "string",             // optional UI note
  "hook_specific_output": {
    "tool_input": { ... },                // before_tool only
    "additional_context": "string"        // after_tool only
  }
}
```

Unknown fields are tolerated at every level. Fields that aren't meaningful
for the current hook type are silently ignored.

**Don't self-name in `system_message` or `reason`** — the UI prefixes
hook-end-event content with `[hook-name]` automatically, and `before_tool`
denials are wrapped as ``Tool 'X' was denied by hook 'Y': {reason}`` before
the LLM sees them. A hook that writes ``"reason": "guard: refused..."``
will produce ``hook 'guard': guard: refused...`` downstream.

`decision: "deny"` per hook type:

| Hook | Effect of `decision: "deny"` |
|---|---|
| `before_tool` | Deny the tool call; `reason` is the tool error returned to the LLM. First deny short-circuits the remaining `before_tool` hooks for this call. |
| `after_tool` | Replace `tool_output_text` with `reason`. Pipeline continues; subsequent hooks see the replacement. |
| `post_agent_turn` | Inject `reason` as a retry user message. Capped at 3 retries per hook per user turn. |

Event-specific payloads:

- `hook_specific_output.tool_input` (`before_tool`): full replacement of the
  model's arguments. Vibe re-validates against the tool's schema **after each
  rewriting hook** — the first invalid rewrite aborts the chain and
  synthesizes a denial attributing the failure to that hook. Rewrites
  compose: hook N receives `tool_input` as rewritten by hooks 1..N-1.
- `hook_specific_output.additional_context` (`after_tool`): text appended
  (with `\n`) to the current `tool_output_text`. Composes with a same-hook
  `decision: "deny"`: deny replaces first, then `additional_context` is
  appended to the replacement.

**Failure path.** Any failure (non-zero exit, timeout, spawn failure,
non-conforming stdout) emits a UI warning and lets the gated action proceed
(fail open). With `strict = true` on a tool hook:

| Hook | Strict failure escalates to |
|---|---|
| `before_tool` | Deny the tool call with the failure reason. |
| `after_tool` | Clear `tool_output_text` (replace with empty). |

`strict` is forbidden on `post_agent_turn`.

#### Execution semantics

- Hooks of the same type fire sequentially in load order (project file first,
  then user file; declaration order within each file).
- Tool calls within a single LLM turn run **concurrently**; each call's hook
  chain runs serially but the chains run in parallel across calls. Hooks
  that touch shared state (filesystem, env) must coordinate themselves.
- `before_tool` rewrites take effect everywhere downstream: the user
  permission prompt sees the rewritten arguments, the tool runs with them,
  and the assistant message is patched so subsequent LLM turns reflect what
  actually ran.

### Pattern Matching

Tool, skill, and agent names support three matching modes:
- **Exact**: `"bash"`, `"read_file"`
- **Glob**: `"bash*"`, `"mcp_*"`
- **Regex**: `"re:^serena_.*$"` (full match, case-insensitive)

## CLI Parameters

```
vibe [PROMPT]                       # Start interactive session with optional prompt
vibe -p TEXT / --prompt TEXT         # Programmatic mode using `default_agent`, one-shot, exit
vibe -p TEXT --auto-approve          # Programmatic mode with all tool calls approved
vibe -p TEXT --agent lean --yolo      # Lean mode with all tool calls approved
vibe --agent NAME                   # Select agent profile (falls back to `default_agent` config)
vibe --auto-approve / --yolo         # Approve all tool calls for the selected agent
vibe --workdir DIR                  # Change working directory
vibe --worktree NAME                # Create/reuse a git worktree under $VIBE_HOME/worktrees on branch NAME and run inside it. Auto-cleanup only for worktrees Vibe created this run and only after a session started; reused worktrees and attached (pre-existing) branches are kept unless confirmed. -p sessions keep worktrees. Ignored with --setup/--check-upgrade.
vibe --add-dir DIR                  # Extra working dir loaded for context (repeatable). Implicitly trusted.
vibe --trust                        # Trust cwd for this invocation only (not persisted)
vibe -c / --continue                # Continue most recent session in this terminal (TTY-scoped, falls back to latest in cwd)
vibe --resume [SESSION_ID]          # Resume a specific session
vibe -v / --version                 # Show version
vibe --setup                        # Run onboarding/setup
vibe --check-upgrade                # Check for a Vibe update now, prompt to install it, and exit
vibe --max-turns N                  # Max assistant turns (programmatic mode)
vibe --max-price DOLLARS            # Max cost limit (programmatic mode)
vibe --max-tokens N                 # Max total session tokens (programmatic mode)
vibe --enabled-tools TOOL           # Enable specific tools (repeatable)
vibe --output text|json|streaming   # Output format (programmatic mode)
```

## Built-in Agents

There are two kinds of agents:
- **Agents** are user-facing profiles selectable via `--agent` or `Shift+Tab`.
  They configure the model's behavior, tools, and system prompt.
- **Subagents** are model-facing: the model can spawn them autonomously to delegate
  subtasks (e.g. exploring the codebase). Users cannot select subagents directly.

### Agents

- **default**: Standard interactive agent
- **plan**: Planning-focused agent
- **accept-edits**: Auto-approves file edits but asks for other tools
- **auto-approve**: Auto-approves all tool calls
- **lean**: Specialized Lean 4 proof assistant. Not available by default — must be
  installed with `/leanstall` (removed with `/unleanstall`). Use `--agent lean
  --auto-approve` or `--agent lean --yolo` to run Lean mode without tool prompts.

### Subagents

- **explore**: Read-only codebase exploration subagent (grep + read only).
  Spawned by the model, not selectable by the user.

Custom agents are TOML files in `~/.vibe/agents/NAME.toml`.

## Built-in Slash Commands

- `/help` - Show help message
- `/config` - Edit config settings
- `/model` - Select active model
- `/thinking` - Select thinking level
- `/theme` - Select Textual UI theme (persisted in config)
- `/reload` - Reload configuration, agent instructions, and skills from disk
- `/clear` - Clear conversation history
- `/log` - Show path to current interaction log file
- `/debug` - Toggle debug console
- `/compact` - Compact conversation history by summarizing
- `/status` - Display agent statistics
- `/voice` - Configure voice settings
- `/mcp` - Display MCP servers and connector status; pass a server or connector
  name to list its tools or open its auth panel when authentication is required
- `/mcp add <url>` - Add a hosted OAuth MCP server. Supports `--name <alias>`,
  repeatable `--scope <scope>`, `--transport <http|streamable-http>`, and
  `--no-login`. Starts OAuth login by default. OAuth-only; use `config.toml`
  for API-key/static auth.
- `/mcp status` - Display MCP auth state (`ok`, `needs_auth`, `static`, `stdio`)
- `/mcp login <alias>` - Start OAuth login for an MCP server
- `/mcp logout <alias>` - Log out from an MCP server and delete stored OAuth
  secrets
- `/resume` (or `/continue`) - Browse and resume past sessions for the current
  folder. The picker header shows the folder being listed. Press `d` twice to
  delete a saved session; the active session cannot be deleted here.
- `/rewind` - Rewind to a previous message
- `/loop <interval> <prompt>` - Schedule a recurring prompt (e.g. `/loop 30s ping`).
  Intervals: `Ns/Nm/Nh/Nd`, minimum 30s, max 50 loops/session.
  - `/loop` (or `/loop list` / `/loop ls`) - List current scheduled loops.
  - `/loop cancel <id|all>` (aliases `rm`, `stop`, `delete`) - Cancel a loop.
  - Loops fire only when the agent is idle and the input bar is focused. At
    most one loop fires per poll. Overdue loops fire once on the next poll
    (no catch-up); `next_fire_at` advances to `now + interval`.
  - Loops are persisted in the session metadata (`loops` field of `meta.json`)
    and restored on `--resume`/`--continue`.
- `/terminal-setup` - Configure Shift+Enter for newlines
- `/proxy-setup` - Configure proxy and SSL certificate settings
- `/leanstall` - Install the Lean 4 agent (leanstral)
- `/unleanstall` - Uninstall the Lean 4 agent
- `/data-retention` - Show data retention information
- `/teleport` - Teleport session to Vibe Code Web (only available when Vibe Code is enabled)
- `/exit` - Exit the application

## File Mentions (`@`)

Type `@` in the chat input to autocomplete files and folders from the
project tree. Pressing Tab/Enter inserts the chosen path. Behavior
depends on the file kind:

- **Text files** are read at submit and their contents are inlined into the
  prompt text (up to ~256KB).
- **Folders** are inserted as a resource link header (name + uri).
- **Image files** (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`) become image
  attachments — sent alongside the prompt as native multimodal content for
  vision-capable models.

Image attachments:

- Require `supports_images = true` on the active model in `config.toml`.
  By default this is enabled only on `mistral-vibe-cli-latest`. Sending
  images to a non-vision model raises a clear error and the message is
  not added to the conversation.
- Snapshotted into `<session_dir>/attachments/<sha1>.<ext>` so that
  resumed sessions stay reproducible even if the source file is moved.
- Capped at 10 MiB per image and 8 images per message.
- Out-of-project paths work via `@/abs/path/to.png` (the picker only
  suggests project files, but the `@`-parser accepts absolute paths).
  Drag-and-drop from Finder into Terminal, iTerm2, or Ghostty is
  intercepted at paste time: if the pasted content is a single bare
  path to an image file (raw, `\\ `-escaped, or quoted), the input
  automatically prepends `@` (and quotes paths containing spaces).
  Non-image paths are pasted verbatim so non-image use cases are not
  affected.
- **Image copy/paste from the clipboard** (**macOS only** for now):
  writes the image to `<session_dir>/attachments/clipboard-<ts>.png`
  (or the system temp dir when no session is active) and inserts an
  `@<path>` token at the cursor. Two entry points:
  1. `Ctrl+V` keybinding inside the prompt.
  2. `/paste-image` slash command.

  Uses `osascript` with a TIFF→PNG fallback via `sips`. On Linux and
  Windows the binding and the slash command are not registered at all,
  so the feature is invisible to users on those platforms.
- Rendered in the chat bubble as one dim `attached image:` footer line
  per image, linking each attachment to its snapshot. Clicking opens the
  file with the OS default image viewer.

## Input Queue

Messages submitted while the agent or a `!`-bash command is running are
queued instead of cancelling the in-flight work, and drain in FIFO order
once the job finishes. Prompts (plain, `/skill ...`, `@`-mentions) and
`!bash` commands can be queued; slash commands and `&teleport` are
rejected with a toast. **Ctrl+C** pops the last queued item (LIFO);
**Esc** interrupts the running job and pauses the queue; pressing Enter
(empty or not) on a paused queue resumes draining.

## Skills System

Skills are specialized instruction sets the model can load on demand.
Each skill is a directory containing a `SKILL.md` file with YAML frontmatter.

### Skill File Format

```markdown
---
name: my-skill
description: What this skill does and when to use it.
user-invocable: true
allowed-tools: bash read
---

# Skill Instructions

Detailed instructions for the model...
```

### Skill Search Order (first match wins)

1. `skill_paths` from config.toml
2. `.vibe/skills/` in trusted project directory
3. `.agents/skills/` in trusted project directory
4. `~/.vibe/skills/` (user global)
5. `~/.agents/skills/` (user global, Agent Skills standard)

## Environment Variables

- `VIBE_HOME` - Override the Vibe home directory (default: `~/.vibe`)
- `MISTRAL_API_KEY` - API key for Mistral provider
- `VIBE_ACTIVE_MODEL` - Override active model
- `VIBE_*` - Any config field can be overridden with the `VIBE_` prefix
- `LOG_LEVEL` - Logging level for `$VIBE_HOME/logs/vibe.log`. One of `DEBUG`,
  `INFO`, `WARNING` (default), `ERROR`, `CRITICAL`. Invalid values fall back
  to `WARNING`.
- `LOG_MAX_BYTES` - Max size in bytes of `vibe.log` before rotation
  (default: `10485760`, i.e. 10 MiB).
- `DEBUG_MODE` - When `true`, forces `DEBUG`-level logging. Under `vibe-acp`
  it also attaches `debugpy` on `localhost:5678`.
- `VIBE_TYPING_GRACE_PERIOD_MS` - Milliseconds the agent waits for a typing
  pause before showing tool-approval / ask-user-question dialogs (default:
  `1000`). Set to `0` to disable. Negative or non-numeric values fall back
  to the default.

## API Keys (.env file)

The `.env` file in VIBE_HOME stores API keys in dotenv format:

```
MISTRAL_API_KEY=your-key-here
```

This file is loaded on startup and its values are injected into the environment.

## Trusted Folders

Vibe uses a trust system to prevent executing project-local config from untrusted
directories. The trust database is stored in `~/.vibe/trusted_folders.toml`.
Project-local config (`.vibe/` directory) is only loaded when the current
directory is explicitly trusted.

Interactive mode prompts to trust unknown folders. The prompt targets the
closest ancestor of the cwd (the cwd itself included) containing a `.git`
entry; the search excludes the user's home directory and the filesystem
root, and falls back to the cwd if no qualifying ancestor is found.
Programmatic mode (`-p`/`--prompt`) never prompts: the folder is untrusted.
Use `--trust` to trust cwd for the current invocation only (not persisted).

## Sensitive Files — DO NOT READ OR EDIT

NEVER read, display, or edit any of these files:
- `~/.vibe/.env` (or `$VIBE_HOME/.env`) — contains API keys and secrets
- Any `.env`, `.env.*` file in the project or VIBE_HOME

If the user asks to set or change an API key, instruct them to edit the `.env`
file themselves. Do not offer to read it, write it, or display its contents.
Do not use tools (read, write_file, bash cat/echo, etc.) to access these files.

## How to Modify Configuration

To help the user modify their Vibe configuration:

1. **Read current config**: Read the file at `~/.vibe/config.toml` (or the path
   from `VIBE_HOME` env var if set)
2. **Create a backup**: Before any edit, copy the file to `config.toml.bak` in the
   same directory (e.g. `cp ~/.vibe/config.toml ~/.vibe/config.toml.bak`). This
   applies to any config file you are about to modify (`config.toml`,
   `trusted_folders.toml`, agent TOML files, etc.)
3. **Edit the TOML file**: Make changes using the edit tool
4. **Reload**: The user can run `/reload` to apply changes without restarting

For API keys, tell the user to edit `~/.vibe/.env` directly — never read or
write that file yourself.

For project-specific configuration, create/edit `.vibe/config.toml` in the
project root (the folder must be trusted first)."""


SKILL = SkillInfo(
    name="vibe",
    description="""Authoritative reference for Mistral Vibe — the CLI agent you (the model) are running inside.

LOAD when the user:
- asks anything about Vibe itself, even by indirect name ("this CLI", "this tool", "you");
- wants to change, inspect, or reset their setup;
- asks why the agent did or did not act;
- asks how to make the CLI do X, where X lives, or what a flag/command/setting does;
- asks any meta question about your own behavior;
- is unsure whether a command, flag, env var, or file is in scope — this skill is the source of truth.

SCOPE: config under `~/.vibe/` and project-local `.vibe/`; `VIBE_*` and `LOG_*` env vars; models and providers; agents and subagents; skills; tools and their permission model; every slash command and CLI flag; hooks; MCP servers; connectors; trusted folders; `@`-file mentions; logs; themes; voice.""",
    user_invocable=False,
    prompt=_PROMPT_TEMPLATE.replace("__VIBE_VERSION__", __version__),
)
