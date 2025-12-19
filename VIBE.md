# Mistral Vibe - Comprehensive Documentation

## Overview

**Mistral Vibe** is an open-source command-line coding assistant powered by Mistral AI's models. It provides a conversational interface to your codebase, allowing you to use natural language to explore, modify, and interact with your projects through a powerful set of tools.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Features](#features)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Usage Patterns](#usage-patterns)
- [Development](#development)
- [Security](#security)
- [API Reference](#api-reference)

## Installation

### Prerequisites

- Python 3.12+
- uv package manager (recommended)

### Installation Methods

#### Using uv (Recommended)

```bash
uv tool install mistral-vibe
```

#### Using pip

```bash
pip install mistral-vibe
```

#### From Source

```bash
git clone https://github.com/mistralai/mistral-vibe.git
cd mistral-vibe
uv sync
uv run python -m vibe.cli.entrypoint
```

## Quick Start

### Basic Usage

1. Navigate to your project's root directory:
   ```bash
   cd /path/to/your/project
   ```

2. Run Vibe:
   ```bash
   vibe
   ```

3. Start interacting with the agent!
   ```
   > Can you find all instances of the word "TODO" in the project?
   ```

### First-Time Setup

When you run Vibe for the first time, it will:
- Create a default configuration file at `~/.vibe/config.toml`
- Prompt you to enter your Mistral API key
- Save your API key to `~/.vibe/.env` for future use

## Features

### Interactive Chat

- Conversational AI agent that understands natural language requests
- Breaks down complex tasks into manageable steps
- Maintains context across multiple interactions

### Powerful Toolset

Vibe provides a comprehensive set of tools for code manipulation:

#### File Operations
- **`read_file`**: Read files with line number offsets and limits
- **`write_file`**: Create or overwrite files
- **`search_replace`**: Make targeted changes using SEARCH/REPLACE blocks

#### Code Search
- **`grep`**: Recursively search code with regex patterns
- Respects `.gitignore` and `.ignore` files

#### System Commands
- **`bash`**: Execute shell commands in a stateful terminal
- Configurable timeout and permission levels

#### Utility Tools
- **`todo`**: Manage a task list for tracking work
- **`clipboard`**: Copy text to system clipboard

### Project-Aware Context

Vibe automatically:
- Scans your project's file structure
- Detects Git repository status
- Provides relevant context to the agent
- Improves understanding of your codebase

### Advanced CLI Experience

- **Autocompletion**:
  - Slash commands with `/` prefix
  - File paths with `@` prefix
- **Command History**: Persistent across sessions
- **Beautiful Themes**: Modern, readable UI
- **Keyboard Shortcuts**: Efficient navigation

### Configuration System

Highly customizable through `config.toml`:
- Model selection and providers
- Tool permissions
- UI preferences
- API timeouts
- Session logging options

### Safety Features

- **Tool Execution Approval**: Explicit confirmation before running tools
- **Trusted Folders**: Security mechanism for local directories
- **Mode System**: Different operating modes for different scenarios

## Architecture

### Core Components

#### 1. CLI Layer (`vibe/cli/`)

- Entry point and argument parsing
- Textual UI implementation
- Command execution and history management
- Terminal setup and configuration

#### 2. Core Layer (`vibe/core/`)

- **Agent**: Main AI agent implementation
  - Conversation state management
  - Tool execution and approval
  - Middleware pipeline for extensions
  
- **Tools**: Tool management system
  - Tool registration and discovery
  - Permission checking
  - Execution framework
  
- **Configuration**: Configuration loading and validation
  - Model and provider management
  - Path resolution
  - Environment variable handling
  
- **LLM Backend**: Model backend implementations
  - Mistral API integration
  - Fireworks API support
  - Streaming and non-streaming modes
  
- **Types**: Data structures and enums
  - Message formats
  - Event types
  - Agent states

#### 3. ACP Layer (`vibe/acp/`)

- Agent Client Protocol implementation
- Editor/IDE integration
- Protocol message handling
- Terminal authentication

#### 4. Setup Layer (`vibe/setup/`)

- Onboarding workflows
- Trusted folder management
- Configuration initialization

### Data Flow

1. **User Input**: Natural language request
2. **Agent Processing**: 
   - Parse request
   - Select appropriate tools
   - Request approval (if needed)
3. **Tool Execution**:
   - Run selected tools
   - Collect results
4. **Response Generation**:
   - Format response
   - Display to user
5. **Context Update**:
   - Update conversation state
   - Log interaction

### Middleware Pipeline

The agent uses a middleware pipeline for extensibility:

- **TurnLimitMiddleware**: Enforce maximum conversation turns
- **PriceLimitMiddleware**: Enforce cost limits
- **AutoCompactMiddleware**: Automatic conversation summarization
- **ContextWarningMiddleware**: Warn about context length
- **PlanModeMiddleware**: Enforce read-only mode

## Configuration

### Configuration File

Vibe uses `config.toml` for configuration, located at:
- `./.vibe/config.toml` (project-specific)
- `~/.vibe/config.toml` (user-specific)

### Main Configuration Options

```toml
[default]
# Active model to use
active_model = "devstral-2"

# Provider configuration
[providers.mistral]
api_key_env = "MISTRAL_API_KEY"
base_url = "https://api.mistral.ai"

# Tool permissions
tools.bash.permission = "ask"
tools.read_file.permission = "always"

# Session logging
[session_logging]
enabled = true
save_dir = "~/.vibe/sessions"

# UI preferences
[ui]
theme = "dark"
auto_compact_threshold = 0.8
```

### API Key Configuration

Vibe supports multiple ways to configure API keys:

1. **Interactive Setup**: Prompted on first run
2. **Environment Variables**: `MISTRAL_API_KEY`
3. **`.env` File**: `~/.vibe/.env`

### Custom System Prompts

Create custom prompts in `~/.vibe/prompts/`:

```
~/.vibe/prompts/
  └── my_custom_prompt.md
```

Then reference in config:
```toml
system_prompt_id = "my_custom_prompt"
```

### Custom Agent Configurations

Create agent-specific configs in `~/.vibe/agents/`:

```toml
# ~/.vibe/agents/redteam.toml
active_model = "devstral-2"
system_prompt_id = "redteam"

[tools.bash]
permission = "always"
```

Use with: `vibe --agent redteam`

### MCP Server Configuration

Extend Vibe with MCP servers:

```toml
[[mcp_servers]]
name = "my_http_server"
transport = "http"
url = "http://localhost:8000"
headers = { "Authorization" = "Bearer my_token" }

[[mcp_servers]]
name = "fetch_server"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]
```

## Usage Patterns

### Interactive Mode

```bash
vibe
```

- Enter conversational interface
- Type natural language requests
- Use `@` for file path completion
- Use `/` for slash commands

### Programmatic Mode

```bash
vibe --prompt "Refactor the main function"
```

- Non-interactive execution
- Auto-approves all tools
- Outputs response and exits

### Slash Commands

Meta-actions during session:
- `/clear`: Clear conversation history
- `/reset`: Reset to initial state
- `/exit`: Quit Vibe
- `/mode`: Change operating mode

### Operating Modes

1. **Default Mode**: Interactive with approval prompts
2. **Auto-approve Mode**: `vibe --auto-approve`
3. **Plan Mode**: `vibe --plan` (read-only exploration)

### Session Management

```bash
# Continue from last session
vibe --continue

# Resume specific session
vibe --resume <session-id>
```

## Development

### Project Structure

```
vibe/
├── acp/              # ACP protocol implementation
├── cli/              # CLI and UI components
├── core/             # Core agent logic
├── setup/            # Setup workflows
└── __init__.py
```

### Development Setup

```bash
# Clone repository
git clone https://github.com/mistralai/mistral-vibe.git
cd mistral-vibe

# Install dependencies
uv sync

# Run tests
uv run pytest

# Run Vibe from source
uv run python -m vibe.cli.entrypoint
```

### Coding Standards

- Python 3.12+ best practices
- Modern type hints with built-in generics
- PEP 8 compliance
- Use `uv` for all commands
- Prefer `pathlib.Path` over `os.path`

### Key Design Patterns

1. **Middleware Pipeline**: Extensible request/response processing
2. **Tool Manager**: Dynamic tool registration and discovery
3. **Configuration Resolution**: Hierarchical config loading
4. **Event-driven Architecture**: Async event streaming

## Security

### Trusted Folders

Vibe implements a security feature to prevent unauthorized access:

1. When entering a directory with `.vibe/`, user is prompted
2. User must explicitly trust the folder
3. Trusted folders are stored in `~/.vibe/trusted_folders.json`

### Tool Permissions

Three permission levels:
- **`always`**: Execute without approval
- **`ask`**: Request approval before execution
- **`never`**: Block execution

Configure in `config.toml`:
```toml
[tools.bash]
permission = "ask"
```

### Approval System

- Interactive mode: Shows approval dialog
- Programmatic mode: Auto-approves (with `--auto-approve`)
- Can toggle auto-approve with `Shift+Tab` in UI

## API Reference

### Agent API

```python
from vibe.core.agent import Agent
from vibe.core.config import VibeConfig

# Create agent
config = VibeConfig.load()
agent = Agent(config)

# Process message
async for event in agent.act("Find all TODO comments"):
    handle_event(event)
```

### Tool API

```python
from vibe.core.tools.manager import ToolManager

# Get tool manager
manager = ToolManager(config)

# List available tools
for tool in manager.get_available_tools():
    print(tool.name)

# Execute tool
result = await manager.execute_tool(
    tool_name="grep",
    pattern="TODO",
    path="."
)
```

### Configuration API

```python
from vibe.core.config import VibeConfig

# Load configuration
config = VibeConfig.load()

# Get active model
model = config.get_active_model()

# Get provider
provider = config.get_provider_for_model(model)
```

## Advanced Topics

### Custom Tools

Create custom tools by extending `BaseTool`:

```python
from vibe.core.tools.base import BaseTool

class MyCustomTool(BaseTool):
    name = "my_custom_tool"
    description = "A custom tool for my project"
    
    async def run(self, param1: str, param2: int) -> str:
        # Implementation
        return result
```

Register in your config:
```toml
[tools.my_custom_tool]
enabled = true
```

### Extending with MCP

Configure MCP servers to add new capabilities:

```toml
[[mcp_servers]]
name = "github"
transport = "stdio"
command = "mcp-github"
args = ["--repo", "owner/repo"]
```

Tools from MCP servers will be available as `github_*` tools.

### Performance Optimization

- Use `auto_compact_threshold` to enable automatic conversation summarization
- Configure `api_timeout` for long-running operations
- Use `max_turns` and `max_price` to limit resource usage

### Debugging

Enable debug logging:
```toml
[logging]
level = "debug"
```

Check session logs:
```bash
ls ~/.vibe/sessions/
```

## Troubleshooting

### Common Issues

1. **API Key Not Found**: Run `vibe --setup` to configure
2. **Permission Denied**: Check tool permissions in config
3. **Context Too Long**: Use auto-compact or manual `/compact`
4. **Model Unavailable**: Check provider configuration

### Getting Help

- Check the [GitHub Issues](https://github.com/mistralai/mistral-vibe/issues)
- Review the [CHANGELOG](CHANGELOG.md)
- Consult the [CONTRIBUTING](CONTRIBUTING.md) guide

## Future Development

Planned enhancements:
- More MCP server integrations
- Enhanced editor/IDE support
- Improved performance optimizations
- Additional tooling capabilities
- Better multi-session management

---

*Copyright 2025 Mistral AI*
*Licensed under the Apache License, Version 2.0*
