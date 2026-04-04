# Tools Architecture in Mistral Vibe

## Overview

Mistral Vibe uses a sophisticated tool system that allows the AI agent to interact with files, run commands, and perform various operations. The architecture is designed for safety, extensibility, and permission management.

## LSP Plugin Tools

The Language Server Protocol (LSP) plugin provides specialized tools that integrate with language servers for advanced code intelligence:

### LSP Tools Architecture (`vibe/core/plugins/builtin/lsp/tools.py`)

LSP tools follow a different pattern than built-in tools:

1. **Base Class**: `_LspBaseTool(BaseTool)` - Shared base class for file-based LSP operations
2. **Simplified Interface**: Uses JSON Schema instead of Pydantic models
3. **String Results**: Return formatted strings instead of ToolResult objects
4. **Factory Pattern**: Instantiated via `make_lsp_tools()` function
5. **Client Management**: Tools receive a `_clients` dict mapping languages to LSP clients

### Available LSP Tools

1. **lsp_diagnostics** - Get errors/warnings for a file
2. **lsp_completion** - Code completion at a position
3. **lsp_hover** - Type/signature documentation
4. **lsp_definition** - Go to definition location
5. **lsp_references** - Find all symbol references
6. **lsp_status** - Show active language servers

### Integration Pattern

LSP tools are injected into the ToolManager by `LspPlugin.setup()`:
- Factory creates tool instances with config/state
- Attaches LSP-specific attributes (_clients, _detected_languages, etc.)
- Tools use `_client_for(file_path)` to route to correct language server
- Error handling returns user-friendly messages

## Core Components

### 1. BaseTool Class (`vibe/core/tools/base.py`)

The foundation of all tools in Mistral Vibe. It's a generic abstract base class with type parameters:

```python
BaseTool[ToolArgs, ToolResult, ToolConfig, ToolState]
```

- **ToolArgs**: Pydantic model defining the tool's input arguments
- **ToolResult**: Pydantic model defining the tool's output/result
- **ToolConfig**: Configuration for the tool (inherits from `BaseToolConfig`)
- **ToolState**: Internal state of the tool (inherits from `BaseToolState`)

#### Key Methods:

1. **`run(args, ctx)`** - The main async method that implements tool functionality
2. **`resolve_permission(args)`** - Determines if a tool invocation should be allowed/denied/asked
3. **`invoke(ctx, **raw)`** - Validates arguments and runs the tool
4. **`get_name()`** - Returns the snake_case name of the tool class
5. **`get_parameters()`** - Returns JSON schema for the tool's arguments
6. **`get_file_snapshot(args)`** - Captures file state before modification (for rewind feature)
7. **`get_result_extra(result)`** - Adds contextual information to results

### 2. Tool Permission System

The permission system controls when tools can be used:

```python
class ToolPermission(StrEnum):
    ALWAYS = auto()  # Always allowed
    NEVER = auto()  # Always denied
    ASK = auto()  # Ask user for confirmation
```

Each tool has a `ToolConfig` that specifies:
- `permission`: Default permission level
- `allowlist`: Patterns that automatically allow (e.g., `["git status", "ls"]`)
- `denylist`: Patterns that automatically deny (e.g., `["rm -rf", "sudo"]`)
- `sensitive_patterns`: Always ask for these patterns

### 3. Built-in Tools

Located in `vibe/core/tools/builtins/`:

#### Bash Tool (`bash.py`)
- Runs shell commands
- Implements complex permission logic using tree-sitter to parse commands
- Checks allowlist/denylist for command patterns
- Detects when commands access directories outside the workdir
- Has Windows-specific handling

#### ReadFile Tool (`read_file.py`)
- Reads files with line offset and limit
- Supports UTF-8 and fallback encoding
- Enforces byte limits for safety
- Can detect AGENTS.md files in parent directories

#### WriteFile Tool (`write_file.py`)
- Writes content to files
- Creates parent directories as needed
- Validates file paths
- Uses atomic writes with temporary files

#### SearchReplace Tool (`search_replace.py`)
- Finds and replaces text in files
- Supports regex patterns
- Can search across multiple files
- Shows diffs of changes

#### Grep Tool (`grep.py`)
- Searches file contents with regex
- Supports include/exclude patterns
- Returns matching lines with context

#### Other Tools:
- `ask_user_question.py` - Interact with user
- `exit_plan_mode.py` - Exit planning mode
- `skill.py` - Manage skills
- `task.py` - Create sub-tasks
- `todo.py` - Manage todo lists
- `webfetch.py` - Fetch web content
- `websearch.py` - Search the web

## Tool Lifecycle

1. **Invocation**: User or agent requests a tool via natural language
2. **Argument Parsing**: Input is parsed into the ToolArgs Pydantic model
3. **Permission Check**: `resolve_permission()` determines if tool should run
4. **Approval**: If permission is ASK, user must approve
5. **Execution**: `run()` method executes the tool logic
6. **Result**: Returns ToolResult or raises ToolError
7. **Display**: Results are formatted for UI/CLI output

## MCP Tools

Mistral Vibe also supports Model Context Protocol (MCP) tools:
- Located in `vibe/core/tools/mcp/`
- Dynamically loaded from MCP servers
- Integrated with the same permission system

## Creating a New Tool

To create a new tool, follow this pattern:

```python
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState


class MyToolArgs(BaseModel):
    param1: str = Field(description="First parameter")
    param2: int = Field(default=42, description="Second parameter")


class MyToolResult(BaseModel):
    output: str
    status: str


class MyToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    custom_setting: str = "default"


class MyToolState(BaseToolState):
    counter: int = 0


class MyTool(BaseTool[MyToolArgs, MyToolResult, MyToolConfig, MyToolState]):
    description = "What this tool does"

    async def run(self, args: MyToolArgs, ctx: InvokeContext | None = None):
        # Implementation here
        yield MyToolResult(output="result", status="success")
```

## Key Design Patterns

1. **Type Safety**: All tools must use Pydantic models for arguments and results
2. **Permission Granularity**: Tools can override config-level permissions per invocation
3. **Streaming Results**: Tools yield results asynchronously (though most return once)
4. **Error Handling**: ToolError exceptions are caught and displayed to users
5. **UI Integration**: Tools provide display methods for CLI/TUI output
6. **Factory Pattern**: LSP tools use factory-based instantiation with post-construction attribute attachment

## Safety Features

1. **Path Validation**: Prevents reading/writing outside workdir
2. **Byte Limits**: Prevents reading/writing huge files
3. **Command Parsing**: Tree-sitter parses bash commands for safety checks
4. **Permission System**: Multi-layered approval system
5. **File Snapshots**: Captures file state before modification (for rewind)
