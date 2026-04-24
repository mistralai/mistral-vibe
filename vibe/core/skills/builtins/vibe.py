from __future__ import annotations

from vibe.core.skills.models import SkillInfo

SKILL = SkillInfo(
    name="vibe",
    description="Understand the Vibe CLI application internals: configuration, VIBE_HOME structure, available parameters, agents, skills, tools, and how to inspect or update the user's setup. Use this skill when the user asks about how Vibe works, wants to configure it, or when you need to understand the runtime environment.",
    prompt="""# Vibe CLI Self-Awareness

You are running inside **Mistral Vibe**, a CLI coding agent built by Mistral AI.
This skill gives you full knowledge of the application internals so you can help
the user understand, configure, and troubleshoot their Vibe installation.

## VIBE_HOME

The user's Vibe home directory defaults to `~/.vibe` but can be overridden via
the `VIBE_HOME` environment variable. All user-level configuration, skills, tools,
agents, prompts, logs, and session data live here.

### Directory Structure

```
~/.vibe/
  config.toml          # Main configuration file (TOML format)
  .env                 # API keys and credentials (dotenv format)
  vibehistory          # Command history
  trusted_folders.toml # Trust database for project folders
  agents/              # Custom agent profiles (*.toml)
  prompts/             # Custom system prompts (*.md)
  skills/              # User-level skills (each skill is a subdirectory with SKILL.md)
  tools/               # Custom tool definitions
  logs/
    vibe.log           # Main log file
    session/           # Session log files
  plans/               # Session plans
```

### Project-Local Configuration

When in a trusted folder, Vibe also looks for project-local configuration:
- `.vibe/config.toml` - Project-specific config (overrides user config)
- `.vibe/skills/` - Project-specific skills
- `.vibe/tools/` - Project-specific tools
- `.vibe/agents/` - Project-specific agents
- `.vibe/prompts/` - Project-specific prompts
- `.agents/skills/` - Standard agent skills directory

## Configuration (config.toml)

The configuration file uses TOML format. Settings can also be overridden via
environment variables with the `VIBE_` prefix (e.g., `VIBE_ACTIVE_MODEL=local`).

### Key Settings

```toml
# Model selection
active_model = "devstral-2"      # Model alias to use (see [[models]])

# UI preferences
vim_keybindings = false
disable_welcome_banner_animation = false
autocopy_to_clipboard = true
file_watcher_for_autocomplete = false

# Behavior
auto_approve = false              # Skip tool approval prompts
system_prompt_id = "cli"          # System prompt: "cli", "lean", or custom .md filename
enable_telemetry = true
enable_update_checks = true
enable_auto_update = true
enable_notifications = true
api_timeout = 720.0               # API request timeout in seconds
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
```

### Models

```toml
[[models]]
name = "mistral-vibe-cli-latest"
provider = "mistral"
alias = "devstral-2"
temperature = 0.2
input_price = 0.4
output_price = 2.0
thinking = "off"                  # "off", "low", "medium", "high"
auto_compact_threshold = 200000

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
disabled_tools = ["webfetch"]

# Per-tool configuration
[tools.bash]
allowlist = ["git", "npm", "python"]
```

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
```

### MCP Servers

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
```

### Session Logging

```toml
[session_logging]
enabled = true
save_dir = ""                     # Defaults to ~/.vibe/logs/session
session_prefix = "session"
```

### Pattern Matching

Tool, skill, and agent names support three matching modes:
- **Exact**: `"bash"`, `"read_file"`
- **Glob**: `"bash*"`, `"mcp_*"`
- **Regex**: `"re:^serena_.*$"` (full match, case-insensitive)

## CLI Parameters

```
vibe [PROMPT]                       # Start interactive session with optional prompt
vibe -p TEXT / --prompt TEXT         # Programmatic mode (auto-approve, one-shot, exit)
vibe --agent NAME                   # Select agent profile
vibe --workdir DIR                  # Change working directory
vibe -c / --continue                # Continue most recent session
vibe --resume [SESSION_ID]          # Resume a specific session
vibe -v / --version                 # Show version
vibe --setup                        # Run onboarding/setup
vibe --max-turns N                  # Max assistant turns (programmatic mode)
vibe --max-price DOLLARS            # Max cost limit (programmatic mode)
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
  installed with `/leanstall` (removed with `/unleanstall`)

### Subagents

- **explore**: Read-only codebase exploration subagent (grep + read_file only).
  Spawned by the model, not selectable by the user.

Custom agents are TOML files in `~/.vibe/agents/NAME.toml`.

## Built-in Slash Commands

- `/help` - Show help message
- `/config` - Edit config settings
- `/model` - Select active model
- `/reload` - Reload configuration, agent instructions, and skills from disk
- `/clear` - Clear conversation history
- `/log` - Show path to current interaction log file
- `/debug` - Toggle debug console
- `/compact` - Compact conversation history by summarizing
- `/status` - Display agent statistics
- `/voice` - Configure voice settings
- `/mcp` - Display available MCP servers (pass a server name to list its tools)
- `/resume` (or `/continue`) - Browse and resume past sessions
- `/rewind` - Rewind to a previous message
- `/terminal-setup` - Configure Shift+Enter for newlines
- `/proxy-setup` - Configure proxy and SSL certificate settings
- `/leanstall` - Install the Lean 4 agent (leanstral)
- `/unleanstall` - Uninstall the Lean 4 agent
- `/data-retention` - Show data retention information
- `/teleport` - Teleport session to Vibe Nuage (only available when Nuage is enabled)
- `/exit` - Exit the application

## Skills System

Skills are specialized instruction sets the model can load on demand.
Each skill is a directory containing a `SKILL.md` file with YAML frontmatter.

### Skill File Format

```markdown
---
name: my-skill
description: What this skill does and when to use it.
user-invocable: true
allowed-tools: bash read_file
---

# Skill Instructions

Detailed instructions for the model...
```

### Skill Search Order (first match wins)

1. `skill_paths` from config.toml
2. `.vibe/skills/` in trusted project directory
3. `.agents/skills/` in trusted project directory
4. `~/.vibe/skills/` (user global)

## Environment Variables

- `VIBE_HOME` - Override the Vibe home directory (default: `~/.vibe`)
- `MISTRAL_API_KEY` - API key for Mistral provider
- `VIBE_ACTIVE_MODEL` - Override active model
- `VIBE_*` - Any config field can be overridden with the `VIBE_` prefix

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

## Sensitive Files — DO NOT READ OR EDIT

NEVER read, display, or edit any of these files:
- `~/.vibe/.env` (or `$VIBE_HOME/.env`) — contains API keys and secrets
- Any `.env`, `.env.*` file in the project or VIBE_HOME

If the user asks to set or change an API key, instruct them to edit the `.env`
file themselves. Do not offer to read it, write it, or display its contents.
Do not use tools (read_file, write_file, bash cat/echo, etc.) to access these files.

## How to Modify Configuration

To help the user modify their Vibe configuration:

1. **Read current config**: Read the file at `~/.vibe/config.toml` (or the path
   from `VIBE_HOME` env var if set)
2. **Create a backup**: Before any edit, copy the file to `config.toml.bak` in the
   same directory (e.g. `cp ~/.vibe/config.toml ~/.vibe/config.toml.bak`). This
   applies to any config file you are about to modify (`config.toml`,
   `trusted_folders.toml`, agent TOML files, etc.)
3. **Edit the TOML file**: Make changes using the search_replace or write_file tool
4. **Reload**: The user can run `/reload` to apply changes without restarting

For API keys, tell the user to edit `~/.vibe/.env` directly — never read or
write that file yourself.

For project-specific configuration, create/edit `.vibe/config.toml` in the
project root (the folder must be trusted first).""",
)
