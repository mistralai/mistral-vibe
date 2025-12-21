# Core Module Architecture

This document provides a comprehensive architecture overview of the `vibe/core/` module, the heart of the Mistral Vibe agent system.

## Table of Contents

1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Core Components](#core-components)
4. [Data Flow](#data-flow)
5. [Tool System](#tool-system)
6. [LLM Backend System](#llm-backend-system)
7. [Middleware Pipeline](#middleware-pipeline)
8. [Configuration System](#configuration-system)
9. [File Structure](#file-structure)

---

## Overview

The core module is the central orchestration layer of the Vibe agent. It manages:

- **Agent lifecycle** - Conversation loops, state management, session handling
- **Tool execution** - Discovery, validation, permission checking, execution
- **LLM communication** - Backend abstraction, streaming, token counting
- **Middleware** - Turn limits, price limits, auto-compaction, plan mode
- **Configuration** - Model settings, tool configs, system prompts
- **Session persistence** - Interaction logging, session resume

**Public API**: The module exports only `run_programmatic()` for programmatic usage.

---

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              VIBE CORE MODULE                                   │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                            AGENT (agent.py)                              │   │
│  │                      Primary Orchestrator Class                          │   │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────┐  │   │
│  │  │ Config  │ │  Tool    │ │ Backend  │ │ Middleware │ │ Interaction  │  │   │
│  │  │         │ │ Manager  │ │  (LLM)   │ │  Pipeline  │ │   Logger     │  │   │
│  │  └────┬────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ └──────┬───────┘  │   │
│  │       │           │            │             │                │          │   │
│  └───────┼───────────┼────────────┼─────────────┼────────────────┼──────────┘   │
│          │           │            │             │                │              │
│          ▼           ▼            ▼             ▼                ▼              │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────────┐     │
│  │ config.py │ │  tools/   │ │   llm/    │ │middleware │ │ interaction_  │     │
│  │           │ │           │ │           │ │   .py     │ │   logger.py   │     │
│  │ • Model   │ │ • base    │ │ • backend │ │           │ │               │     │
│  │ • Provider│ │ • manager │ │ • format  │ │ • Turn    │ │ • Session     │     │
│  │ • Tools   │ │ • mcp     │ │ • types   │ │ • Price   │ │   save/load   │     │
│  │ • Modes   │ │ • builtins│ │           │ │ • Compact │ │ • Metadata    │     │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────────┘     │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        SUPPORTING MODULES                                │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌─────────┐ ┌───────────────┐   │   │
│  │  │ types   │ │ modes   │ │system_prompt│ │  utils  │ │autocompletion │   │   │
│  │  │  .py    │ │  .py    │ │    .py      │ │  .py    │ │     /         │   │   │
│  │  └─────────┘ └─────────┘ └─────────────┘ └─────────┘ └───────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### Agent Class (`agent.py`)

The `Agent` class is the central orchestrator that manages the entire conversation lifecycle.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                   AGENT CLASS                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ATTRIBUTES                           DEPENDENCIES                               │
│  ┌──────────────────────┐            ┌──────────────────────┐                   │
│  │ • config: VibeConfig │◄───────────│ config.py            │                   │
│  │ • messages: list     │            └──────────────────────┘                   │
│  │ • stats: AgentStats  │            ┌──────────────────────┐                   │
│  │ • session_id: str    │            │ types.py             │                   │
│  │ • tool_manager       │◄───────────│ • LLMMessage         │                   │
│  │ • format_handler     │            │ • AgentStats         │                   │
│  │ • backend            │◄───────────│ • BaseEvent          │                   │
│  │ • middleware_pipeline│            └──────────────────────┘                   │
│  │ • interaction_logger │            ┌──────────────────────┐                   │
│  │ • approval_callback  │            │ tools/manager.py     │                   │
│  └──────────────────────┘            └──────────────────────┘                   │
│                                      ┌──────────────────────┐                   │
│  PUBLIC METHODS                      │ llm/backend/         │                   │
│  ┌──────────────────────────────┐    └──────────────────────┘                   │
│  │ • act(msg) → AsyncGen[Event]│    ┌──────────────────────┐                   │
│  │ • add_message(message)       │    │ middleware.py        │                   │
│  │ • set_approval_callback()    │    └──────────────────────┘                   │
│  │ • switch_mode(mode)          │    ┌──────────────────────┐                   │
│  │ • reload_with_initial_msgs() │    │ interaction_logger   │                   │
│  │ • clear_history()            │    └──────────────────────┘                   │
│  │ • compact() → summary        │                                               │
│  └──────────────────────────────┘                                               │
│                                                                                  │
│  PRIVATE METHODS                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │ • _conversation_loop(msg) → AsyncGen[Event]  # Main loop                 │   │
│  │ • _perform_llm_turn() → AsyncGen[Event]      # Single LLM call + tools   │   │
│  │ • _chat(max_tokens) → LLMChunk               # Non-streaming LLM call    │   │
│  │ • _chat_streaming() → AsyncGen[LLMChunk]     # Streaming LLM call        │   │
│  │ • _handle_tool_calls(resolved) → AsyncGen    # Tool execution            │   │
│  │ • _should_execute_tool(tool, args, id)       # Permission check          │   │
│  │ • _ask_approval(tool, args, id)              # User approval             │   │
│  │ • _get_context() → ConversationContext       # Context for middleware    │   │
│  │ • _setup_middleware()                        # Initialize pipeline       │   │
│  │ • _clean_message_history()                   # Remove invalid messages   │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Type System (`types.py`)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                    TYPES                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  EVENTS (Yielded by Agent.act())         MESSAGES                               │
│  ┌─────────────────────────────┐        ┌─────────────────────────────┐         │
│  │         BaseEvent           │        │         LLMMessage          │         │
│  │         (abstract)          │        │                             │         │
│  └──────────────┬──────────────┘        │ • role: Role                │         │
│                 │                        │ • content: Content|None     │         │
│    ┌────────────┼────────────┐          │ • tool_calls: list|None     │         │
│    │            │            │          │ • name: str|None            │         │
│    ▼            ▼            ▼          │ • tool_call_id: str|None    │         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐    └─────────────────────────────┘         │
│  │Assistant│ │ToolCall │ │ToolRes  │                                            │
│  │Event    │ │Event    │ │Event    │    ┌─────────────────────────────┐         │
│  │         │ │         │ │         │    │           Role              │         │
│  │•content │ │•tool_nm │ │•result  │    │                             │         │
│  │•stopped │ │•args    │ │•error   │    │ • system   • assistant      │         │
│  └─────────┘ │•call_id │ │•skipped │    │ • user     • tool           │         │
│              └─────────┘ │•duration│    └─────────────────────────────┘         │
│                          └─────────┘                                            │
│                                                                                  │
│  ┌─────────────┐  ┌─────────────┐                                               │
│  │CompactStart │  │ CompactEnd  │                                               │
│  │Event        │  │ Event       │                                               │
│  └─────────────┘  └─────────────┘                                               │
│                                                                                  │
│  STATISTICS                              TOOL CALLS                             │
│  ┌─────────────────────────────┐        ┌─────────────────────────────┐         │
│  │        AgentStats           │        │         ToolCall            │         │
│  │                             │        │                             │         │
│  │ • steps                     │        │ • id: str                   │         │
│  │ • session_prompt_tokens     │        │ • index: int                │         │
│  │ • session_completion_tokens │        │ • function: FunctionCall    │         │
│  │ • tool_calls_agreed         │        │ • type: "function"          │         │
│  │ • tool_calls_rejected       │        └─────────────────────────────┘         │
│  │ • tool_calls_succeeded      │                                                │
│  │ • context_tokens            │        ┌─────────────────────────────┐         │
│  │ • tokens_per_second         │        │       FunctionCall          │         │
│  │                             │        │                             │         │
│  │ @computed: session_cost     │        │ • name: str                 │         │
│  └─────────────────────────────┘        │ • arguments: str (JSON)     │         │
│                                          └─────────────────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Conversation Loop

```
┌──────────────┐
│  User Input  │
│   (string)   │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           Agent.act(message)                                  │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                     _conversation_loop(message)                          │ │
│  │                                                                          │ │
│  │    ┌───────────────────────┐                                            │ │
│  │    │ Add user message to   │                                            │ │
│  │    │ self.messages         │                                            │ │
│  │    └───────────┬───────────┘                                            │ │
│  │                │                                                         │ │
│  │                ▼                                                         │ │
│  │    ┌───────────────────────────────────────────────────────────────┐    │ │
│  │    │                    CONVERSATION LOOP                           │    │ │
│  │    │                                                                │    │ │
│  │    │    ┌─────────────────────┐                                    │    │ │
│  │    │    │ middleware.before   │──────► STOP/COMPACT/INJECT?        │    │ │
│  │    │    └──────────┬──────────┘              │                      │    │ │
│  │    │               │                         │                      │    │ │
│  │    │               ▼                         │                      │    │ │
│  │    │    ┌─────────────────────┐              │                      │    │ │
│  │    │    │ _perform_llm_turn() │◄─────────────┘                      │    │ │
│  │    │    │                     │                                     │    │ │
│  │    │    │ • Call LLM backend  │──────► yield AssistantEvent         │    │ │
│  │    │    │ • Parse tool calls  │                                     │    │ │
│  │    │    │ • Execute tools     │──────► yield ToolCallEvent          │    │ │
│  │    │    │                     │──────► yield ToolResultEvent        │    │ │
│  │    │    └──────────┬──────────┘                                     │    │ │
│  │    │               │                                                │    │ │
│  │    │               ▼                                                │    │ │
│  │    │    ┌─────────────────────┐                                    │    │ │
│  │    │    │ middleware.after    │                                    │    │ │
│  │    │    └──────────┬──────────┘                                    │    │ │
│  │    │               │                                                │    │ │
│  │    │               ▼                                                │    │ │
│  │    │         finish_reason?  ───────► break loop                   │    │ │
│  │    │               │                                                │    │ │
│  │    │               └──────────────────► continue loop              │    │ │
│  │    │                                                                │    │ │
│  │    └────────────────────────────────────────────────────────────────┘    │ │
│  │                                                                          │ │
│  │    ┌─────────────────────┐                                              │ │
│  │    │ Save interaction    │                                              │ │
│  │    │ (InteractionLogger) │                                              │ │
│  │    └─────────────────────┘                                              │ │
│  │                                                                          │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Tool Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           TOOL EXECUTION FLOW                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌───────────────────────┐                                                      │
│  │ LLM Response with     │                                                      │
│  │ tool_calls            │                                                      │
│  └───────────┬───────────┘                                                      │
│              │                                                                   │
│              ▼                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                   APIToolFormatHandler.parse_message()                     │  │
│  │                                                                            │  │
│  │  Extract tool calls from LLM message → ParsedMessage                       │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│              │                                                                   │
│              ▼                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                  APIToolFormatHandler.resolve_tool_calls()                 │  │
│  │                                                                            │  │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────┐    │  │
│  │  │ Match tool name │───►│ Validate args   │───►│ Create Resolved     │    │  │
│  │  │ to ToolManager  │    │ with Pydantic   │    │ or Failed ToolCall  │    │  │
│  │  └─────────────────┘    └─────────────────┘    └─────────────────────┘    │  │
│  │                                                                            │  │
│  │  Returns: ResolvedMessage { tool_calls: [], failed_calls: [] }             │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│              │                                                                   │
│              ▼                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                     For each ResolvedToolCall:                             │  │
│  │                                                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │                    yield ToolCallEvent                               │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                              │                                            │  │
│  │                              ▼                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │               _should_execute_tool(tool, args, id)                   │  │  │
│  │  │                                                                      │  │  │
│  │  │  ┌───────────────┐    ┌───────────────┐    ┌────────────────────┐   │  │  │
│  │  │  │ Check mode    │───►│ Check tool    │───►│ Check allow/deny   │   │  │  │
│  │  │  │ auto_approve  │    │ permission    │    │ list patterns      │   │  │  │
│  │  │  └───────────────┘    │ ALWAYS/NEVER  │    └────────────────────┘   │  │  │
│  │  │                       │ /ASK          │              │              │  │  │
│  │  │                       └───────────────┘              ▼              │  │  │
│  │  │                                            ┌────────────────────┐   │  │  │
│  │  │                                            │ approval_callback  │   │  │  │
│  │  │                                            │ (if needed)        │   │  │  │
│  │  │                                            └────────────────────┘   │  │  │
│  │  │                                                                      │  │  │
│  │  │  Returns: ToolDecision { execute: bool, reason: str }                │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                              │                                            │  │
│  │                   ┌──────────┴──────────┐                                │  │
│  │                   │                      │                                │  │
│  │              execute=True           execute=False                         │  │
│  │                   │                      │                                │  │
│  │                   ▼                      ▼                                │  │
│  │  ┌─────────────────────────┐  ┌─────────────────────────┐                │  │
│  │  │   tool.invoke(**args)   │  │  Skip with reason       │                │  │
│  │  │                         │  │                         │                │  │
│  │  │  • Validate args        │  └────────────┬────────────┘                │  │
│  │  │  • Call tool.run()      │               │                             │  │
│  │  │  • Return ToolResult    │               │                             │  │
│  │  └────────────┬────────────┘               │                             │  │
│  │               │                            │                             │  │
│  │               └────────────┬───────────────┘                             │  │
│  │                            │                                             │  │
│  │                            ▼                                             │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐│  │
│  │  │  Add tool response message to self.messages                         ││  │
│  │  │  yield ToolResultEvent { result, error, skipped, duration }         ││  │
│  │  └─────────────────────────────────────────────────────────────────────┘│  │
│  │                                                                         │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Tool System

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 TOOL SYSTEM                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                        ToolManager (tools/manager.py)                      │  │
│  │                                                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │                         DISCOVERY                                    │  │  │
│  │  │                                                                      │  │  │
│  │  │   Search Paths:                                                      │  │  │
│  │  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │  │  │
│  │  │   │ vibe/core/tools │  │ config.tool_   │  │ .vibe/tools     │     │  │  │
│  │  │   │ /builtins       │  │ paths[]        │  │ (if trusted)    │     │  │  │
│  │  │   └─────────────────┘  └─────────────────┘  └─────────────────┘     │  │  │
│  │  │              │                  │                    │               │  │  │
│  │  │              └──────────────────┴────────────────────┘               │  │  │
│  │  │                                 │                                    │  │  │
│  │  │                                 ▼                                    │  │  │
│  │  │   ┌───────────────────────────────────────────────────────────────┐ │  │  │
│  │  │   │ _iter_tool_classes(): Scan .py files, find BaseTool subclass  │ │  │  │
│  │  │   └───────────────────────────────────────────────────────────────┘ │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │                       MCP INTEGRATION                                │  │  │
│  │  │                                                                      │  │  │
│  │  │   ┌─────────────────┐           ┌─────────────────┐                 │  │  │
│  │  │   │  HTTP Servers   │           │  Stdio Servers  │                 │  │  │
│  │  │   │                 │           │                 │                 │  │  │
│  │  │   │ • list_tools    │           │ • list_tools    │                 │  │  │
│  │  │   │ • create_proxy  │           │ • create_proxy  │                 │  │  │
│  │  │   └────────┬────────┘           └────────┬────────┘                 │  │  │
│  │  │            │                             │                          │  │  │
│  │  │            └──────────────┬──────────────┘                          │  │  │
│  │  │                           │                                         │  │  │
│  │  │                           ▼                                         │  │  │
│  │  │              Register proxy tool classes                            │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                            │  │
│  │  Methods:                                                                  │  │
│  │  • available_tools() → dict[str, type[BaseTool]]                          │  │
│  │  • get(tool_name) → BaseTool  (lazy instantiation)                        │  │
│  │  • get_tool_config(name) → BaseToolConfig                                 │  │
│  │  • reset_all()                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                    BaseTool[ToolArgs, ToolResult, Config, State]           │  │
│  │                               (tools/base.py)                              │  │
│  │                                                                            │  │
│  │  Class Variables:                     Abstract Methods:                    │  │
│  │  ┌────────────────────────┐          ┌──────────────────────────────┐     │  │
│  │  │ • description: str     │          │ async run(args) → ToolResult │     │  │
│  │  │ • prompt_path: Path    │          └──────────────────────────────┘     │  │
│  │  └────────────────────────┘                                               │  │
│  │                                                                            │  │
│  │  Methods:                                                                  │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │ • invoke(**raw) → ToolResult    # Validates args and calls run()    │  │  │
│  │  │ • get_name() → str              # Class method                      │  │  │
│  │  │ • get_parameters() → dict       # JSON Schema for args              │  │  │
│  │  │ • get_tool_prompt() → str|None  # Load prompt from file             │  │  │
│  │  │ • from_config(config) → tool    # Factory                           │  │  │
│  │  │ • check_allowlist_denylist()    # Permission check hook             │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                          BUILT-IN TOOLS                                    │  │
│  │                         (tools/builtins/)                                  │  │
│  │                                                                            │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │  │
│  │  │  bash.py    │  │ read_file   │  │ write_file  │  │ search_replace  │   │  │
│  │  │             │  │   .py       │  │   .py       │  │     .py         │   │  │
│  │  │ Execute     │  │ Read file   │  │ Write to    │  │ Search and      │   │  │
│  │  │ bash cmds   │  │ contents    │  │ files       │  │ replace text    │   │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘   │  │
│  │                                                                            │  │
│  │  ┌─────────────┐  ┌─────────────┐                                         │  │
│  │  │  grep.py    │  │  todo.py    │                                         │  │
│  │  │             │  │             │                                         │  │
│  │  │ Search in   │  │ Task list   │                                         │  │
│  │  │ files       │  │ management  │                                         │  │
│  │  └─────────────┘  └─────────────┘                                         │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                       TOOL PERMISSIONS                                     │  │
│  │                                                                            │  │
│  │  ┌───────────────────────────────────────────────────────────────────┐    │  │
│  │  │            ToolPermission (StrEnum)                                │    │  │
│  │  │                                                                    │    │  │
│  │  │   ALWAYS ─────► Auto-approve all executions                        │    │  │
│  │  │   NEVER  ─────► Always deny execution                              │    │  │
│  │  │   ASK    ─────► Prompt user for approval (default)                 │    │  │
│  │  └───────────────────────────────────────────────────────────────────┘    │  │
│  │                                                                            │  │
│  │  ┌───────────────────────────────────────────────────────────────────┐    │  │
│  │  │            BaseToolConfig                                          │    │  │
│  │  │                                                                    │    │  │
│  │  │   • permission: ToolPermission                                     │    │  │
│  │  │   • workdir: Path | None                                           │    │  │
│  │  │   • allowlist: list[str]  ──► Glob patterns to always allow        │    │  │
│  │  │   • denylist: list[str]   ──► Glob patterns to always deny         │    │  │
│  │  └───────────────────────────────────────────────────────────────────┘    │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## LLM Backend System

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              LLM BACKEND SYSTEM                                  │
│                                   (llm/)                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                    BackendLike Protocol (llm/types.py)                     │  │
│  │                                                                            │  │
│  │  Async Context Manager Protocol:                                           │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │ • __aenter__() → BackendLike                                        │  │  │
│  │  │ • __aexit__(exc_type, exc_val, exc_tb) → None                       │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                            │  │
│  │  Methods:                                                                  │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │ complete(model, messages, temperature, tools, max_tokens,           │  │  │
│  │  │          tool_choice, extra_headers) → LLMChunk                     │  │  │
│  │  │                                                                      │  │  │
│  │  │ complete_streaming(...) → AsyncGenerator[LLMChunk]                   │  │  │
│  │  │                                                                      │  │  │
│  │  │ count_tokens(model, messages, temperature, tools,                    │  │  │
│  │  │              tool_choice, extra_headers) → int                       │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│                                       │                                          │
│                    ┌──────────────────┴──────────────────┐                      │
│                    │                                      │                      │
│                    ▼                                      ▼                      │
│  ┌───────────────────────────────────┐  ┌───────────────────────────────────┐  │
│  │     MistralBackend                 │  │     GenericBackend                │  │
│  │    (llm/backend/mistral.py)        │  │   (llm/backend/generic.py)        │  │
│  │                                    │  │                                    │  │
│  │  • Mistral-specific API client     │  │  • OpenAI-compatible API client   │  │
│  │  • Token counting via API          │  │  • Works with any OpenAI-like     │  │
│  │  • Native Mistral message format   │  │    endpoint                       │  │
│  │                                    │  │  • Token counting estimation      │  │
│  └───────────────────────────────────┘  └───────────────────────────────────┘  │
│                                                                                  │
│                                       ▲                                          │
│                                       │                                          │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                  Backend Factory (llm/backend/factory.py)                  │  │
│  │                                                                            │  │
│  │   BACKEND_FACTORY = {                                                      │  │
│  │       Backend.MISTRAL: MistralBackend,                                     │  │
│  │       Backend.GENERIC: GenericBackend,                                     │  │
│  │   }                                                                        │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                 APIToolFormatHandler (llm/format.py)                       │  │
│  │                                                                            │  │
│  │  Handles tool call parsing and formatting for LLM messages                 │  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │ Input Types:                                                          │ │  │
│  │  │                                                                       │ │  │
│  │  │ ParsedToolCall    ResolvedToolCall     FailedToolCall                │ │  │
│  │  │ ┌─────────────┐   ┌─────────────────┐   ┌─────────────────┐          │ │  │
│  │  │ │• tool_name  │   │• tool_name      │   │• tool_name      │          │ │  │
│  │  │ │• raw_args   │   │• tool_class     │   │• call_id        │          │ │  │
│  │  │ │• call_id    │   │• validated_args │   │• error          │          │ │  │
│  │  │ └─────────────┘   │• call_id        │   └─────────────────┘          │ │  │
│  │  │                   └─────────────────┘                                 │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  │  Methods:                                                                  │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │ • get_available_tools(tool_manager, config) → list[AvailableTool]   │ │  │
│  │  │ • parse_message(message) → ParsedMessage                             │ │  │
│  │  │ • resolve_tool_calls(parsed, tool_manager, config) → ResolvedMessage│ │  │
│  │  │ • get_tool_choice() → StrToolChoice | AvailableTool | None          │ │  │
│  │  │ • process_api_response_message(message) → LLMMessage                 │ │  │
│  │  │ • create_tool_response_message(tool_call, result) → dict            │ │  │
│  │  │ • create_failed_tool_response_message(failed, error) → dict         │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Middleware Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            MIDDLEWARE PIPELINE                                   │
│                             (middleware.py)                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                        MiddlewarePipeline                                  │  │
│  │                                                                            │  │
│  │   Methods:                                                                 │  │
│  │   • add(middleware) → MiddlewarePipeline  (fluent API)                    │  │
│  │   • clear() → None                                                         │  │
│  │   • reset(reason) → None                                                   │  │
│  │   • run_before_turn(context) → MiddlewareResult                           │  │
│  │   • run_after_turn(context) → MiddlewareResult                            │  │
│  │                                                                            │  │
│  │   Execution:                                                               │  │
│  │   ┌─────────────────────────────────────────────────────────────────────┐ │  │
│  │   │  for middleware in self.middlewares:                                 │ │  │
│  │   │      result = await middleware.before_turn(context)                  │ │  │
│  │   │      if result.action != CONTINUE:                                   │ │  │
│  │   │          return result  # Short-circuit                              │ │  │
│  │   │  return MiddlewareResult(action=CONTINUE)                            │ │  │
│  │   └─────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                    ConversationMiddleware Protocol                         │  │
│  │                                                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │ before_turn(context: ConversationContext) → MiddlewareResult        │  │  │
│  │  │ after_turn(context: ConversationContext) → MiddlewareResult         │  │  │
│  │  │ reset(reset_reason: ResetReason) → None                             │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                            │  │
│  │  ConversationContext:                 MiddlewareResult:                   │  │
│  │  ┌──────────────────────┐            ┌──────────────────────────────┐    │  │
│  │  │ • messages: list     │            │ • action: MiddlewareAction   │    │  │
│  │  │ • stats: AgentStats  │            │ • message: str | None        │    │  │
│  │  │ • config: VibeConfig │            │ • reason: str | None         │    │  │
│  │  └──────────────────────┘            │ • metadata: dict             │    │  │
│  │                                       └──────────────────────────────┘    │  │
│  │                                                                            │  │
│  │  MiddlewareAction (StrEnum):                                              │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐│  │
│  │  │ CONTINUE ──────► Proceed normally                                    ││  │
│  │  │ STOP ──────────► End conversation                                    ││  │
│  │  │ COMPACT ───────► Trigger context compaction                          ││  │
│  │  │ INJECT_MESSAGE ► Add a message to conversation                       ││  │
│  │  └──────────────────────────────────────────────────────────────────────┘│  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                        MIDDLEWARE IMPLEMENTATIONS                          │  │
│  │                                                                            │  │
│  │  ┌─────────────────────────────┐   ┌─────────────────────────────┐        │  │
│  │  │    TurnLimitMiddleware      │   │    PriceLimitMiddleware     │        │  │
│  │  │                             │   │                             │        │  │
│  │  │ • Counts turns              │   │ • Tracks session cost       │        │  │
│  │  │ • Stops at max_turns        │   │ • Stops at max_price        │        │  │
│  │  │                             │   │                             │        │  │
│  │  │ Trigger: after_turn         │   │ Trigger: before_turn        │        │  │
│  │  │ Action: STOP                │   │ Action: STOP                │        │  │
│  │  └─────────────────────────────┘   └─────────────────────────────┘        │  │
│  │                                                                            │  │
│  │  ┌─────────────────────────────┐   ┌─────────────────────────────┐        │  │
│  │  │   AutoCompactMiddleware     │   │  ContextWarningMiddleware   │        │  │
│  │  │                             │   │                             │        │  │
│  │  │ • Monitors context size     │   │ • Monitors context usage    │        │  │
│  │  │ • Triggers compaction       │   │ • Warns at thresholds       │        │  │
│  │  │   when threshold reached    │   │   (50%, 75%, 90%)           │        │  │
│  │  │                             │   │                             │        │  │
│  │  │ Trigger: before_turn        │   │ Trigger: after_turn         │        │  │
│  │  │ Action: COMPACT             │   │ Action: INJECT_MESSAGE      │        │  │
│  │  └─────────────────────────────┘   └─────────────────────────────┘        │  │
│  │                                                                            │  │
│  │  ┌─────────────────────────────┐                                          │  │
│  │  │    PlanModeMiddleware       │                                          │  │
│  │  │                             │                                          │  │
│  │  │ • Active in PLAN mode       │                                          │  │
│  │  │ • Injects plan reminders    │                                          │  │
│  │  │                             │                                          │  │
│  │  │ Trigger: before_turn        │                                          │  │
│  │  │ Action: INJECT_MESSAGE      │                                          │  │
│  │  └─────────────────────────────┘                                          │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                         PIPELINE FLOW                                      │  │
│  │                                                                            │  │
│  │        ┌──────────────────────────────────────────────────────────┐       │  │
│  │        │                    BEFORE TURN                            │       │  │
│  │        │                                                           │       │  │
│  │        │  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌────────┐ │       │  │
│  │        │  │ Price   │───►│ Auto    │───►│ Plan    │───►│ More...│ │       │  │
│  │        │  │ Limit   │    │ Compact │    │ Mode    │    │        │ │       │  │
│  │        │  └─────────┘    └─────────┘    └─────────┘    └────────┘ │       │  │
│  │        │                                                           │       │  │
│  │        │  Any STOP/COMPACT action short-circuits the pipeline     │       │  │
│  │        └──────────────────────────────────────────────────────────┘       │  │
│  │                                                                            │  │
│  │                              ▼ LLM Turn ▼                                  │  │
│  │                                                                            │  │
│  │        ┌──────────────────────────────────────────────────────────┐       │  │
│  │        │                    AFTER TURN                             │       │  │
│  │        │                                                           │       │  │
│  │        │  ┌─────────┐    ┌─────────┐    ┌────────┐                │       │  │
│  │        │  │ Turn    │───►│ Context │───►│ More...│                │       │  │
│  │        │  │ Limit   │    │ Warning │    │        │                │       │  │
│  │        │  └─────────┘    └─────────┘    └────────┘                │       │  │
│  │        │                                                           │       │  │
│  │        └──────────────────────────────────────────────────────────┘       │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Configuration System

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           CONFIGURATION SYSTEM                                   │
│                               (config.py)                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                          VibeConfig (BaseSettings)                         │  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │                          SETTINGS SOURCES                             │ │  │
│  │  │                        (Priority high → low)                          │ │  │
│  │  │                                                                       │ │  │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │ │  │
│  │  │  │ Init values  │  │ Environment  │  │ TOML config  │  │ Secrets   │ │ │  │
│  │  │  │ (programatic)│  │ (VIBE_*)     │  │ file         │  │ files     │ │ │  │
│  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └───────────┘ │ │  │
│  │  │         │                 │                 │                │       │ │  │
│  │  │         └─────────────────┴────────┬────────┴────────────────┘       │ │  │
│  │  │                                    │                                  │ │  │
│  │  │                                    ▼                                  │ │  │
│  │  │                         ┌──────────────────┐                         │ │  │
│  │  │                         │   VibeConfig     │                         │ │  │
│  │  │                         └──────────────────┘                         │ │  │
│  │  │                                                                       │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │                         CORE SETTINGS                                 │ │  │
│  │  │                                                                       │ │  │
│  │  │  Model Selection           UI Settings                                │ │  │
│  │  │  ┌─────────────────────┐   ┌─────────────────────┐                   │ │  │
│  │  │  │ • active_model      │   │ • vim_keybindings   │                   │ │  │
│  │  │  │   = "devstral-2"    │   │ • disable_welcome   │                   │ │  │
│  │  │  └─────────────────────┘   │ • textual_theme     │                   │ │  │
│  │  │                            └─────────────────────┘                   │ │  │
│  │  │  Agent Behavior            System Prompt                              │ │  │
│  │  │  ┌─────────────────────┐   ┌─────────────────────┐                   │ │  │
│  │  │  │ • auto_compact      │   │ • system_prompt_id  │                   │ │  │
│  │  │  │ • context_warnings  │   │ • instructions      │                   │ │  │
│  │  │  │ • api_timeout       │   │ • include_*         │                   │ │  │
│  │  │  └─────────────────────┘   └─────────────────────┘                   │ │  │
│  │  │                                                                       │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │                      PROVIDER & MODEL CONFIGS                        │ │  │
│  │  │                                                                       │ │  │
│  │  │  ProviderConfig                  ModelConfig                          │ │  │
│  │  │  ┌────────────────────────┐     ┌────────────────────────┐           │ │  │
│  │  │  │ • name: str            │     │ • name: str            │           │ │  │
│  │  │  │ • api_base: str        │     │ • provider: str        │           │ │  │
│  │  │  │ • api_key_env_var: str │     │ • alias: str | None    │           │ │  │
│  │  │  │ • backend: Backend     │     │ • temperature: float   │           │ │  │
│  │  │  │   (MISTRAL | GENERIC)  │     │ • max_tokens: int      │           │ │  │
│  │  │  └────────────────────────┘     │ • pricing: PricingInfo │           │ │  │
│  │  │                                  └────────────────────────┘           │ │  │
│  │  │                                                                       │ │  │
│  │  │  providers: list[ProviderConfig]                                      │ │  │
│  │  │  models: list[ModelConfig]                                            │ │  │
│  │  │                                                                       │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │                        TOOL CONFIGURATION                            │ │  │
│  │  │                                                                       │ │  │
│  │  │  tools: dict[str, BaseToolConfig]                                     │ │  │
│  │  │  ┌────────────────────────────────────────────────────────────────┐  │ │  │
│  │  │  │ "bash": BashToolConfig(permission=ASK, allowlist=["git *"])   │  │ │  │
│  │  │  │ "read_file": BaseToolConfig(permission=ALWAYS)                 │  │ │  │
│  │  │  │ "write_file": WriteToolConfig(permission=ASK, denylist=[...]) │  │ │  │
│  │  │  └────────────────────────────────────────────────────────────────┘  │ │  │
│  │  │                                                                       │ │  │
│  │  │  tool_paths: list[str]   ◄── Additional tool directories             │ │  │
│  │  │  enabled_tools: list     ◄── Explicitly enabled tools                 │ │  │
│  │  │  disabled_tools: list    ◄── Explicitly disabled tools                │ │  │
│  │  │                                                                       │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │                         MCP SERVERS                                   │ │  │
│  │  │                                                                       │ │  │
│  │  │  mcp_servers: list[MCPServer]                                         │ │  │
│  │  │                                                                       │ │  │
│  │  │  MCPHttp           MCPStreamableHttp          MCPStdio               │ │  │
│  │  │  ┌──────────────┐  ┌──────────────────┐      ┌──────────────────┐    │ │  │
│  │  │  │ • url: str   │  │ • url: str       │      │ • command: str   │    │ │  │
│  │  │  │ • headers    │  │ • headers        │      │ • args: list     │    │ │  │
│  │  │  └──────────────┘  └──────────────────┘      │ • env: dict      │    │ │  │
│  │  │                                               └──────────────────┘    │ │  │
│  │  │                                                                       │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  │  Key Methods:                                                              │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │ • load(agent=None, **overrides) → VibeConfig   # Class method        │ │  │
│  │  │ • save_updates(updates: dict) → None           # Persist changes     │ │  │
│  │  │ • get_active_model() → ModelConfig             # Current model       │ │  │
│  │  │ • get_provider_for_model(model) → ProviderConfig                     │ │  │
│  │  │ • effective_workdir → Path                     # Resolved workdir    │ │  │
│  │  │ • system_prompt → str                          # Built prompt        │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                           AGENT MODES (modes.py)                           │  │
│  │                                                                            │  │
│  │  ┌────────────────────────────────────────────────────────────────────┐   │  │
│  │  │                    AgentMode (StrEnum)                              │   │  │
│  │  │                                                                     │   │  │
│  │  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │   │  │
│  │  │  │   DEFAULT   │ │    PLAN     │ │ACCEPT_EDITS │ │AUTO_APPROVE │   │   │  │
│  │  │  │             │ │             │ │             │ │             │   │   │  │
│  │  │  │ Safety:     │ │ Safety:     │ │ Safety:     │ │ Safety:     │   │   │  │
│  │  │  │  NEUTRAL    │ │   SAFE      │ │ DESTRUCTIVE │ │   YOLO      │   │   │  │
│  │  │  │             │ │             │ │             │ │             │   │   │  │
│  │  │  │ auto_approve│ │ auto_approve│ │ auto_approve│ │ auto_approve│   │   │  │
│  │  │  │   = False   │ │   = False   │ │  = partial  │ │   = True    │   │   │  │
│  │  │  │             │ │             │ │  (edits)    │ │             │   │   │  │
│  │  │  │ All tools   │ │ Read-only   │ │ File edit   │ │ All tools   │   │   │  │
│  │  │  │ require     │ │ tools only  │ │ auto-       │ │ auto-       │   │   │  │
│  │  │  │ approval    │ │ (grep,read) │ │ approved    │ │ approved    │   │   │  │
│  │  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘   │   │  │
│  │  │                                                                     │   │  │
│  │  └────────────────────────────────────────────────────────────────────┘   │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
vibe/core/
│
├── __init__.py                 # Public API: exports only run_programmatic
├── agent.py                    # Agent class - core orchestrator (35KB)
├── config.py                   # VibeConfig - configuration management (18KB)
├── types.py                    # Type definitions, events, messages (6.7KB)
├── middleware.py               # Middleware pipeline system (7.9KB)
├── modes.py                    # Agent modes (DEFAULT, PLAN, etc.) (2.8KB)
├── system_prompt.py            # System prompt generation (14KB)
├── interaction_logger.py       # Session logging and persistence (8.1KB)
├── output_formatters.py        # Output formatting utilities (2.4KB)
├── programmatic.py             # run_programmatic function (2.2KB)
├── utils.py                    # Utility functions (9KB)
├── trusted_folders.py          # Trusted folder management (2.2KB)
│
├── paths/                      # Path management
│   ├── __init__.py
│   ├── config_paths.py         # Config file paths
│   └── global_paths.py         # Global installation paths
│
├── llm/                        # LLM backend abstraction
│   ├── __init__.py
│   ├── types.py                # BackendLike protocol
│   ├── format.py               # APIToolFormatHandler
│   ├── exceptions.py           # LLM exceptions
│   └── backend/
│       ├── __init__.py
│       ├── factory.py          # Backend factory
│       ├── generic.py          # OpenAI-compatible backend
│       └── mistral.py          # Mistral-specific backend
│
├── tools/                      # Tool system
│   ├── __init__.py
│   ├── base.py                 # BaseTool abstract class
│   ├── manager.py              # ToolManager - discovery & instantiation
│   ├── mcp.py                  # MCP tool support
│   ├── ui.py                   # Tool UI helpers
│   └── builtins/               # Built-in tools
│       ├── __init__.py
│       ├── bash.py             # Bash command execution
│       ├── read_file.py        # File reading
│       ├── write_file.py       # File writing
│       ├── search_replace.py   # Search and replace
│       ├── grep.py             # File content search
│       ├── todo.py             # Task list management
│       └── prompts/            # Tool-specific prompt files
│
├── autocompletion/             # Autocompletion system
│   ├── __init__.py
│   ├── completers.py           # Completer base classes
│   ├── fuzzy.py                # Fuzzy matching
│   ├── path_prompt.py          # Path completion
│   ├── path_prompt_adapter.py  # Adapter for prompt_toolkit
│   └── file_indexer/
│       ├── __init__.py
│       ├── indexer.py          # File indexing
│       ├── store.py            # Index storage
│       ├── watcher.py          # File system watcher
│       └── ignore_rules.py     # Gitignore-style rules
│
└── prompts/                    # System prompt templates
    └── __init__.py             # SystemPrompt enum, prompt loading
```

---

## Dependency Graph

```
                                    ┌──────────────────┐
                                    │  programmatic.py │
                                    │  (Public API)    │
                                    └────────┬─────────┘
                                             │
                                             ▼
                         ┌───────────────────────────────────────┐
                         │              agent.py                  │
                         │         (Core Orchestrator)            │
                         └───┬───────┬───────┬───────┬───────┬───┘
                             │       │       │       │       │
            ┌────────────────┘       │       │       │       └────────────────┐
            │                        │       │       │                        │
            ▼                        ▼       │       ▼                        ▼
    ┌───────────────┐        ┌──────────┐   │   ┌──────────┐        ┌───────────────┐
    │   config.py   │        │ types.py │   │   │ modes.py │        │ interaction_  │
    │               │        │          │   │   │          │        │   logger.py   │
    └───────┬───────┘        └────┬─────┘   │   └──────────┘        └───────────────┘
            │                     │         │
            │                     │         │
            ▼                     │         ▼
    ┌───────────────┐             │   ┌────────────────┐
    │    paths/     │             │   │ middleware.py  │
    │               │             │   │                │
    └───────────────┘             │   └────────────────┘
                                  │
                   ┌──────────────┼──────────────┐
                   │              │              │
                   ▼              ▼              ▼
           ┌─────────────┐ ┌───────────┐ ┌──────────────────┐
           │   tools/    │ │   llm/    │ │  system_prompt   │
           │             │ │           │ │      .py         │
           │ ┌─────────┐ │ │┌────────┐ │ └──────────────────┘
           │ │ base.py │ │ ││types.py│ │
           │ │manager  │ │ ││format  │ │
           │ │mcp.py   │ │ ││backend/│ │
           │ │builtins/│ │ │└────────┘ │
           │ └─────────┘ │ └───────────┘
           └─────────────┘
```

---

## Key Patterns

### 1. Event-Driven Architecture
The `Agent.act()` method returns an `AsyncGenerator[BaseEvent]`, allowing clients to react to each step of the conversation (assistant messages, tool calls, tool results, compaction events).

### 2. Middleware Pipeline
Non-invasive control flow management through a pipeline of middleware that can:
- Stop the conversation
- Inject messages
- Trigger context compaction
- Enforce limits (turns, price)

### 3. Lazy Tool Instantiation
Tools are only instantiated when first accessed via `ToolManager.get()`, reducing memory footprint and enabling dynamic tool discovery.

### 4. Typed Tool System
`BaseTool` is parameterized with generic types for args, result, config, and state. This enables:
- Automatic Pydantic validation
- JSON schema generation for LLM consumption
- Type-safe tool development

### 5. Protocol-Based Backend
The `BackendLike` protocol allows any LLM backend implementation, making it easy to add new providers without modifying core code.

### 6. Session Persistence
`InteractionLogger` saves complete interaction history as JSON, enabling session resume and debugging.

---

*This architecture documentation was generated from analysis of the vibe/core module.*
