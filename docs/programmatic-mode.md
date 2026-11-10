# Programmatic mode

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

Without `--auto-approve`, a tool call that would ask for approval is denied, and the
agent is told so. This lets an agent's allowlist restrict the shell to a few
commands, for example only the test runner (`~/.vibe/agents/tester.toml`):

```toml
[tools.bash]
permission = "ask"
allowlist = ["pytest", "npm test"]
```

```bash
vibe --prompt "Run the tests and fix failures." --agent tester --enabled-tools bash
```

A chained command such as `pytest; rm -rf build` is denied unless every part is
allowlisted.

Commands the agent runs get `VIBE_SESSION=1` in their environment, so scripts can detect they run under Vibe.

## Options

When using `--prompt`, you can specify additional options:

- **`--max-turns N`**: Limit the maximum number of assistant turns. The session will stop after N turns.
- **`--max-price DOLLARS`**: Set a maximum cumulative cost in dollars for this run's root session and subagents. Once measured cost reaches the limit, no further completion starts and the outcome is `price_limit`; calls already admitted may finish, retries included. A model whose prices are 0, the default, is free, so `--max-price 0` runs free models only and stops before the next call once one costs anything.
- **`--max-tokens N`**: Set a maximum cumulative LLM token budget for this run's root session and subagents, counting both prompt and completion tokens. Once measured usage reaches the limit, no further completion starts and the outcome is `token_limit`; calls already admitted may finish, retries included. This is separate from the output cap on one completion.
- **`--agent NAME`**: Select the agent profile for this run.
- **`--model ALIAS`**: Use this model alias for the run instead of `active_model`, without saving it as the default. Only for a new session; an unknown alias is a usage error.
- **`--auto-approve`, `--yolo`**: Approves all tool calls without prompting, including in interactive sessions. A new session on a built-in mode (`ask`, `accept-edits`, `smart-approve`) starts the `auto-approve` agent instead, which Shift+Tab can leave; a custom agent, or a resumed session, keeps its agent with every call approved.
- **`--enabled-tools TOOL`**: Enable specific tools. In programmatic mode, this disables all other tools. Can be specified multiple times. Supports exact names, glob patterns (e.g., `bash*`), or regex with `re:` prefix (e.g., `re:^serena_.*$`).
- **`--disabled-tools TOOL`**: Disable specific tools after `--enabled-tools` filtering. Can be specified multiple times. Supports exact names, glob patterns (e.g., `bash*`), or regex with `re:` prefix (e.g., `re:^serena_.*$`).
- **`--output FORMAT`**: Set the output format. Options:
  - `text` (default): Human-readable text output
  - `json`: All messages as JSON at the end
  - `streaming`: Newline-delimited JSON per message

  On a resumed session, `text` and `streaming` cover only the new run, so a run that ends without a reply of its own prints none; `json` holds the whole session.
- **`--prompt-file PATH`**: Read the prompt from a file instead of `--prompt`. With `-p` and no text, the prompt is read from stdin.
- **`--system-prompt-file PATH`**: Replace Vibe's own instructions in the system prompt, and the agent's, with the file's text. The harness's general guidance still comes first, and the project context, `AGENTS.md` files and tool instructions still follow it. An empty or unreadable file is a usage error.
- **`--time-limit SECONDS`**: Stop the run after this many seconds. The active turn and its tool processes are stopped, and the session can be resumed with `--resume`. SIGTERM stops a run the same way.
- **`--output-dir DIR`**: Write `DIR/export.json` and copy the session journal to `DIR/session/`, whichever way the run ends. The export records the Vibe version, the effective config (header and environment values redacted), token usage, cost, the number of model calls the agent made (`steps`), the stop reason and the outcome. When the session had config issues, such as a skill or a config file it could not load, the export lists them under `warnings`, each as `{"message": str, "source": str | null}`, where `source` names the file or skill it is about; the field is left out when there are none, and the run goes on regardless. Usage, cost and `steps` count this run only, also on a resumed session, while the copied journal holds the whole session. Only an argument the parser itself rejects (an unknown flag, a malformed value) exits 1 without an export.

Completion budgets include provider-reported usage from every discarded
provider-accepted attempt, including a nonretryable failure. The in-context
compaction reasoning cutoff is an exception: it closes the stream before the
provider reports usage, so its locally estimated usage is counted instead. An attempt that ends before the provider reports usage, such as a
dropped stream, counts nothing and is logged as a warning. Model calls Vibe
makes outside the agent loop, such as describing an image for a model without
image input, are not counted and do not stop at a budget.

Example:

```bash
vibe --prompt "Analyze the codebase" --max-turns 5 --max-price 1.0 --max-tokens 50000 --output json
vibe --prompt-file task.md --auto-approve --time-limit 1800 --output-dir out/
```

## Exit codes

| Code | Outcome in `export.json` | Meaning |
| --- | --- | --- |
| `0` | `finished` | The agent finished. |
| `1` | `usage_error`, `config_error` | The run could not start as asked. |
| `2` | `infrastructure_failure` | The model API (after retries) or the runtime failed. |
| `3` | `turn_limit`, `token_limit`, `price_limit`, `deadline`, `terminated`, `length`, `refusal` | The agent stopped without finishing; score what it left. `length` means the conversation or the last answer hit a model token limit: the context window, or the output-token cap (`agent_max_output_tokens`, else the model's `max_output_tokens` where the provider requires a cap). An answer cut off at the output cap is first discarded and asked for again, up to `output_limit_retries` times (6 by default). `refusal` means the model declined to answer. |
| `4` | `aborted` | The `--agent-socket` server aborted the run (experimental, see below); `error.message` in `export.json` is the server's message. |

`vibe -p` exits only after its app-server process has, however the run ends.
Killed outright (SIGKILL, the OOM killer), it takes its app-server with it: at
once on Linux and Windows, and on macOS as soon as the app-server sees its
input close, normally within a second. `vibe run-server`'s app-servers do the
same.

## Agent Socket (`--agent-socket`, experimental)

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
`--workdir`, `--worktree`, `--add-dir` or `--continue`; those are usage errors
(exit 1). It is not supported on Windows (exit 1).

To continue a finished run with a new prompt, start a new run with
`--resume SESSION_ID`, taking the ID from `session_id` in the first run's
`export.json`. The new run redoes the handshake, so the server may be a new one
and declares its tools again; the model sees the whole earlier conversation
followed by the new prompt. Every limit (`--max-turns`, `--time-limit`,
`--max-tokens`, `--max-price`) counts from the new run's start. Its export, under
the same `session_id`, counts only its own `steps`, `usage` and `cost_usd`; its
`journal_dir` holds the whole session, earlier runs included.

### Sandboxed sessions

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

### Protocol

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

## Many runs in one process (`vibe run-server`, experimental)

Experimental: the command is hidden from `--help`, and it and its protocol may
change or go away without notice. It is not supported on Windows (exit 1).

`vibe run-server` serves many runs at once, each what
`vibe -p --agent-socket PATH --output-dir DIR` does, with the same outcomes,
exit codes, limits and export. Every run must use an agent socket that serves a
sandbox.

Runs share app-server processes rather than starting one each. An app-server
serves at most `--runs-per-app-server K` runs (default 16). A run goes to the
app-server with the most runs that still has room, so that a spare one drains;
when none has room, another starts. An app-server exits once its last run has
gone, and a later run starts another. One that exits early fails only its own runs, as
`infrastructure_failure`. One that has not let a run go 15 seconds after it
ended takes no new run.

It speaks JSON-RPC 2.0 on stdin and stdout, framed as the agent socket is: one
compact JSON message with camelCase fields per line. Only the protocol is
written to stdout. Errors are `{"code": str, "message": str, "data": any}`,
where `code` is `invalid_request`, `invalid_params`, `not_initialized`,
`not_found`, `conflict`, `method_not_found` or `internal_error`; an
`invalid_params` error names the fields at fault, never their values.
`--max-runs N` caps the live runs.

| Method | Params | Result |
| --- | --- | --- |
| `initialize` | `{"protocolVersion": 1, "clientInfo": {"name": str, "version": str}}` | `{"protocolVersion": 1, "serverInfo": {"name": "vibe-run-server", "version": str}, "maxRuns": int \| null}` |
| `run/start` | `{"runId": str, "prompt": str, "agentSocket": str, "outputDir"?: str, "resumeSessionId"?: str, "agent"?: str, "autoApprove"?: bool, "enabledTools"?: [str], "disabledTools"?: [str], "maxTurns"?: int, "maxTokens"?: int, "maxPrice"?: float, "timeLimit"?: float, "config"?: object, "credentials"?: {str: str}}` | `{}` |
| `run/stop` | `{"runId": str}` | `{}` |
| `ping` | `{}` | `{}` |

- `initialize` comes first, once. Any other protocol version is refused with
  `invalid_request`, whose `data` is `{"protocolVersion": 1}`; any other
  request before it, with `not_initialized`.
- `run/start` answers at once, and the run goes on in the background. `runId`
  is the caller's name for the run, unique among live runs; a live one is a
  `conflict` with `data` `{"reason": "duplicate_run"}`, and `maxRuns` live runs
  a `conflict` with `{"reason": "at_capacity", "maxRuns": int}`. The options
  and limits are those of `vibe -p`; `timeLimit` counts from the request.
  Relative `agentSocket` and `outputDir` paths name paths under the directory
  the server started in.
- `config` is the run's user config, the document `config.toml` would hold as
  parsed TOML. It replaces the server's own `config.toml` for the run, except
  for what applies to the whole app-server process (see below).
- `credentials` maps environment variable names to values. A provider whose
  `api_key_env_var` names one sends that value. The values stay in memory:
  they are not put in the environment, and are redacted from exports, logs,
  error messages and `run/ended`.
- `run/stop` ends a live run as `terminated`, as SIGTERM ends `vibe -p`;
  `not_found` when no live run has that `runId`. A stopped run that has not
  wound down 15 seconds after the stop is cancelled, and still ends with
  `run/ended`: `terminated`, with an `error` saying it was cancelled, whose
  `code` is `stop_timed_out`. If its export then takes more than 5 more
  seconds, `run/ended` is sent without it, with an `exportError` and an
  `exportErrorCode` of `export_timed_out`.
- `ping` is answered at any time, before `initialize` too. A ping with no
  answer means the server is wedged.

Each run that started ends with one notification, after its export is
written:

```json
{"jsonrpc": "2.0", "method": "run/ended", "params": {"runId": str, "outcome": str, "exitCode": int, "sessionId": str | null, "error": {"message": str, "code": str | null} | null, "exportPath": str | null, "exportError": str | null, "exportErrorCode": str | null, "warnings": [{"message": str, "source": str | null}] | null}}
```

`outcome` and `exitCode` are those of `vibe -p` (see [Exit codes](#exit-codes)),
`aborted` and 4 included. `error.code`, when set, is a stable name for the
failure, so failures can be counted without matching messages. `exportError`
says why the export could not be written, and `exportErrorCode`, when set,
names that reason. `warnings` are the export's. The `runId` is free again once
the notification is sent.

On end of input, SIGTERM or SIGINT, the server ends every live run as
`terminated`, as `run/stop` does, writes its export, sends its `run/ended`,
stops its app-servers and exits 0. A message that is not a request ends it the
same way, with exit 1, without repeating the message. A run that has no
`run/ended` when the server's output closes, or the process exits, is lost:
treat it as an infrastructure failure. It has no `export.json` (the export is
written once, at the end, and renamed into place, so a `.export.json.tmp` may
be left), and its session's journal stops at its last saved message in the
session log directory. Its sandbox is left as it was.

The runs share `VIBE_HOME` and `HOME` (session logs, trusted folders, the
user's agents, prompts, skills and tools), the `.env` file, the environment and
the log file. The server's log lines for a run name it as `run{run=<runId>}`.
The server does not report errors to Sentry.

Two settings apply to a whole app-server process, so to each of its runs. It
takes them from the server's own `config.toml` when its first run starts. A run
whose `config` sets `enable_system_trust_store` otherwise ends as a
`config_error`. A run whose `config` sets another `log_level` goes on at the
app-server's level, with a warning in its `warnings` and in the log. The
app-server sets up no process-wide tracer, so no run's API key serves another
run's traces.

Each app-server starts with `MALLOC_ARENA_MAX=2` unless the server's
environment sets it. Its runs use many threads, and on Linux with glibc's
default of up to eight memory arenas per core, a busy app-server's memory grows
by about 45 KiB per run; with two arenas it stays flat.
