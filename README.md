# Mistral Vibe

[![PyPI Version](https://img.shields.io/pypi/v/mistral-vibe)](https://pypi.org/project/mistral-vibe)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/release/python-3120/)
[![CI Status](https://github.com/mistralai/mistral-vibe/actions/workflows/ci.yml/badge.svg)](https://github.com/mistralai/mistral-vibe/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/mistralai/mistral-vibe)](https://github.com/mistralai/mistral-vibe/blob/main/LICENSE)

```


      ██    ██
      ██    ██

    ██  ██  ██
██      ██      ██


```

**Mistral's open-source CLI coding assistant with multi-provider support.**

Mistral Vibe is a command-line coding assistant that works with Mistral's models, local LLMs (Ollama, llama.cpp, vLLM, LM Studio, LocalAI), and cloud providers (OpenAI, OpenRouter, Together AI, Groq). It provides a conversational interface to your codebase, allowing you to use natural language to explore, modify, and interact with your projects through a powerful set of tools.

> [!WARNING]
> Mistral Vibe works on Windows, but we officially support and target UNIX environments.

## Installation

### One-line install (recommended)

**Linux and macOS**

```bash
curl -LsSf https://mistral.ai/vibe/install.sh | bash
```

**Windows**

First, install uv
```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then, use uv command below.

### Using uv

```bash
uv tool install mistral-vibe
```

### Using pip

```bash
pip install mistral-vibe
```

### Development Installation

```bash
git clone https://github.com/mixelpixx/mistral-vibe.git
cd mistral-vibe
uv sync
uv run vibe --help
```

## Features

- **Multi-Provider Support**: Use Mistral AI, local LLMs, or third-party cloud providers with a unified interface.
- **Interactive Chat**: A conversational AI agent that understands your requests and breaks down complex tasks.
- **Powerful Toolset**: A suite of tools for file manipulation, code searching, version control, and command execution.
- **Full MCP Protocol**: Complete Model Context Protocol support including tools, resources, and prompts.
- **Project-Aware Context**: Automatic scanning of project structure and Git status for relevant context.
- **Advanced CLI Experience**: Autocompletion, persistent history, beautiful themes.
- **Highly Configurable**: Customize models, providers, tool permissions, and UI preferences.
- **Safety First**: Tool execution approval and permission controls.

## Quick Start

1. Navigate to your project directory:

   ```bash
   cd /path/to/your/project
   ```

2. Run the setup wizard:

   ```bash
   vibe --setup
   ```

   The wizard will guide you through:
   - Selecting a theme
   - Choosing your LLM provider (local or cloud)
   - Configuring your API key (if needed)

3. Start using Vibe:

   ```bash
   vibe
   ```

4. Or use a specific provider directly:

   ```bash
   vibe --provider ollama --model devstral
   ```

## Provider Support

Vibe supports 10 built-in provider presets across local and cloud deployments.

### List Available Providers

```bash
vibe --list-providers
```

### Local Providers

| Provider | Default Port | Command |
|----------|--------------|---------|
| Ollama | 11434 | `vibe --provider ollama` |
| llama.cpp | 8080 | `vibe --provider llamacpp` |
| vLLM | 8000 | `vibe --provider vllm` |
| LocalAI | 8080 | `vibe --provider localai` |
| LM Studio | 1234 | `vibe --provider lmstudio` |

Local providers do not require API keys. Ensure your local server is running before starting Vibe.

**Example: Using Ollama**

```bash
# Start Ollama (in another terminal)
ollama serve

# Pull a model
ollama pull devstral

# Run Vibe with Ollama
vibe --provider ollama --model devstral
```

**Example: Using llama.cpp**

```bash
# Start llama.cpp server
llama-server -m model.gguf --port 8080

# Run Vibe
vibe --provider llamacpp --model devstral
```

### Cloud Providers

| Provider | API Base | Environment Variable |
|----------|----------|---------------------|
| Mistral AI | api.mistral.ai | MISTRAL_API_KEY |
| OpenAI | api.openai.com | OPENAI_API_KEY |
| OpenRouter | openrouter.ai | OPENROUTER_API_KEY |
| Together AI | api.together.xyz | TOGETHER_API_KEY |
| Groq | api.groq.com | GROQ_API_KEY |

**Example: Using OpenAI**

```bash
export OPENAI_API_KEY="sk-..."
vibe --provider openai --model gpt-4o
```

**Example: Using OpenRouter**

```bash
export OPENROUTER_API_KEY="sk-or-..."
vibe --provider openrouter --model anthropic/claude-3.5-sonnet
```

### Custom API Endpoints

Override the default API base URL for any provider:

```bash
vibe --provider ollama --api-base http://192.168.1.100:11434/v1
```

### Disable Streaming

Some providers or configurations may not support streaming responses:

```bash
vibe --provider vllm --no-stream
```

## Command-Line Options

| Option | Description |
|--------|-------------|
| `--setup` | Run the onboarding wizard |
| `--provider NAME` | Use a specific provider preset |
| `--model NAME` | Use a specific model |
| `--api-base URL` | Override the API base URL |
| `--no-stream` | Disable streaming responses |
| `--list-providers` | List all available provider presets |
| `--agent NAME` | Load agent configuration from ~/.vibe/agents/NAME.toml |
| `--auto-approve` | Automatically approve all tool executions |
| `--continue` | Continue from the most recent session |
| `--resume ID` | Resume a specific session by ID |

## Built-in Tools

Vibe includes a comprehensive set of tools for coding tasks:

### File Operations

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents with optional line range |
| `write_file` | Write content to a file |
| `search_replace` | Make targeted edits to files |
| `glob` | Find files matching glob patterns (e.g., `**/*.py`) |
| `list_directory` | List directory contents with metadata |

### Code Search

| Tool | Description |
|------|-------------|
| `grep` | Recursive regex search with ripgrep support |

### System

| Tool | Description |
|------|-------------|
| `bash` | Execute shell commands in a stateful terminal |
| `todo` | Track task progress |

### Web

| Tool | Description |
|------|-------------|
| `web_fetch` | Fetch and extract content from URLs |

### MCP Integration

| Tool | Description |
|------|-------------|
| `mcp_read_resource` | Read resources from MCP servers |
| `mcp_get_prompt` | Get prompt templates from MCP servers |

## Slash Commands

Use slash commands during a session for meta-actions:

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear conversation history |
| `/cost` | Show token usage and cost for the session |
| `/mcp` | Show MCP servers, tools, resources, and prompts |
| `/model` | Change the active model |
| `/mode` | Toggle auto-approve mode |
| `/compact` | Summarize and compact conversation |
| `/quit` | Exit the session |

## Configuration

Vibe uses a `config.toml` file located at `~/.vibe/config.toml` (or `./.vibe/config.toml` for project-specific config).

### Provider Configuration

Configure custom providers in your config file:

```toml
# Define a custom provider
[[providers]]
name = "my-local-server"
api_base = "http://localhost:5000/v1"
api_key_env_var = ""  # Empty for no auth
api_style = "openai"
backend = "generic"

# Define models for the provider
[[models]]
name = "my-model"
provider = "my-local-server"
alias = "local-custom"
temperature = 0.2

# Set the active model
active_model = "local-custom"
```

### API Key Configuration

API keys can be configured in multiple ways (in order of precedence):

1. **Environment Variables**:
   ```bash
   export MISTRAL_API_KEY="your_key"
   ```

2. **`.env` File** at `~/.vibe/.env`:
   ```bash
   MISTRAL_API_KEY=your_key
   OPENAI_API_KEY=your_key
   ```

3. **Interactive Setup**: Run `vibe --setup` to configure through the wizard.

### MCP Server Configuration

Configure MCP (Model Context Protocol) servers for extended capabilities:

```toml
# HTTP MCP server
[[mcp_servers]]
name = "my_http_server"
transport = "http"
url = "http://localhost:8000"
headers = { "Authorization" = "Bearer my_token" }

# Stdio MCP server (local process)
[[mcp_servers]]
name = "fetch_server"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]
```

**MCP Features Supported:**
- Tools: Remote tool execution
- Resources: Read data from MCP servers (files, databases, APIs)
- Resource Templates: Dynamic resource URIs
- Prompts: Reusable prompt templates with arguments

View configured MCP servers and their capabilities with the `/mcp` command.

### Tool Permissions

Control tool execution permissions:

```toml
[tools.bash]
permission = "ask"  # "always", "ask", or "never"

[tools.write_file]
permission = "ask"

[tools.read_file]
permission = "always"
```

### Enable/Disable Tools

Control which tools are available:

```toml
# Only enable specific tools (glob patterns supported)
enabled_tools = ["read_file", "grep", "bash"]

# Or disable specific tools
disabled_tools = ["web_fetch", "mcp_*"]

# Regex patterns (prefix with re:)
enabled_tools = ["re:^(?!mcp_).*$"]  # Exclude all MCP tools
```

### Custom System Prompts

Create custom prompts in `~/.vibe/prompts/`:

```toml
system_prompt_id = "my_custom_prompt"  # Loads ~/.vibe/prompts/my_custom_prompt.md
```

### Custom Agent Configurations

Create agent profiles in `~/.vibe/agents/`:

```toml
# ~/.vibe/agents/local-dev.toml
active_model = "ollama-devstral"
system_prompt_id = "developer"

[tools.bash]
permission = "always"
```

Use with: `vibe --agent local-dev`

## Usage Examples

### Interactive Mode

```bash
# Start with default provider
vibe

# Start with specific provider and model
vibe --provider ollama --model codellama

# Continue previous session
vibe --continue
```

### One-shot Mode

```bash
# Run a single prompt
vibe --prompt "Find all TODO comments in the project"

# With auto-approve for scripting
vibe --prompt "Run the test suite" --auto-approve
```

### Session Management

```bash
# Resume a specific session
vibe --resume abc123

# View session cost
# (Use /cost command during session)
```

## Development

### Running Tests

```bash
uv run pytest tests/ -v
```

### Syntax Checking

```bash
uv run python -m py_compile vibe/core/tools/builtins/*.py
```

## Enhancements in This Fork

This fork adds the following features to the upstream Mistral Vibe:

1. **Multi-Provider Support**: 10 built-in provider presets (Ollama, llama.cpp, vLLM, LocalAI, LM Studio, OpenAI, OpenRouter, Together AI, Groq, Mistral).

2. **Provider CLI Flags**: New command-line options `--provider`, `--model`, `--api-base`, `--no-stream`, `--list-providers`.

3. **Provider Selection in Onboarding**: The setup wizard now includes provider selection.

4. **New Tools**:
   - `glob`: Find files by pattern (`**/*.py`, `src/**/*.ts`)
   - `list_directory`: List directory contents with size/date metadata
   - `web_fetch`: Fetch and extract content from URLs

5. **Full MCP Protocol Support**:
   - Resources: Read data exposed by MCP servers
   - Resource Templates: Dynamic resource URI patterns
   - Prompts: Reusable prompt templates with arguments
   - `mcp_read_resource` tool for LLM to access MCP resources
   - `mcp_get_prompt` tool for LLM to retrieve MCP prompts

6. **New Slash Commands**:
   - `/cost`: Display token usage and estimated cost for the session
   - `/mcp`: Show all MCP servers with their tools, resources, and prompts

7. **Generic Backend Improvements**: Better handling of non-Mistral API responses (finish_reason, tool call index fields).

## Resources

- [CHANGELOG](CHANGELOG.md) - Version history
- [CONTRIBUTING](CONTRIBUTING.md) - Contribution guidelines

## License

Copyright 2025 Mistral AI

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the [LICENSE](LICENSE) file for the full license text.
