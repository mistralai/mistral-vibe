# Mistral Vibe

[![PyPI Version](https://img.shields.io/pypi/v/mistral-vibe)](https://pypi.org/project/mistral-vibe)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/release/python-3120/)
[![CI Status](https://github.com/mistralai/mistral-vibe/actions/workflows/ci.yml/badge.svg)](https://github.com/mistralai/mistral-vibe/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/mistralai/mistral-vibe)](https://github.com/mistralai/mistral-vibe/blob/main/LICENSE)

```
██████████████████░░
██████████████████░░
████  ██████  ████░░
████    ██    ████░░
████          ████░░
████  ██  ██  ████░░
██      ██      ██░░
██████████████████░░
██████████████████░░
```

**Mistral's open-source CLI coding assistant.**

Mistral Vibe is a command-line coding assistant powered by Mistral's models. It provides a conversational interface to your codebase, allowing you to use natural language to explore, modify, and interact with your projects through a powerful set of tools.

> [!WARNING]
> Mistral Vibe works on Windows, but we officially support and target UNIX environments.

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

## Table of Contents

- [Features](#features)
  - [Built-in Agents](#built-in-agents)
  - [Subagents and Task Delegation](#subagents-and-task-delegation)
  - [Interactive User Questions](#interactive-user-questions)
- [Terminal Requirements](#terminal-requirements)
- [Quick Start](#quick-start)
- [Usage](#usage)
  - [Interactive Mode](#interactive-mode)
  - [Trust Folder System](#trust-folder-system)
  - [Programmatic Mode](#programmatic-mode)
  - [TUI Implementations](#tui-implementations)
- [Voice Mode](#voice-mode)
- [Slash Commands](#slash-commands)
  - [Built-in Slash Commands](#built-in-slash-commands)
  - [Custom Slash Commands via Skills](#custom-slash-commands-via-skills)
- [Skills System](#skills-system)
  - [Creating Skills](#creating-skills)
  - [Skill Discovery](#skill-discovery)
  - [Managing Skills](#managing-skills)
- [Configuration](#configuration)
  - [Configuration File Location](#configuration-file-location)
  - [API Key Configuration](#api-key-configuration)
  - [OpenTelemetry Tracing](#opentelemetry-tracing)
  - [Custom System Prompts](#custom-system-prompts)
  - [Custom Agent Configurations](#custom-agent-configurations)
  - [Tool Management](#tool-management)
  - [MCP Server Configuration](#mcp-server-configuration)
  - [Session Management](#session-management)
  - [Update Settings](#update-settings)
  - [Custom Vibe Home Directory](#custom-vibe-home-directory)
- [Editors/IDEs](#editorsides)
- [Resources](#resources)
- [Data collection & usage](#data-collection--usage)
- [License](#license)

## Features

- **Interactive Chat**: A conversational AI agent that understands your requests and breaks down complex tasks.
- **Powerful Toolset**: A suite of tools for file manipulation, code searching, version control, and command execution, right from the chat prompt.
  - Read, write, and patch files (`read`, `write_file`, `edit`).
  - Execute shell commands, with managed shell sessions, polling, and stdin helpers available during rollout.
  - Recursively search code with `grep` (with `ripgrep` support).
  - Manage a `todo` list to track the agent's work.
  - Ask interactive questions to gather user input (`ask_user_question`).
  - Delegate tasks to subagents for parallel work (`task`).
- **Project-Aware Context**: Vibe automatically provides your project's location, Git branch, and recent commit history to the agent, improving its understanding of your codebase.
- **Advanced CLI Experience**: Built with modern libraries for a smooth and efficient workflow.
  - Autocompletion for slash commands (`/`) and file paths (`@`).
  - Image attachments via `@` mentions — `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` files are sent to vision-capable models (e.g. Mistral Medium 3.5) as native multimodal content.
  - Persistent command history.
  - Beautiful Themes.
- **Highly Configurable**: Customize models, providers, tool permissions, and UI preferences through a simple `config.toml` file.
- **Safety First**: Features tool execution approval.
- **Multiple Built-in Agents**: Choose from different agent profiles tailored for specific workflows.

### Built-in Agents

Vibe comes with several built-in agent profiles, each designed for different use cases:

- **`ask`**: Requires approval for tool executions.
- **`plan`**: Read-only agent for exploration and planning. Auto-approves safe tools like `grep` and `read`.
- **`accept-edits`**: The default agent. Auto-approves file edits only (`write_file`, `edit`). Useful for code refactoring.
- **`auto-approve`**: Auto-approves all tool executions. Use with caution.

Use the `--agent` flag to select a different agent:

```bash
vibe --agent plan
```

To change the default agent used when `--agent` is not passed, set
`default_agent` in your `config.toml`:

```toml
default_agent = "plan"
```

Valid values are `ask`, `plan`, `accept-edits`, `auto-approve`,
`lean` (only when listed in `installed_agents`), or the name of any
custom agent file in `~/.vibe/agents/` or the project's `.vibe/agents/`
directory. Subagents such as `explore` are not accepted.

> Note: `default_agent` applies in both interactive and programmatic
> (`-p` / `--prompt`) sessions. Pass `--auto-approve` or `--yolo` with any
> agent when a run should approve all tool calls without prompting.

### Subagents and Task Delegation

Vibe supports subagents for delegating tasks. Subagents run independently and can perform specialized work without user interaction, preventing the context from being overloaded.

The `task` tool allows the agent to delegate work to subagents:

```
> Can you explore the codebase structure while I work on something else?

🤖 I'll use the task tool to delegate this to the explore subagent.

> task(task="Analyze the project structure and architecture", agent="explore")
```

Create custom subagents by adding `agent_type = "subagent"` to your agent configuration. Vibe comes with a built-in subagent called `explore`, a read-only subagent for codebase exploration and skill loading used internally for delegation.

When the Unified Harness is active, the interactive prompt shows each known
subagent's name, type, live status, and latest measured context size. Opening a
subagent also shows its active status above the prompt and its context use in the
bottom-right gauge. With an empty or locally edited prompt, press Down from its
last line to focus the list;
unsent text stays in the prompt. Use Up and Down to highlight a row, then press
Enter to open it; with the mouse, hover to highlight and click to open. Select **Main
conversation** to return, or press Escape from a subagent view. Ctrl+C adds a
local-only, error-styled user message explaining that subagents cannot be
controlled directly; return to Main and ask the main agent to stop one. Press
Ctrl+C again to quit Vibe. After
opening Main conversation, press Up from its row to focus the prompt. While a
subagent view is open, Up stops at the Main row. Idle subagents remain listed as
**ready** because the main agent can send them more instructions. Explicitly
stopped subagents leave the list after you return to Main. The first subagent
update does not add a local information message. When a subagent becomes ready,
its transcript shows a local information message explaining how to give it a
new goal or stop it from Main. Set `show_subagent_status_list = false` in
`config.toml`, or change it through `/config`, to hide this UI.

### Interactive User Questions

The `ask_user_question` tool allows the agent to ask you clarifying questions during its work. This enables more interactive and collaborative workflows.

```
> Can you help me refactor this function?

🤖 I need to understand your requirements better before proceeding.

> ask_user_question(questions=[{
    "question": "What's the main goal of this refactoring?",
    "options": [
        {"label": "Performance", "description": "Make it run faster"},
        {"label": "Readability", "description": "Make it easier to understand"},
        {"label": "Maintainability", "description": "Make it easier to modify"}
    ]
}])
```

The agent can ask multiple questions at once, displayed as tabs. Each question supports 2-4 options plus an automatic "Other" option for free text responses.

## Terminal Requirements

Vibe's interactive interface requires a modern terminal emulator. Recommended terminal emulators include:

- **WezTerm** (cross-platform)
- **Alacritty** (cross-platform)
- **Ghostty** (Linux and macOS)
- **Kitty** (Linux and macOS)

Most modern terminals should work, but older or minimal terminal emulators may have display issues.

## Quick Start

1. Navigate to your project's root directory:

   ```bash
   cd /path/to/your/project
   ```

2. Run Vibe:

   ```bash
   vibe
   ```

3. If this is your first time running Vibe, it will:
   - Use built-in defaults without creating a configuration file until you
     save a setting
   - Prompt you to enter your API key if it's not already configured
   - Save your API key to `~/.vibe/.env` for future use

   Alternatively, you can configure your API key separately using `vibe --setup`.

4. Start interacting with the agent!

   ```
   > Can you find all instances of the word "TODO" in the project?

   🤖 The user wants to find all instances of "TODO". The `grep` tool is perfect for this. I will use it to search the current directory.

   > grep(pattern="TODO", path=".")

   ... (grep tool output) ...

   🤖 I found the following "TODO" comments in your project.
   ```

## Usage

### Interactive Mode

Simply run `vibe` to enter the interactive chat loop.

- **Multi-line Input**: Press `Ctrl+J` or `Shift+Enter` for select terminals to insert a newline.
- **Input Cursor**: The main input cursor blinks by default. Set `cursor_blink = false` in `config.toml` for a steady cursor. Changes apply without restarting through `/config` or `/reload` in both Python and Rust terminals.
- **File Paths**: Reference files in your prompt using the `@` symbol for smart autocompletion (e.g., `> Read the file @src/agent.py`). A bare `@` quickly lists immediate non-hidden entries; after a path character, Git workspaces suggest tracked and non-ignored files. Pasting a standalone existing absolute or home-relative file or folder also creates a mention.
- **Shell Commands**: Prefix any command with `!` to execute it directly in your shell, bypassing the agent (e.g., `> !ls -l`).
- **External Editor**: Press `Ctrl+G` to edit your current input in an external editor.
- **Tool Output Toggle**: Press `Ctrl+O` to toggle the tool output view.
- **Todo View Toggle**: Press `Ctrl+T` to toggle the todo list view.
- **Debug Console**: Press `Ctrl+\` to toggle the debug console.
- **Agent Selection**: Press `Shift+Tab` to cycle through agents (ask, plan, ...).
- **Queueing**: Prompts submitted while the agent is working are queued by the app server. Empty `Enter` or `Ctrl+Enter` steers the queued prompts into the active turn; on Unified Harness sessions, this atomically consumes the stored queue item. `Ctrl+C` removes the newest queued prompt. `Escape` interrupts the active turn and pauses remaining prompts, and `Enter` resumes a paused queue. Shell commands and non-side-channel slash commands require an idle session.
- **Exit**: Type `/exit`, `exit`, `quit`, `:q`, or `:quit` in the input box, or press `Ctrl+C` / `Ctrl+D` twice within ~1 second. Set `ask_confirmation_on_exit = false` (or toggle it in `/config`) to make `Ctrl+D` quit on the first press; `Ctrl+C` always requires confirmation.

### Copying & Text Selection

- **Copy**: Use `Ctrl+Y` or `Ctrl+Shift+C` to copy the current selection to clipboard. With autocopy enabled (default via `autocopy_to_clipboard = true`), mouse selection automatically copies on release and shows a brief confirmation.
- **Multi-click selection**: Double-click selects a word, triple-click selects the paragraph. Dragging extends the selection at the same granularity.

You can start Vibe with a prompt using the following command:

```bash
vibe "Refactor the main function in cli/main.py to be more modular."
```

### Trust Folder System

Vibe includes a trust folder system to ensure you only run the agent in directories you trust. When you first run Vibe in a new directory which contains a `.vibe` subfolder, it may ask you to confirm whether you trust the folder.

Trusted folders are remembered for future sessions. You can manage trusted folders through its configuration file `~/.vibe/trusted_folders.toml`. In the trust prompt, use Up/Down or the mouse wheel to scroll the detected-file list. Text is selectable there too: double-click selects a word, triple-click selects a paragraph, and dragging near the list edges scrolls while extending the selection.

This safety feature helps prevent accidental execution in sensitive directories.

### Programmatic Mode

You can run Vibe non-interactively by piping input or using the `--prompt` flag. This is useful for scripting.

```bash
vibe --prompt "Refactor the main function in cli/main.py to be more modular."
```

By default, it uses your configured `default_agent` (`accept-edits` unless changed).
To approve all tool calls without prompting, pass `--auto-approve` or `--yolo`
(also available for interactive sessions):

```bash
vibe --prompt "Refactor the main function in cli/main.py to be more modular." --auto-approve
```

#### Programmatic Mode Options

When using `--prompt`, you can specify additional options:

- **`--max-turns N`**: Limit the maximum number of assistant turns. The session will stop after N turns.
- **`--max-price DOLLARS`**: Set a maximum cost limit in dollars for this run. The turn is interrupted once the cost exceeds this limit, and the outcome is `price_limit`.
- **`--max-tokens N`**: Set a maximum cumulative LLM token budget for this run, counting both prompt and completion tokens. The turn is interrupted once usage exceeds this limit, and the outcome is `token_limit`.
- **`--agent NAME`**: Select the agent profile for this run.
- **`--auto-approve`, `--yolo`**: Approves all tool calls without prompting, including in interactive sessions. Can be combined with any `--agent` value.
- **`--enabled-tools TOOL`**: Enable specific tools. In programmatic mode, this disables all other tools. Can be specified multiple times. Supports exact names, glob patterns (e.g., `bash*`), or regex with `re:` prefix (e.g., `re:^serena_.*$`).
- **`--disabled-tools TOOL`**: Disable specific tools after `--enabled-tools` filtering. Can be specified multiple times. Supports exact names, glob patterns (e.g., `bash*`), or regex with `re:` prefix (e.g., `re:^serena_.*$`).
- **`--output FORMAT`**: Set the output format. Options:
  - `text` (default): Human-readable text output
  - `json`: All messages as JSON at the end
  - `streaming`: Newline-delimited JSON per message

  On a resumed session, `text` and `streaming` cover only the new run, so a run that ends without a reply of its own prints none; `json` holds the whole session.
- **`--prompt-file PATH`**: Read the prompt from a file instead of `--prompt`. With `-p` and no text, the prompt is read from stdin.
- **`--time-limit SECONDS`**: Stop the run after this many seconds. The active turn and its tool processes are stopped, and the session can be resumed with `--resume`. SIGTERM stops a run the same way.
- **`--output-dir DIR`**: Write `DIR/export.json` and copy the session journal to `DIR/session/`, whichever way the run ends. The export records the Vibe version, the effective config (header and environment values redacted), token usage, cost, the number of model calls the agent made (`steps`), the stop reason and the outcome. When the session had config issues, such as a skill or a config file it could not load, the export lists them under `warnings`, each as `{"message": str, "source": str | null}`, where `source` names the file or skill it is about; the field is left out when there are none, and the run goes on regardless. Usage, cost and `steps` count this run only, also on a resumed session, while the copied journal holds the whole session. Only an argument the parser itself rejects (an unknown flag, a malformed value) exits 1 without an export.

Example:

```bash
vibe --prompt "Analyze the codebase" --max-turns 5 --max-price 1.0 --max-tokens 50000 --output json
vibe --prompt-file task.md --auto-approve --time-limit 1800 --output-dir out/
```

#### Exit Codes

| Code | Outcome in `export.json` | Meaning |
| --- | --- | --- |
| `0` | `finished` | The agent finished. |
| `1` | `usage_error`, `config_error` | The run could not start as asked. |
| `2` | `infrastructure_failure` | The model API (after retries) or the runtime failed. |
| `3` | `turn_limit`, `token_limit`, `price_limit`, `deadline`, `terminated`, `length`, `refusal` | The agent stopped without finishing: a limit or a model refusal; score what it left. `length` means the conversation or the last answer hit a model token limit: the context window, or the output-token cap (`max_output_tokens` on the model). |
| `4` | `aborted` | The `--agent-socket` server aborted the run (experimental, see below); `error.message` in `export.json` is the server's message. |

A model refusal ends the run as `refusal`, exit `3`; `error` in `export.json` has the code `refusal`, and its message gives the refusal's category and explanation when the model sends them. Only the legacy runtime (`--legacy-harness`) reports a refusal as one.

#### Agent Socket (`--agent-socket`, experimental)

Experimental: the option is hidden from `--help`, and it and the protocol below
may change or go away without notice.

`vibe -p --agent-socket PATH` runs the same session as `vibe -p` on the host,
with the same export, exit codes, limits and SIGTERM handling, connected to an
agent server on the Unix socket `PATH`. The server serves two independent
things, either or both:

- **A sandbox.** The session's file and shell tools run in it instead of on
  this host, and the session starts in the sandbox's workspace (see "Sandboxed
  sessions" below).
- **Tools.** The server declares them in its handshake, and the session offers
  them to the model next to Vibe's own; each call goes back to the server. A
  tool's `modelAccess` decides whether the model calls it directly, from
  `run_typescript` as `tools.<namespace>.<name>`, or both. `--enabled-tools` and
  `--disabled-tools` filter only Vibe's own tools. Without a sandbox, the
  session runs on this host as plain `vibe -p` does.

The option needs `-p` or `--prompt-file`, and cannot be combined with
`--workdir`, `--worktree`, `--add-dir`, `--continue` or `--teleport`; those are
usage errors (exit 1). It is not supported on Windows (exit 1).

To continue a finished run with a new prompt, start a new run with
`--resume SESSION_ID`, taking the ID from `session_id` in the first run's
`export.json`. The new run redoes the handshake, so the server may be a new one
and declares its tools again; the model sees the whole earlier conversation
followed by the new prompt. Every limit (`--max-turns`, `--time-limit`,
`--max-tokens`, `--max-price`) counts from the new run's start. Its export, under
the same `session_id`, counts only its own `steps`, `usage` and `cost_usd`; its
`journal_dir` holds the whole session, earlier runs included.

##### Sandboxed sessions

When the server serves a sandbox, the workspace context (git metadata and
`AGENTS.md` files) is read from the sandbox. Session storage, model calls, MCP
servers and hooks stay on this host. Background processes run in the sandbox,
which must be POSIX for them, as `enable_background_processes` says. Rewinding
the session restores no files, since the files are in the sandbox. If the
sandbox fails, the run ends as `infrastructure_failure`.

Vibe's tool helper can crash in a sandbox that works: it exits part way through
a tool call without answering, say from a segmentation fault or an
out-of-memory kill. The model then reads a tool error saying the command may or
may not have run, with the exit code and what the helper printed, and the turn
goes on. The third crash in a row ends the run as `infrastructure_failure`; a
tool call the helper answers, even with an error, resets the count. Sessions
sharing the sandbox, such as subagents, share the count. A helper that cannot
start at all (no interpreter at `python`) ends the run at once, as does a crash
outside the model's tool calls, such as while copying skills into the sandbox
or saving a large output there.

As on the host, when the model reads a file, the `AGENTS.md` files in the
directories between it and the workspace are added to what it reads, once per
session. Looking for them costs a `sandbox/readFile` request per directory, so
a session looks in each directory once. When Vibe's own `write_file` or
`search_replace` writes an `AGENTS.md`, its directory is looked in again on the
next read below it; a `bash` command whose text names `AGENTS.md` makes every
directory be looked in again. An `AGENTS.md` that appears any other way, for
example from a script or `git checkout`, in a directory already looked in is
not seen for the rest of the session.

File tools follow symlinks in the sandbox, as on the host. Approvals, though,
match a path as written, since this host cannot see the sandbox's links. So
without `--auto-approve`, approving a link also allows its target, and a link
in the workspace that points outside it counts as a workspace path, for example
for `accept-edits`. The target is still in the sandbox. Runs with
`--auto-approve`, such as evaluation runs, ask for no approval and are not
affected.

A tool output too large to hand the model whole is kept with the session on
this host, as with plain `vibe -p`, and also saved in an owner-only directory
under the sandbox's temporary directory. The model is given that sandbox path,
so it reads the output back with the sandboxed tools.

Only the user's Vibe files are loaded on this host, and `--trust` has no
effect. Of the project's own files, only the `AGENTS.md` files and the skills
(`.vibe/skills` and `.agents/skills`) are read from the sandbox. The rest of the
project's `.vibe/` directory (config, agents, tools, hooks, plugins and
prompts) is ignored, whereas `vibe -p --trust` loads it: loading it would run
code the sandbox controls on this host, such as hooks, MCP servers and tools.
A run whose sandbox workspace has it logs a warning.

The model reads skills with the sandboxed tools, so the skills read on this
host (the user's, and the plugins', the built-in ones included) are copied into
the sandbox's temporary directory under `mistralai-vibe-skills/<digest>/`,
outside the workspace, and the model is given those paths. A copy is named by
the digest of its files, checked before it is reused, and uploaded only when
missing, so later runs on the same sandbox reuse it. A skill with more than 200
files, a file over 256 KiB, more than 1 MiB of files besides `SKILL.md`, or a
`SKILL.md` over 2 MiB is not copied, and the run goes without it. Each skill
left out, for that or because the copy failed, is logged and listed in the
session's config issues, and so in the export's `warnings`.

##### Protocol

The caller creates the socket with mode `0600` inside a `0700` directory, and
serves it for the whole run. A Unix socket path is limited to 104 bytes on macOS
(108 on Linux), so keep it short, for example under `/tmp`.

For each request, Vibe opens a new connection, writes one line, reads one line
and closes the connection; concurrent requests use concurrent connections.
Answer them concurrently too: a request's timeout covers the time it waits
behind others, such as a background process's poll behind a long shell
command. A line is a compact JSON-RPC 2.0 message with camelCase fields, followed by `\n`.
Replies may carry fields Vibe does not know, which it ignores.

| Method | Params | Result |
| --- | --- | --- |
| `agent/initialize` | `{"protocolVersion": 2, "vibeVersion": str}` | `{"protocolVersion": 2, "workspace"?: str, "python"?: str, "tools"?: [ToolDefinition], "toolTimeoutSeconds"?: float, "instructions"?: str}` |
| `sandbox/execute` | `{"command": str, "cwd": str, "timeout": float \| null}` | `{"exitCode": int, "stdout": str, "stderr": str}` |
| `sandbox/readFile` | `{"path": str, "maxBytes": int}` | `{"contentBase64": str \| null}` |
| `tools/call` | `{"callbackId": str, "name": str, "input": any, "toolCallId": str}` | `{"output"?: any, "annotations"?: object, "error"?: {"message": str, "code"?: str, "details"?: any}}` |

- `agent/initialize` is called once, before the session starts, and must
  answer within 60 seconds. The server must
  answer with protocol version 2. `workspace` and `python` come together, or
  not at all when the server serves no sandbox: `workspace` is the absolute
  sandbox path the session starts in, and `python` the interpreter that runs
  Vibe's tool helper (Python 3.9 or later). Each tool is
  `{"namespace"?: str, "name": str, "description"?: str, "inputSchema"?: object, "outputSchema"?: object, "modelAccess"?: "direct" | "programmatic" | "both"}`;
  `namespace` defaults to `client` and `modelAccess` to `programmatic`. A
  namespace cannot be `vibe` or `ui`, nor the name of an MCP server the session
  has, and a `<namespace>.<name>` is declared once. `toolTimeoutSeconds`, a
  positive number, is how long a `tools/call` may take; it defaults to 600.
  `instructions` are project instructions for the session. The system prompt
  carries them where it carries the project's `AGENTS.md` files, under the
  same heading and with the same weight, followed by any `AGENTS.md` found in
  the workspace. They are left out, like the `AGENTS.md` files, when
  `include_project_context` is off. Without a socket, the same text goes in
  an `AGENTS.md` at the root of the workspace, which `vibe -p` reads when the
  directory is trusted (`--trust`).
  If the handshake fails, the run ends as `infrastructure_failure` (exit 2);
  if it declares a tool Vibe cannot offer, as `usage_error` (exit 1), before
  the model is called.
- `sandbox/execute` runs `command` with `sh` in `cwd`, with the sandbox's own
  environment. Vibe runs its tool helper with `cwd` `/` and passes the
  session's working directory in the command itself, so a model that deletes
  or moves that directory gets a tool error, as on the host. A non-zero exit
  is a result, not an error. A command that
  outlives `timeout` seconds is answered with the error code `-32001`: the
  model reads it as a timed-out command, as on the host, and the turn goes
  on. A server that stops a command sooner, at a limit of its own, says so in
  the error's optional `data`, as `{"timeoutSeconds": float}`, and the model
  is told that limit; without it, the model is told no limit. To stop a
  command, the server kills the process it started (swerex kills its process
  group); the processes the command started go with it. Vibe waits 30
  seconds past `timeout` for the reply; if none comes, it closes the
  connection and the sandbox has failed.
- `sandbox/readFile` returns at most `maxBytes` of a regular file, base64
  encoded, or `null` when there is no such file.
- `tools/call` runs one call of a declared tool. `callbackId` is unique to the
  call, `name` is `<namespace>.<name>`, and `toolCallId` the model's call it
  answers. `output`
  is the tool's result, and `error` makes the call a tool error. Either way the
  model reads it and the turn goes on. So does an error reply, a malformed
  reply, a lost connection, or no reply within `toolTimeoutSeconds`: each is a
  tool error. A shorter `--time-limit` shortens the wait to match.

For a sandbox request, any other error reply, a malformed reply, or a
connection that closes or cannot be opened means the sandbox failed: the turn
fails and the run ends as `infrastructure_failure` (exit 2). A tool helper
command that exits non-zero is a crash of the helper, not of the sandbox; see
[Sandboxed sessions](#sandboxed-sessions).

The server can end the run itself, from any request, by answering it with the
error code `-32002` and a message: `{"code": -32002, "message": str}`. Vibe
stops the run as SIGTERM does and exits 4, with the outcome `aborted` and the
server's message as `error.message` in `export.json`. Vibe gives the abort no
meaning of its own; the message is for whoever reads the export.

### TUI Implementations

The `vibe` command starts through a small launcher that picks the terminal
client from the `VIBE_CLI` environment variable:

- `VIBE_CLI=rust vibe` starts the Rust TUI.
- `VIBE_CLI=python vibe` runs the legacy Python (Textual) TUI.
- With `VIBE_CLI` unset (or set to any other value), `vibe` runs the legacy
  Python TUI.

The commands above use POSIX shell syntax, where the assignment only applies
to that command. On Windows, set the variable for the session instead:

- PowerShell: `$env:VIBE_CLI = "python"; vibe` (unset with
  `Remove-Item Env:VIBE_CLI`)
- cmd: `set VIBE_CLI=python`, then run `vibe` (unset with `set VIBE_CLI=`)

When the `VIBE_CLI` environment variable is set to `rust`, every `vibe`
invocation selects the Rust client. To run a nested command (e.g.
`vibe mcp add ...`) with the legacy Python client instead, set `VIBE_CLI` to
`python` for that invocation.

## Voice Mode

> [!WARNING]
> Voice mode is experimental and may change in future releases.

Voice mode allows you to dictate input using your microphone instead of typing.

### Activating Voice Mode

Toggle voice mode on or off with the `/voice` slash command:

```
> /voice
```

### Recording Shortcuts

| Shortcut | Action           |
| -------- | ---------------- |
| `Ctrl+R` | Start recording  |
| Any key  | Stop recording   |
| `Escape` | Cancel recording |
| `Ctrl+C` | Cancel recording |

## Slash Commands

Use slash commands for meta-actions and configuration changes during a session.

### Built-in Slash Commands

Vibe provides several built-in slash commands. Use slash commands by typing them in the input box:

```
> /help
```

If a model response is interrupted by a backend error, use `/retry` to continue
from the partial response. Add optional guidance after the command, for example
`/retry keep the conclusion concise`.

Use `/mcp` or `/connectors` to browse configured MCP servers and workspace
connectors. The browser starts on the first item; press Up or Left to focus its
fuzzy search bar, then Up again to wrap to the last item.

With the Unified Harness, `/loop every two minutes check the build` asks the
model to schedule a recurring prompt. Calendar requests such as `/loop weekdays
at 9am review CI` work too, using the machine's local timezone. Ask the model to
list or cancel schedules; `/loop` has no TUI management subcommands. Requests
submitted while busy join the normal prompt queue. Schedules survive resume,
but only run while Vibe is open and idle; missed runs do not accumulate.
The `cron` tool is enabled and allowed by default; explicit tool filters and
`tools.cron.permission` settings still apply.

### Custom Slash Commands via Skills

You can define your own slash commands through the skills system. Skills are reusable components that extend Vibe's functionality.

To create a custom slash command:

1. Create a skill directory with a `SKILL.md` file
2. Set `user-invocable = true` in the skill metadata
3. Define the command logic in your skill

Example skill metadata:

```markdown
---
name: my-skill
description: My custom skill with slash commands
user-invocable: true
---
```

Custom slash commands appear in the autocompletion menu alongside built-in commands.

## Skills System

Vibe's skills system allows you to extend functionality through reusable components. Skills can add new tools, slash commands, and specialized behaviors.

Vibe follows the [Agent Skills specification](https://agentskills.io/specification) for skill format and structure.

### Creating Skills

Skills are defined in directories with a `SKILL.md` file containing metadata in YAML frontmatter. For example, `~/.vibe/skills/code-review/SKILL.md`:

```markdown
---
name: code-review
description: Perform automated code reviews
license: MIT
compatibility: Python 3.12+
user-invocable: true
allowed-tools:
  - read
  - grep
  - ask_user_question
---

# Code Review Skill

This skill helps analyze code quality and suggest improvements.
```

By default, both the user and the model can invoke a skill. Invocation controls
are independent:

- `user-invocable: false` hides the skill from the `/` menu and prevents direct
  `/skill-name` invocation while still allowing the model to load it.
- `disable-model-invocation: true` follows the Claude Code convention and makes
  the skill explicit-only: users can still invoke `/skill-name`, but the model
  does not see or invoke it automatically.
- Skills using OpenAI's `agents/openai.yaml` convention can express the same
  explicit-only behavior. Vibe applies this policy regardless of active model or provider:

  ```yaml
  policy:
    allow_implicit_invocation: false
  ```

  If the policy metadata is malformed or contains an unknown policy field,
  Vibe reports the issue and keeps the skill explicit-only.

### Skill Discovery

Vibe discovers skills from multiple locations:

1. **Custom paths**: Configured in `config.toml` via `skill_paths`
2. **Standard Agent Skills path** (project root, trusted folders only): `.agents/skills/` — [Agent Skills](https://agentskills.io) standard
3. **Local project skills** (project root, trusted folders only): `.vibe/skills/` in your project
4. **Global skills directories**: `~/.vibe/skills/` and `~/.agents/skills/`

```toml
skill_paths = ["/path/to/custom/skills"]
```

### Managing Skills

Enable or disable skills using patterns in your configuration:

```toml
# Enable specific skills
enabled_skills = ["code-review", "test-*"]

# Disable specific skills
disabled_skills = ["experimental-*"]
```

Skills support the same pattern matching as tools (exact names, glob patterns, and regex).

## Configuration

### Configuration File Location

Vibe is configured via a `config.toml` file. It looks for this file first in `./.vibe/config.toml` and then falls back to `~/.vibe/config.toml`.

### Theme

The default `auto` theme follows the terminal background when it can be detected, then the operating-system light/dark preference. Choose another theme with `/theme` or set it explicitly:

```toml
theme = "dracula"
```

### API Key Configuration

To use Vibe, you'll need a Mistral API key. You can obtain one by signing up at [https://console.mistral.ai](https://console.mistral.ai).

You can configure your API key using `vibe --setup`, or through one of the methods below.

Vibe supports multiple ways to configure your API keys:

1. **Interactive Setup (Recommended for first-time users)**: When you run Vibe for the first time or if your API key is missing, Vibe will prompt you to enter it. The key will be securely saved to `~/.vibe/.env` for future sessions.

2. **Environment Variables**: Set your API key as an environment variable:

   ```bash
   export MISTRAL_API_KEY="your_mistral_api_key"
   ```

3. **`.env` File**: Create a `.env` file in `~/.vibe/` and add your API keys:

   ```bash
   MISTRAL_API_KEY=your_mistral_api_key
   ```

   Vibe automatically loads API keys from `~/.vibe/.env` on startup. Environment variables take precedence over the `.env` file if both are set.

**Note**: The `.env` file is specifically for API keys and other provider credentials. General Vibe configuration should be done in `config.toml`.

### Custom Domains

If you use a Mistral-compatible deployment instead of the default `console.mistral.ai` / `api.mistral.ai`, you can point browser sign-in at it. The credential is still a Mistral API key.

Run `vibe --setup`, choose **Launch browser** then **Other**, enter your login domain, and sign in through the browser. A bare domain is prefixed with `https://`, and the auth API base is derived as `DOMAIN/api`. The overridden `mistral` provider is saved to your user config so subsequent runs reuse it.

**Note**: the wizard reads any custom `browser_auth_base_url` already set in `config.toml`. Choosing **Other** pre-fills that configured domain so you can confirm or edit it. Choosing **Mistral AI** while a custom domain is configured warns you first — press **Enter** again to confirm the reset to the default domain, which is then persisted.

### TLS and Corporate Certificate Authorities

By default, Vibe uses the bundled `certifi` certificate roots for outbound HTTPS requests. If your organization installs private certificate authorities in the operating system trust store, you can opt in to the system trust store in `config.toml`:

```toml
enable_system_trust_store = true
```

`SSL_CERT_FILE` and `SSL_CERT_DIR` are still supported and are loaded as additional trust anchors.

### OpenTelemetry Tracing

Vibe can export traces for agent, model, and tool operations over OTLP/HTTP. Enable tracing in `config.toml`:

```toml
enable_otel = true
```

By default, Vibe sends traces to the telemetry endpoint associated with the configured Mistral provider and authenticates with that provider's API key. `enable_telemetry` must also remain enabled.

To send traces to another collector, configure its base URL. Vibe appends `/v1/traces`; configure authentication with the standard `OTEL_EXPORTER_OTLP_*` environment variables when needed.

```toml
enable_otel = true
otel_endpoint = "https://collector.example.com:4318"
```

Span attributes are redacted on the client before export. The default mode redacts sensitive values, `strict` redacts sensitive attributes entirely, and `none` disables redaction:

```toml
otel_redaction = "default" # "default", "strict", or "none"
```

Use `none` only when the collector is trusted to receive potentially sensitive prompt, response, and tool data.

### Custom System Prompts

You can create `AGENTS.md` files to add custom instructions. You can also replace the entire system prompt.

Place `AGENTS.md` files in:
- `~/.vibe/AGENTS.md` — user-level instructions for all projects
- Project directories — project-specific instructions, loaded from cwd up to the trust root

Priority: closer directories override more distant ones. Instructions in `AGENTS.md` override the default system prompt. Files are only loaded for trusted folders.

Custom system prompts entirely replace the default one (`prompts/cli.md`). Create a markdown file in the `~/.vibe/prompts/` directory with your custom prompt content.

To use a custom system prompt, set the `system_prompt_id` in your configuration to match the filename (without the `.md` extension):

```toml
# Use a custom system prompt
system_prompt_id = "my_custom_prompt"
```

This will load the prompt from `~/.vibe/prompts/my_custom_prompt.md`.

Project-local prompts in `.vibe/prompts/` are also supported and override user-level prompts with the same name. This applies to all custom prompts (system, compaction, and title).

### Custom Compaction Prompts

Compaction uses the built-in prompt at `prompts/compact.md` by default. You can replace it with a custom prompt from `~/.vibe/prompts/` (or `.vibe/prompts/`) using the same resolution rules as system prompts.

To use a custom compaction prompt, set `compaction_prompt_id` in your configuration to match the filename (without the `.md` extension):

```toml
# Use a custom compaction prompt
compaction_prompt_id = "my_compaction_prompt"
```

Any extra instructions passed to `/compact ...` are appended after the configured compaction prompt.

Compaction keeps the same session and visible conversation. Later model requests
use the latest compacted context followed by newer messages.

### Custom Title Prompts

Vibe generates session titles in the background with the built-in prompt at `prompts/session_title.md`. You can replace it with a custom prompt from `~/.vibe/prompts/` (or `.vibe/prompts/`) using the same resolution rules as system prompts.

To use a custom title prompt, set `title_prompt_id` in your configuration to match the filename (without the `.md` extension):

```toml
# Use a custom title prompt
title_prompt_id = "my_title_prompt"
```

The configured prompt is the system prompt of the background call. Vibe still sends the session transcript as the user message, so the prompt only needs to describe how titles are written (length, language, style).

### Custom Agent Configurations

You can create custom agent configurations for specific use cases (e.g., red-teaming, specialized tasks) by adding agent-specific TOML files in the `~/.vibe/agents/` directory.

To use a custom agent, run Vibe with the `--agent` flag:

```bash
vibe --agent my_custom_agent
```

Vibe will look for a file named `my_custom_agent.toml` in the agents directory and apply its configuration.

Example custom agent configuration (`~/.vibe/agents/redteam.toml`):

```toml
# Custom agent configuration for red-teaming
active_model = "mistral-medium-3.5"
system_prompt_id = "redteam"

# Disable some tools for this agent
disabled_tools = ["edit", "write_file"]

# Override tool permissions for this agent
[tools.bash]
permission = "always"

[tools.read]
permission = "always"
```

Note: This implies that you have set up a redteam prompt named `~/.vibe/prompts/redteam.md`.

### Tool Management

The built-in shell surface is controlled by the `managed_shell_tools_enabled` config
field and the `vibe_cli_managed_shell_tools` GrowthBook experiment. The default variant
keeps the legacy one-shot `bash` tool, including its existing Windows behavior.
The managed variant exposes OS-native shell tools:
POSIX systems, including WSL where Vibe runs as Linux, get managed `bash`,
`bash_output`, `bash_stdin`, `bash_sessions`, and `bash_log_file`; native Windows
gets `git_bash`, `git_bash_output`, `git_bash_stdin`, `git_bash_sessions`, and
`git_bash_log_file` when Git Bash is available. If Git Bash is unavailable,
native Windows falls back to `powershell`, `powershell_output`,
`powershell_stdin`, `powershell_sessions`, and `powershell_log_file`.

Managed shell sessions return a `session_id`, inline output, a cursor for polling
more output, and a log path under `~/.vibe/shell-tool/sessions/`. Long-running
commands can be left alive with `background = true`, and interactive commands can
be driven with the matching stdin tool.

POSIX `bash` reads permissions, allowlists, and denylists from `[tools.bash]`.
Native Windows `git_bash` reads them from `[tools.git_bash]`; native Windows
`powershell` reads them from `[tools.powershell]`. Neither Windows tool reads
`[tools.bash]`. Git Bash is preferred when Vibe can resolve a usable `bash.exe`
from PATH, Git for Windows, or standard Git install locations. If Git Bash is
unavailable, the PowerShell resolution order is `pwsh.exe`, then
`powershell.exe`. `cmd.exe` is not used by the managed Windows shell tools.

```toml
[tools.git_bash]
permission = "ask"
shell = "C:\\Program Files\\Git\\bin\\bash.exe"

[tools.powershell]
permission = "ask"
shell = "powershell.exe"
```

The rollout assignment is server-managed and is not a `config.toml` option.

`enable_background_processes = false` takes background processes (`tools.process`
in `run_typescript`) away from the agent on the default runtime: its commands then
run to completion in the foreground, and the system prompt no longer describes
process tools. It defaults to `true`.

`enable_subagents = false` likewise takes subagents (`tools.subagent` in
`run_typescript`) away from the agent on the default runtime: the system prompt
no longer describes them, and the agent works alone. It defaults to `true`.

#### Enable/Disable Tools with Patterns

You can control which tools are active using `enabled_tools` and `disabled_tools`.
These fields support exact names, glob patterns, and regular expressions.
When both are set, `enabled_tools` first narrows the tool set, then
`disabled_tools` removes matching tools from that set.

Examples:

```toml
# Only enable tools that start with "serena_" (glob)
enabled_tools = ["serena_*"]

# Regex (prefix with re:) — matches full tool name (case-insensitive)
enabled_tools = ["re:^serena_.*$"]

# Disable a group with glob; everything else stays enabled
disabled_tools = ["mcp_*", "grep"]
```

Notes:

- MCP tool names use underscores, e.g., `serena_list` not `serena.list`.
- Regex patterns are matched against the full tool name using fullmatch.

### MCP Server Configuration

You can configure MCP (Model Context Protocol) servers to extend Vibe's capabilities. Add MCP server configurations under the `mcp_servers` section:

Remote MCP servers can be added non-interactively from the shell. Static auth
is selected when `--api-key-env` or `--header` is provided; otherwise the
server uses OAuth and starts browser login by default.

```bash
vibe mcp add mistralai \
  --url https://api.mistral.ai/mcp \
  --transport streamable-http \
  --api-key-env MISTRAL_API_KEY

vibe mcp add linear \
  --url https://mcp.linear.app/mcp

vibe mcp remove mistralai
```

Use `--no-login` to persist an OAuth server without starting login. Static auth
also supports repeatable `--header`, `--api-key-header`, `--api-key-format`,
`--startup-timeout-sec`, and `--tool-timeout-sec`. Run `vibe mcp add --help`
for the complete command reference. `vibe mcp remove <name>` removes the server
from the user configuration. Removing an OAuth server also deletes its stored
tokens, client information, and configuration fingerprint when available.

With `VIBE_CLI` set to `rust`, shell `mcp add` uses the OAuth-only
`/mcp add` syntax: `vibe mcp add https://mcp.linear.app/mcp --name linear
--no-login`. It accepts `--scope` (repeatable), `--transport`, and
`--allow-insecure-http`; without `--no-login`, it starts browser login. Both
`add` and `remove NAME` update the user configuration without opening a chat
session. For stdio or static-auth additions, set `VIBE_CLI` to `python` and run
`vibe mcp add` with the flags above.

Hosted OAuth MCP servers can also be added from inside Vibe:

```text
/mcp add https://mcp.linear.app/mcp
/mcp add https://mcp.example.com/mcp --name docs --scope read --transport http --no-login
```

`/mcp add` is OAuth-only. It writes `auth.type = "oauth"` with optional
scopes and starts login by default. It uses `transport = "streamable-http"`
unless you pass `--transport http`. Pass `--no-login` to add the server without
starting OAuth login. The shortcut supports `streamable-http` and `http`
transports.

```toml
# Example MCP server configurations
[[mcp_servers]]
name = "my_http_server"
transport = "http"
url = "http://localhost:8000"

[mcp_servers.auth]
type = "static"
headers = { "X-Client" = "vibe" }
api_key_env = "MY_API_KEY_ENV_VAR"
api_key_header = "Authorization"
api_key_format = "Bearer {token}"

[[mcp_servers]]
name = "my_streamable_server"
transport = "streamable-http"
url = "http://localhost:8001"

[mcp_servers.auth]
type = "static"
headers = { "X-Client" = "vibe" }

[[mcp_servers]]
name = "fetch_server"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]
env = { "DEBUG" = "1", "LOG_LEVEL" = "info" }
```

Supported transports:

- `http`: Standard HTTP transport
- `streamable-http`: HTTP transport with streaming support
- `stdio`: Standard input/output transport (for local processes)

Key fields:

- `name`: A short alias for the server (used in tool names)
- `transport`: The transport type
- `url`: Base URL for HTTP transports
- `headers`: Additional HTTP headers
- `api_key_env`: Environment variable containing the API key
- `command`: Command to run for stdio transport
- `args`: Additional arguments for stdio transport
- `startup_timeout_sec`: Timeout in seconds for the server to start and initialize (default 10s)
- `tool_timeout_sec`: Timeout in seconds for tool execution (default 60s)
- `env`: Environment variables to set for the MCP server of transport type stdio

HTTP MCP servers can use either static auth or OAuth. Both use an `auth` block;
legacy top-level `api_key_env` / `headers` keys are still accepted and promoted
to static auth when Vibe loads the configuration.

```toml
[[mcp_servers]]
name = "linear"
transport = "streamable-http"
url = "https://mcp.linear.app/mcp"

[mcp_servers.auth]
type = "oauth"
scopes = []
```

MCP tools are named using the pattern `{server_name}_{tool_name}` and can be configured with permissions like built-in tools:

```toml
# Configure permissions for specific MCP tools
[tools.fetch_server_get]
permission = "always"

[tools.my_http_server_query]
permission = "ask"
```

MCP server configurations support additional features:

- **Environment variables**: Set environment variables for MCP servers
- **Custom timeouts**: Configure startup and tool execution timeouts

Example with environment variables and timeouts:

```toml
[[mcp_servers]]
name = "my_server"
transport = "http"
url = "http://localhost:8000"
env = { "DEBUG" = "1", "LOG_LEVEL" = "info" }
startup_timeout_sec = 15
tool_timeout_sec = 120
```

### Hooks

Hooks wire arbitrary shell commands into Vibe's lifecycle to gate, audit, or rewrite agent behavior. No flag is required — declaring a hook is enough.

Declared in `<project>/.vibe/hooks.toml` (project, loaded first; trusted only) and `~/.vibe/hooks.toml` (user-global, loaded second; duplicates by `name` lose to the project entry):

```toml
[[hooks]]
name = "deny-rm-rf"
type = "pre_tool"
match = "bash"                       # tool-name matcher (fnmatch glob + `re:` regex escape, case-insensitive)
command = "uv run python /path/to/guard-bash"
timeout = 60.0                       # seconds; default 60 for all hooks
strict = false                       # tool hooks only: turn failures into denials (pre) / text-clears (post)
description = "Reject dangerous shell commands."
```

Subagents inherit the parent's hook config so policies apply transitively.

#### Common ground

Every hook is spawned with a JSON invocation on **stdin** (UTF-8) containing the session context: `session_id`, `parent_session_id`, `transcript_path`, `cwd`, plus `hook_event_name` discriminating the hook type. Tool hooks add tool-specific fields (below).

Every hook signals back via its **exit code** and **stdout**. The contract on stdout is strict: either empty (do nothing), or a JSON object matching the schema below. Use **stderr** for diagnostics / debug logs.

- **Exit `0`, empty stdout** — passthrough.
- **Exit `0`, valid JSON object on stdout** — structured response. Universal top-level fields:
  - `system_message` (string, optional) — shown to the user in the UI.
  - `decision` (`"allow"` | `"deny"`, optional, default `"allow"`) — the effect of `"deny"` depends on the hook type.
  - `reason` (string, optional) — accompanies `decision: "deny"`.
  - Event-specific payload under `hook_specific_output`.
- **Exit `0`, non-empty but non-conforming stdout** (free-form text, broken JSON, JSON scalar/array, schema mismatch) — treated as a hook failure with the parse error as the message. Warning by default; escalated to deny / clear under `strict = true` on a tool hook.
- **Any non-zero exit / timeout / spawn failure** — same failure path. Diagnostic taken from stderr (falling back to stdout, then the exit code).

Unknown JSON fields are tolerated at every level (forward-compatible). Fields that aren't meaningful for the current hook type are silently ignored.

#### `post_agent`

Fires after every assistant turn that ends without pending tool calls.

- **Receives** (in addition to the session context): no extra fields.
- **Can return**:
  - `decision: "deny"` + `reason` — `reason` is injected as a new user message asking for a retry. Capped at **3 retries per hook per user turn**; further denies become terminal warnings.
  - `system_message` — UI-only.

#### `pre_tool`

Fires per tool call, **before** the user permission prompt. First deny short-circuits remaining `pre_tool` hooks for that call.

- **Receives** (in addition to the session context): `tool_name`, `tool_call_id`, `tool_input` (the model's raw arguments).
- **Can return**:
  - `decision: "deny"` + `reason` — denies the tool call; `reason` becomes the tool error the LLM sees.
  - `hook_specific_output.tool_input` (object) — **full replacement** of the model's arguments. Re-validated against the tool's schema (validation failure → synthesized denial). Rewrites compose left-to-right across hooks. The rewritten arguments are also what the permission prompt displays, what the tool runs with, and what subsequent LLM turns see on the assistant message.
  - `system_message` — UI-only.

#### `post_tool`

Fires per tool call **if and only if the tool body actually ran**. `tool_status` is `success`, `failure`, or `cancelled` (cancellation during the tool body — cancellation is shielded so audit hooks still run). Does not fire when the tool never executed: `pre_tool` denial, user denial at the approval prompt, permission `NEVER`, or cancellation before the body started.

- **Receives** (in addition to the session context): `tool_name`, `tool_call_id`, `tool_input` (post-rewrite), `tool_status`, `tool_output` (structured result dict; null on failure), `tool_output_text` (the running text the LLM will see, mutable by prior hooks), `tool_error`, `duration_ms`.
- **Can return**:
  - `decision: "deny"` + `reason` — replaces `tool_output_text` with `reason`. Pipeline continues; subsequent hooks see the replacement.
  - `hook_specific_output.additional_context` (string) — **appended** (with a `\n` separator) to `tool_output_text`. Composes with a same-hook deny: deny replaces first, then `additional_context` is appended to the replacement.
  - `system_message` — UI-only.

### Session Management

#### Session Continuation and Resumption

Vibe supports continuing from previous sessions:

- **`--continue`** or **`-c`**: Continue from the most recent saved session
- **`--resume`**: Open an interactive session picker
- **`--resume SESSION_ID`**: Resume a specific session by ID (supports partial matching)
- **`/resume`** or **`/continue`**: Open the session picker from inside Vibe; press `D` twice to delete a local saved session. The active session cannot be deleted from this picker.

```bash
# Continue from last session
vibe --continue

# Open session picker
vibe --resume

# Resume specific session
vibe --resume abc123
```

Session logging must be enabled in your configuration for these features to work.

The first user message pins the resolved model to that session. Resuming keeps
the pinned model even if your configured default changes. `/model` uses the
normal config persistence target and updates an existing session override
immediately; the session file is synchronized when the next user message is
sent. An explicit persistent target selected through `/config` changes only
that layer. `/clear` starts a new, unpinned conversation that follows the
current config. If a selected model is no longer configured, Vibe falls back
to the current default model.

#### Working Directory Control

Use the `--workdir` option to specify a working directory:

```bash
vibe --workdir /path/to/project
```

This is useful when you want to run Vibe from a different location than your current directory.

Use `--add-dir` (repeatable) to make additional directories available to the agent for the duration of the session:

```bash
vibe --add-dir /path/to/other-project --add-dir /path/to/library
```

Each path is implicitly trusted (no trust prompt) and contributes its `AGENTS.md` and `.vibe/` configuration (tools, skills, agents, prompts, hooks) to the session. File-tool permissions treat each `--add-dir` path the same way as your primary working directory — reads and writes inside them don't require the "outside workdir" prompt. Nested paths collapse: passing `/repo` and `/repo/sub` is equivalent to passing just `/repo`.

Use `--worktree NAME` to create (or reuse) a [git worktree](https://git-scm.com/docs/git-worktree) and run inside it:

```bash
vibe --worktree my-feature
```

The worktree lives under `$VIBE_HOME/worktrees/<repo-name>-<repo-hash>/NAME` and is checked out on a branch named `NAME` (created if it doesn't exist, attached if it does). Vibe `cd`s into it before the session starts and trusts it for the session (no trust prompt). If you start Vibe from a subdirectory, Vibe enters the matching subdirectory inside the worktree.

Existing worktrees are reused only when they belong to the same git repository and are checked out on branch `NAME`; otherwise Vibe exits with an error instead of running in the wrong checkout.

Pass `--worktree` with no name to have Vibe name one for you:

```bash
vibe "Fix the login bug" --worktree     # -> fix-the-login-bug, on vibe/fix-the-login-bug
vibe --worktree                         # no prompt -> a random slug, e.g. brave-quiet-otter
```

The name comes from your prompt, shortened to whole words. Without a prompt — or when the prompt has nothing usable in it, such as emoji only — Vibe generates a random slug instead. Unlike the named form, this never reuses an existing worktree: Vibe claims a free name, adding `-2`, `-3` and so on if needed, so two sessions started at once can never land in the same checkout. The branch is always `vibe/<name>`, matching the worktrees Le Chat Desktop creates.

Order matters, because `--worktree` takes an optional value: `vibe --worktree "Fix the login bug"` reads the prompt as the *name*. Put the prompt first, or separate it with `--`:

```bash
vibe --worktree -- "Fix the login bug"
```

Automatic cleanup only applies to worktrees Vibe created this run, and only after a session actually started — a startup failure (bad config, `--continue` with no sessions) never deletes anything, and a reused worktree is always left in place. When an interactive session exits, Vibe removes the worktree directory automatically if there are no uncommitted changes, untracked files, or commits beyond the commit where the worktree session started. If any of those exist, Vibe asks whether to keep or remove the worktree. When Vibe created the branch it is deleted alongside the worktree; a branch that already existed and was merely attached is kept unless you confirm its deletion. Keeping preserves the directory and branch so you can return later; removing force-deletes them, discarding changes, untracked files, and commits. Programmatic runs (`vibe -p ... --worktree NAME`) do not clean up automatically because there is no exit prompt; remove them manually with `git worktree remove`. `--worktree` is ignored with `--setup` and `--check-upgrade`.

Sessions are scoped per directory, so `-c`/`--continue` and the `--resume` picker only see sessions started inside that worktree. To carry a session across worktrees, resume it explicitly by ID with `--resume <ID>`.

#### Worktree ownership

Whichever way a worktree is created, Vibe writes an ownership record beside it under `$VIBE_HOME/worktrees/.claims/<repo-name>-<repo-hash>/<name>/`, recording the branch, the commit the session started from, and whether Vibe created the branch. Nothing is ever removed without one: a worktree you made yourself, or one whose record is missing or unreadable, is left alone.

The record directory also holds a marker per session currently working in the worktree. Sessions from different clients run in separate processes with nothing shared between them, so a marker is the only evidence that someone else is still in there. A worktree with any marker left is kept. A process killed outright leaves its marker behind and the worktree survives, which is the direction worth failing in.

The app-server never removes a worktree on its own. Closing a session does not count: the desktop app releases an idle session's process a second after each turn to reclaim it, and the session stays live and resumable, so its worktree outlives that. A worktree that exists is removed in exactly one situation — **you delete its session**. It still has to be one Vibe created, held by nobody else, and free of uncommitted changes, untracked files, and commits made since the session began; anything else is kept and logged.

Two things are cleaned up without asking, neither of which is a worktree you could have worked in. A session whose very first turn never completed has its worktree rolled back, because such a session is never published and leaves no session file — there is nothing to return to. And a reservation that never became a worktree, an empty directory left by a claim whose `git worktree add` did not land, is discarded the next time a session starts in that repo.

The cost of that conservatism is that a worktree whose app-server was killed outright stays on disk, holding a marker for a session that no longer exists. Removing it is a judgement about whether you are finished with the work, which only you can make.

On Windows, Vibe resolves an absolute Git executable for automatic repository
inspection and ignores executables inside the current project. Set
`GIT_PYTHON_GIT_EXECUTABLE` to an absolute path when using a custom or portable
Git installation. Vibe still starts when no trusted Git executable is available;
only Git-dependent metadata and features are unavailable.

### Update Settings

Vibe checks for updates at most once per day during a session. When Vibe is installed from a package index with `uv tool install`, the check runs `uv tool list --outdated`, so your uv settings (such as `exclude-newer`) decide which version is offered, and an exact version pin (`mistral-vibe==X`) suppresses the offer. Other installs (git or local path, Homebrew, pip, or a uv too old for `--outdated`) query PyPI. When a newer version is found, the next launch shows an update prompt before opening the chat, offering to either update immediately (via `uv tool upgrade mistral-vibe` or `brew upgrade mistral-vibe`) or continue with the current version. Vibe reports success only once `vibe --version` shows the new version. If a uv install stays on the old version, for example because of a version pin, Vibe asks whether to run `uv tool install --force mistral-vibe@latest`, which drops the pin, extras and `--with` packages.

Run `vibe --check-upgrade` to check immediately, prompt to install a newer version if one exists, and exit.

To disable the daily check entirely, add this to your `config.toml`:

```toml
enable_update_checks = false
```

### Notification Settings

Vibe can notify you when the agent needs your attention (awaiting approval, asking a question, or task complete). This is useful when you switch to another window while the agent works.

To disable notifications:

```toml
enable_notifications = false
```

### Custom Vibe Home Directory

By default, Vibe stores its configuration in `~/.vibe/`. You can override this by setting the `VIBE_HOME` environment variable:

```bash
export VIBE_HOME="/path/to/custom/vibe/home"
```

This affects where Vibe looks for:

- `config.toml` - Main configuration
- `.env` - API keys
- `connector_bootstrap_cache.json` - Short-lived connector discovery cache
- `agents/` - Custom agent configurations
- `prompts/` - Custom system and compaction prompts
- `tools/` - Custom tools
- `logs/` - Session logs

Custom tools will be deprecated in a future release. Prefer skills for new
extensions; Vibe can help migrate existing custom tools to skills.

### Logging

Vibe writes structured logs to `~/.vibe/logs/vibe.log`. Use `/log-level` to open an interactive picker that lets you set the session override and/or persist a level to `config.toml`. You can also set `log_level` directly in `config.toml` or via the `/config` screen.

Valid levels: `DEBUG`, `INFO`, `WARNING` (default), `ERROR`, `CRITICAL`.

Precedence: session override > `LOG_LEVEL` env var > `log_level` in config.toml > default.

The `LOG_LEVEL` environment variable overrides the config value at startup. Use `DEBUG_MODE=true` to force `DEBUG` at startup.

## Editors/IDEs

Mistral Vibe can be used in text editors and IDEs that support [Agent Client Protocol](https://agentclientprotocol.com/overview/clients). See the [ACP Setup documentation](docs/acp-setup.md) for setup instructions for various editors and IDEs.

## Resources

- [CHANGELOG](CHANGELOG.md) - See what's new in each version
- [CONTRIBUTING](CONTRIBUTING.md) - Guidelines for feature requests, feedback and bug reports

## Data collection & usage

Use of Vibe is subject to our [Privacy Policy](https://legal.mistral.ai/terms/privacy-policy) and may include the collection and processing of data related to your use of the service, such as usage data, to operate, maintain, and improve Vibe. You can disable telemetry and crash reporting in your `config.toml` by setting `enable_telemetry = false`.


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
