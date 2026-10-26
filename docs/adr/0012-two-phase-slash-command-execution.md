# 0012 Slash Commands While Busy

## Decision

The Runtime-owned accepted queue, exposed through the app server, contains
structured user turns only. App-server modules enforce product command policy.
The terminal client does not keep a second queue for slash commands, shell
commands or deferred settings writes.

Slash commands have two execution policies:

- Commands marked `side_channel` may run immediately while an agent turn or
  shell command is active. Only one such command runs at a time.
- All other slash commands require an idle session. If the user submits one
  while busy, the client keeps the text in the input and shows a warning.

Shell commands also require an idle session. They are rejected while an agent
turn, another shell command, or accepted queued prompt is active.

Settings a turn reads open their picker only while idle; settings no turn reads
may be side-channel commands. Once the user confirms a choice, the client sends
the app-server request directly, and the app server decides whether a running
turn blocks that write. There is no deferred client-side persistence queue.

## Rationale

Runtime is the single owner of accepted turn ordering; the app server enforces
command policy through its session capabilities. A second client-side queue for
commands and shell work duplicates scheduling rules and forces the UI to keep a
drain task, command barriers, discard callbacks, and picker coordination.

Rejecting non-side-channel commands while busy makes the behavior explicit and
keeps the client small. It also avoids ordering ambiguities such as whether a
queued `/clear` should discard prompts that the app server has already
accepted.

Settings writes that a turn reads require the session to be idle because they
can rebuild model, tool, skill, and prompt state. Since their commands cannot
open while busy, their handlers can persist directly without retry or deferral
logic.

## Side-channel rules

A side-channel command must be safe to run alongside active work. Suitable
commands display information, copy data, exit, or write settings no turn reads.
They must not mutate session history or configuration the active turn reads.
MCP, connector, and plugin changes stay idle-only: the Harness refuses them
while a turn runs.

If one side-channel command is already running, a second one is rejected with a
warning. Side-channel commands are not queue items and do not appear in the
queued-message UI.

## Agent Guidance

- Mark a command `side_channel` only when it is safe during an active turn or
  shell command, and make sure every app-server and Harness call behind it
  accepts a running turn, with a backend contract test against the real
  adapter.
- Add a config path to `config/write`'s turn-independent allowlist only when no
  turn, tool, or prompt reads it.
- Mark a side-channel command `.after_jobs()` when its server write conflicts
  with a compaction, teleport, project request, or agent install.
- Keep lifecycle commands such as `/clear`, `/compact`, `/rewind`, `/resume`,
  and `/retry` on the idle-only path.
- Keep settings commands that a turn reads on the idle-only path and write
  config directly after the user confirms the picker.
- Do not add command, shell, callback, or barrier variants to the prompt queue.
  New queued work belongs in an explicit app-server API.
- Preserve the submitted input when rejecting a busy command so the user can
  retry it after the active work finishes.

## Flag To User When

- A proposed side-channel command mutates state that the active turn can read.
- A change introduces a client-side deferred queue or local ordering barrier.
- A delivery surface needs a new kind of queued work that the app-server
  protocol does not support.
