# Mistral Vibe - Complete Technische Documentatie

## Overzicht

Mistral Vibe is een open-source CLI coding assistant ontwikkeld door Mistral AI. Het biedt een conversationele interface voor je codebase, waarmee je natuurlijke taal kunt gebruiken om code te verkennen, aan te passen en te beheren via een krachtige set tools.

**Repository**: https://github.com/mistralai/mistral-vibe  
**Licentie**: Apache 2.0  
**Python Versie**: 3.12+  
**Platform Support**: Linux, macOS (officieel), Windows (experimenteel)

---

## Architectuur Overzicht

### Core Componenten

```
mistral-vibe/
├── vibe/                    # Hoofdapplicatie code
│   ├── core/               # Kernfunctionaliteit
│   ├── cli/                # Command-line interface
│   ├── tools/              # Tool implementations
│   └── agent/              # Agent logica
├── scripts/                # Utility scripts
├── tests/                  # Test suite
├── distribution/           # Distribution specifieke code
│   └── zed/               # Zed editor integratie
└── .github/                # CI/CD workflows
```

---

## Bestandsstructuur & Uitleg per Directory

### 1. Root Level Bestanden

#### `pyproject.toml`
**Functie**: Project configuratie en dependencies  
**Belangrijke Secties**:
- Build systeem configuratie (gebruikt `hatchling`)
- Package metadata (naam, versie, auteurs)
- Dependencies lijst (Python packages)
- Development dependencies
- Entry points voor CLI commands
- Tool configuraties (ruff, mypy, pytest)

**Gebruik**: Bevat alle project metadata en dependency management. Wordt gebruikt door `uv` of `pip` voor installatie.

#### `uv.lock`
**Functie**: Lock file voor reproduceerbare builds  
**Inhoud**: Exacte versies van alle dependencies en sub-dependencies  
**Gebruik**: Zorgt dat alle teamleden en CI/CD exact dezelfde package versies gebruiken

#### `action.yml`
**Functie**: GitHub Actions workflow definitie  
**Gebruik**: Definieert hoe Vibe kan worden gebruikt als GitHub Action in CI/CD pipelines  
**Features**:
- Automatische code reviews
- Code quality checks
- Integration testing

#### `.python-version`
**Functie**: Specificeert de vereiste Python versie  
**Inhoud**: `3.12` of hoger  
**Gebruik**: Gebruikt door `pyenv` en andere version managers

#### `.gitignore`
**Functie**: Git exclusions  
**Bevat typisch**:
- `__pycache__/`
- `*.pyc`
- `.venv/`
- `.env`
- `dist/`
- Build artifacts

#### `.pre-commit-config.yaml`
**Functie**: Pre-commit hooks configuratie  
**Hooks**:
- Code formatting (ruff, black)
- Linting
- Type checking
- Trailing whitespace removal
- YAML validation

#### `.typos.toml`
**Functie**: Spell checker configuratie  
**Gebruik**: Voorkomt typos in code en documentatie

#### `.envrc`
**Functie**: Direnv configuratie  
**Gebruik**: Automatisch environment setup bij directory entry  
**Kan bevatten**: Environment variables, PATH wijzigingen

#### `flake.nix` & `flake.lock`
**Functie**: Nix flake definitie voor reproduceerbare development environments  
**Gebruik**: Alternatieve development setup via Nix package manager  
**Voordelen**: Volledig reproduceerbare builds over verschillende machines

#### `vibe-acp.spec`
**Functie**: Agent Client Protocol specificatie  
**Gebruik**: Definieert hoe Vibe communiceert met andere agents/tools

---

### 2. Documentation Bestanden

#### `README.md`
**Sectie Breakdown**:

1. **Installation Methods**:
   - One-line install script
   - `uv` installation
   - `pip` installation

2. **Features**:
   - Interactive chat interface
   - Tool beschrijvingen (`read_file`, `write_file`, `search_replace`, `bash`, `grep`, `todo`)
   - Project-aware context scanning
   - CLI experience features (autocomplete, history, themes)

3. **Quick Start Guide**:
   - Eerste gebruik instructies
   - API key configuratie
   - Basic interaction voorbeelden

4. **Usage Modes**:
   - Interactive mode
   - Programmatic mode
   - Slash commands

5. **Configuration Details**:
   - Config file locaties (`./.vibe/config.toml`, `~/.vibe/config.toml`)
   - API key setup (3 methoden)
   - Custom system prompts
   - Custom agent configurations
   - MCP server setup

#### `AGENTS.md`
**Inhoud**: Guide voor het maken van custom agents  
**Topics**:
- Agent configuratie structuur
- Use cases (red-teaming, specialized tasks)
- Voorbeeld agent configuraties
- Tool permission overrides

#### `CHANGELOG.md`
**Structuur**:
```markdown
## [Version] - Date
### Added
### Changed
### Fixed
### Removed
```
**Gebruik**: Track alle wijzigingen per release

#### `CONTRIBUTING.md`
**Secties**:
- Code of Conduct
- Bug reporting guidelines
- Feature request process
- Development setup
- Testing requirements
- PR submission guidelines

#### `LICENSE`
**Type**: Apache License 2.0  
**Belangrijke Rechten**:
- Commercial gebruik toegestaan
- Modificatie toegestaan
- Distributie toegestaan
- Patent rights granted

---

### 3. `vibe/` Directory - Core Application

#### `vibe/core/`
**Kern functionaliteit modules**:

##### `system_prompt.py`
```python
# Bevat logica voor:
- System prompt generatie
- Project context scanning
- Directory structure analysis
- Git status integratie
```

**Key Features**:
- Scant maximaal N bestanden (configureerbaar via `max_files`)
- Depth limiting voor grote repositories
- Genereert gestructureerde context voor de AI
- Filtert irrelevante bestanden (.git, node_modules, etc.)

**Voorbeeld Output Format**:
```
Directory structure of project_name (depth≤3, max 100 items):
├── src/
│   ├── main.py
│   └── utils.py
└── tests/
    └── test_main.py

Git Status:
- Modified: src/main.py
- Untracked: new_feature.py
```

##### `config.py`
```python
# Configuration management
- Laadt config.toml
- Environment variable parsing
- Default values
- Validatie
```

**Configuratie Schema**:
```toml
# Model configuratie
active_model = "devstral-2"
system_prompt_id = "core"

# API Keys (in .env file)
# MISTRAL_API_KEY=xxx

# Tool permissions
[tools.bash]
permission = "ask"  # "always", "ask", "never"

[tools.read_file]
permission = "always"

# MCP Servers
[[mcp_servers]]
name = "my_server"
transport = "http"
url = "http://localhost:8000"
```

##### `agent.py`
```python
# Agent orchestration
- Message handling
- Tool calling logic
- Conversation state management
- Error handling en recovery
```

**Agent Loop**:
1. User input → Context toevoegen
2. Send to LLM
3. Parse response (text + tool calls)
4. Execute tools (met permission checks)
5. Feed tool results back
6. Loop tot completion

##### `context_manager.py`
```python
# Beheert conversatie context
- Message history
- Token counting
- Context window management
- Relevante code snippets caching
```

#### `vibe/cli/`
**CLI Interface Components**:

##### `main.py`
```python
# Entry point
def main():
    # Parse arguments
    # Initialize agent
    # Start interactive loop of run single prompt
    pass
```

**CLI Modes**:
- Interactive: `vibe`
- Single prompt: `vibe "prompt text"`
- Piped input: `echo "prompt" | vibe`

##### `repl.py`
```python
# REPL (Read-Eval-Print Loop)
- Prompt rendering
- Input handling
- Multi-line support (Ctrl+J)
- Command history
- Autocomplete
```

**Special Syntax**:
- `@filename` - File path autocomplete
- `/command` - Slash commands
- `!command` - Direct shell execution

##### `commands.py`
```python
# Slash command implementations
/help       # Show available commands
/clear      # Clear conversation
/reset      # Reset agent state  
/config     # Show/modify config
/exit       # Exit application
```

##### `autocomplete.py`
```python
# Autocomplete engine
- File path completion (@)
- Slash command completion (/)
- Context-aware suggestions
```

#### `vibe/tools/`
**Tool Implementations**:

Elke tool is een aparte module met:
```python
class ToolName:
    name: str
    description: str
    parameters: dict  # JSON Schema
    
    async def execute(self, **params) -> str:
        # Tool implementation
        pass
```

##### `read_file.py`
```python
# Leest bestand inhoud
parameters:
    path: str  # Bestandspad (relatief of absoluut)
returns: str   # Bestand inhoud
```

##### `write_file.py`
```python
# Schrijft naar bestand
parameters:
    path: str     # Bestandspad
    content: str  # Nieuwe inhoud
returns: str      # Bevestiging
```

##### `search_replace.py`
```python
# Vervang tekst in bestand
parameters:
    path: str        # Bestandspad
    old_text: str    # Te vervangen tekst
    new_text: str    # Nieuwe tekst
returns: str         # Diff van wijzigingen
```

##### `bash.py`
```python
# Voert shell commando's uit
# Stateful terminal session!
parameters:
    command: str  # Shell commando
returns: str      # Stdout + stderr
```

**Belangrijke Features**:
- Persistent shell session per conversatie
- Working directory tracking
- Environment variables preserved
- Multi-line command support

##### `grep.py`
```python
# Recursief zoeken in bestanden
parameters:
    pattern: str    # Regex pattern
    path: str       # Zoekpad (default: ".")
    file_pattern: str  # File glob (optional)
returns: str        # Matches met context
```

**Implementation**:
- Gebruikt `ripgrep` indien beschikbaar (veel sneller)
- Falls back naar Python's `grep`
- Respects .gitignore

##### `list_dir.py` (`ls`)
```python
# Lijst directory inhoud
parameters:
    path: str  # Directory pad (optional)
returns: str   # Formatted directory listing
```

##### `todo.py`
```python
# Task tracking voor de agent
commands:
    add: str     # Voeg taak toe
    remove: int  # Verwijder taak
    list: None   # Toon alle taken
returns: str     # Updated todo lijst
```

**Use Case**: Agent kan zijn eigen werk plannen en tracken:
```
1. [ ] Implement feature X
2. [✓] Write tests
3. [ ] Update documentation
```

---

### 4. `scripts/` Directory

#### `install.sh`
**Functie**: One-line installer script  
**Wat het doet**:
1. Detecteert OS (Linux/macOS)
2. Checkt Python versie
3. Installeert `uv` indien nodig
4. Installeert `mistral-vibe` package
5. Configureert PATH

#### `dev-setup.sh`
**Functie**: Development environment setup  
**Installeert**:
- Pre-commit hooks
- Development dependencies
- Test frameworks
- Linting tools

---

### 5. `tests/` Directory

#### Test Structuur
```
tests/
├── unit/           # Unit tests per module
├── integration/    # Integration tests
├── fixtures/       # Test data
└── conftest.py     # Pytest configuratie
```

#### Test Coverage Areas
- Tool execution
- Configuration loading
- Context management
- CLI commands
- Error handling
- Permission system

**Run Tests**:
```bash
pytest                    # Alle tests
pytest tests/unit/        # Alleen unit tests
pytest -v                 # Verbose output
pytest --cov=vibe         # Met coverage report
```

---

### 6. `.vscode/` Directory

#### `settings.json`
```json
{
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true,
  "editor.formatOnSave": true,
  "python.formatting.provider": "black"
}
```

#### `launch.json`
**Debug Configurations**:
- Run Vibe in debug mode
- Attach to running process
- Test debugging

---

### 7. `.github/` Directory

#### `workflows/ci.yml`
**CI Pipeline Stages**:
1. **Lint**: Ruff, mypy, pre-commit
2. **Test**: pytest met coverage
3. **Build**: Package build test
4. **Release**: Auto-publish naar PyPI (op tag)

**Triggers**:
- Push naar main
- Pull requests
- Tags (voor releases)

---

## Configuration Deep Dive

### Config File Locaties (Prioriteit)
1. `./.vibe/config.toml` (Project-specific)
2. `~/.vibe/config.toml` (User-level)
3. Default values (hardcoded)

### Complete Config Example
```toml
# Model Settings
active_model = "devstral-2"
temperature = 0.7
max_tokens = 4096

# System Prompt
system_prompt_id = "core"  # of custom prompt in ~/.vibe/prompts/

# Project Context
[context]
max_depth = 3          # Max directory depth voor scanning
max_files = 100        # Max aantal bestanden in context
ignore_patterns = [    # Extra patterns om te negeren
    "*.log",
    "node_modules/*",
    ".env"
]

# Tool Permissions
[tools.bash]
permission = "ask"     # "always" | "ask" | "never"

[tools.read_file]
permission = "always"

[tools.write_file]
permission = "ask"

[tools.search_replace]
permission = "ask"

[tools.grep]
permission = "always"

[tools.todo]
permission = "always"

# Disabled Tools
disabled_tools = []    # Tool namen om uit te schakelen

# Enabled Tools (als specified, alleen deze)
enabled_tools = ["*"]  # Glob patterns: ["serena_*", "bash"]

# MCP Server Configurations
[[mcp_servers]]
name = "fetch_server"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]

[[mcp_servers]]
name = "http_api"
transport = "http"
url = "http://localhost:8000"
headers = { "Authorization" = "Bearer token123" }

[[mcp_servers]]
name = "streaming_api"
transport = "streamable-http"
url = "http://localhost:8001"
api_key_env = "MY_API_KEY"
api_key_header = "X-API-Key"
api_key_format = "Bearer {token}"

# UI Settings
[ui]
theme = "dark"         # "dark" | "light" | "auto"
show_timestamps = true
color_output = true
```

---

## Custom System Prompts

### Locatie
`~/.vibe/prompts/<prompt_id>.md`

### Structuur
```markdown
# System Prompt: <Name>

## Role
Je bent een [beschrijving].

## Capabilities
- Capability 1
- Capability 2

## Guidelines
1. Guideline 1
2. Guideline 2

## Tools Available
[Automatisch toegevoegd door Vibe]

## Project Context
[Automatisch toegevoegd door Vibe]
```

### Gebruik
```toml
# In config.toml
system_prompt_id = "my_custom_prompt"
```

Dit laadt `~/.vibe/prompts/my_custom_prompt.md`

---

## Custom Agents

### Locatie
`~/.vibe/agents/<agent_name>.toml`

### Voorbeeld: Red Team Agent
```toml
# ~/.vibe/agents/redteam.toml

# Model override
active_model = "devstral-2"
temperature = 0.9  # Hoger voor creativiteit

# Custom system prompt
system_prompt_id = "redteam"

# Tool restrictions
disabled_tools = ["write_file", "bash"]

# Permission overrides
[tools.read_file]
permission = "always"

[tools.grep]
permission = "always"
```

### Gebruik
```bash
vibe --agent redteam
```

---

## MCP (Model Context Protocol) Servers

### Wat is MCP?
Protocol voor het uitbreiden van Vibe met externe tools/services.

### Transport Types

#### 1. HTTP
```toml
[[mcp_servers]]
name = "my_api"
transport = "http"
url = "http://localhost:8000"
headers = { "X-API-Key" = "secret" }
```

#### 2. Streamable HTTP
```toml
[[mcp_servers]]
name = "streaming_api"
transport = "streamable-http"
url = "http://localhost:8001"
```

#### 3. STDIO
```toml
[[mcp_servers]]
name = "local_tool"
transport = "stdio"
command = "python"
args = ["-m", "my_tool"]
```

### Tool Naming
MCP tools krijgen prefix: `<server_name>_<tool_name>`

Bijvoorbeeld:
- Server: `serena`
- Tool: `search`
- Vibe tool name: `serena_search`

### Tool Filtering
```toml
# Alleen MCP tools van specifieke server
enabled_tools = ["serena_*"]

# Alles behalve MCP tools
disabled_tools = ["mcp_*"]

# Regex pattern
enabled_tools = ["re:^serena_.*$"]
```

---

## Environment Variables

### `VIBE_HOME`
**Default**: `~/.vibe/`  
**Gebruik**: Custom config directory
```bash
export VIBE_HOME="/custom/path"
```

**Structuur**:
```
$VIBE_HOME/
├── config.toml
├── .env
├── agents/
├── prompts/
├── tools/
└── logs/
```

### `MISTRAL_API_KEY`
**Gebruik**: Mistral API authentication
```bash
export MISTRAL_API_KEY="your_key_here"
```

**Alternatieven**:
1. Environment variable (hoogste prioriteit)
2. `~/.vibe/.env` file
3. Interactive prompt bij eerste gebruik

---

## Tool Permission System

### Permission Levels
- **`always`**: Altijd uitvoeren zonder vragen
- **`ask`**: Vraag gebruiker om goedkeuring
- **`never`**: Blokkeer dit tool volledig

### Auto-Approve Mode
```bash
# CLI flag
vibe --auto-approve "Do something"

# In interactive mode
# Press Shift+Tab to toggle
```

### Use Cases

**Development**:
```toml
[tools.bash]
permission = "ask"      # Veiligheid

[tools.read_file]
permission = "always"   # Veel gebruikt
```

**Production/CI**:
```bash
vibe --auto-approve "Run tests and fix issues"
```

---

## Advanced Features

### File References met `@`
```
> Read the file @src/main.py and explain the logic
```
- Tab completion
- Relative paths
- Absolute paths

### Shell Commands met `!`
```
> !git status
> !npm test
```
Voert direct uit, bypassed de agent.

### Multi-line Input
**Keyboard**:
- `Ctrl+J` of `Shift+Enter`

**Gebruik**:
```
> I need you to:
> 1. Read the config file
> 2. Update the version
> 3. Commit the changes
```

---

## Workflows & Patterns

### 1. Bug Fix Workflow
```
User: Find the bug causing test failures
Agent: [uses grep tool to search for error patterns]
Agent: [uses read_file to examine suspicious files]
Agent: [uses search_replace to fix bug]
Agent: [uses bash to run tests]
Agent: [uses todo to track remaining tasks]
```

### 2. Feature Implementation
```
User: Implement user authentication
Agent: [todo: add "Implement auth logic"]
Agent: [read_file: check existing structure]
Agent: [write_file: create auth.py]
Agent: [search_replace: update main.py]
Agent: [bash: run tests]
Agent: [todo: mark "Implement auth logic" as done]
```

### 3. Code Review
```
User: Review this PR
Agent: [bash: git diff main...feature-branch]
Agent: [read_file: examine changed files]
Agent: [grep: check for potential issues]
Agent: Provides detailed review comments
```

---

## Performance & Optimization

### Context Window Management
- Vibe scant alleen relevante bestanden
- Depth limiting voor grote repos
- `.gitignore` patterns worden gerespecteerd

### Token Usage Optimization
- Incremental context updates
- Relevantie filtering
- Compression van directory listings

### Tool Execution
- Async waar mogelijk
- Caching van file reads
- Batching van shell commands

---

## Troubleshooting

### Common Issues

#### 1. API Key Errors
```
Error: No API key found
```
**Fix**:
```bash
export MISTRAL_API_KEY="your_key"
# Of
echo 'MISTRAL_API_KEY=your_key' > ~/.vibe/.env
```

#### 2. Permission Denied
```
Error: Tool 'bash' execution blocked
```
**Fix**: Update `config.toml`
```toml
[tools.bash]
permission = "ask"  # of "always"
```

#### 3. Large Repository Slow
```
Warning: Large repository detected
```
**Fix**: Adjust limits in `config.toml`
```toml
[context]
max_depth = 2
max_files = 50
```

---

## Best Practices

### 1. Security
- ✅ Gebruik `permission = "ask"` voor destructieve tools
- ✅ Review tool executions in sensitive environments
- ✅ Keep API keys in `.env`, never commit
- ❌ Gebruik nooit `--auto-approve` met untrusted input

### 2. Configuration
- ✅ Project-specific configs in `./.vibe/config.toml`
- ✅ User defaults in `~/.vibe/config.toml`
- ✅ Document custom prompts en agents
- ✅ Version control `.vibe/config.toml` (exclude `.env`)

### 3. Performance
- ✅ Gebruik depth limiting voor grote repos
- ✅ Add irrelevante directories aan ignore patterns
- ✅ Gebruik `ripgrep` voor snellere searches

### 4. Collaboration
- ✅ Share custom agents via team repository
- ✅ Document team-specific workflows
- ✅ Standaardize tool permissions per team

---

## Integration met IDEs

### Zed Editor
**Locatie**: `distribution/zed/`

**Features**:
- Native Vibe integratie
- Inline suggestions
- Context-aware completions

**Setup**:
Zie `distribution/zed/README.md` voor installatie instructies.

---

## Future Features (Roadmap)

Gebaseerd op de repository en community feedback:

1. **More IDE Integrations**: VS Code, IntelliJ
2. **Enhanced MCP Support**: Meer builtin servers
3. **Team Collaboration**: Shared agents en prompts
4. **Advanced Context**: Semantic code understanding
5. **Custom Tool Creation**: Simplified tool development

---

## Conclusie

Mistral Vibe is een krachtige, uitbreidbare coding assistant die:
- Natuurlijke taal gebruikt voor code interacties
- Project context begrijpt via directory scanning en Git
- Veilig tools uitvoert met permission system
- Flexibel is via configuratie en custom agents
- Open source en extensible via MCP protocol

De modulaire architectuur maakt het makkelijk om aan te passen aan specifieke team workflows en om uit te breiden met custom functionaliteit.

---

## Resources

- **GitHub**: https://github.com/mistralai/mistral-vibe
- **PyPI**: https://pypi.org/project/mistral-vibe
- **Mistral AI Blog**: https://mistral.ai/news/devstral-2-vibe-cli
- **API Docs**: https://docs.mistral.ai

Voor vragen of issues: GitHub Issues of Mistral AI Discord