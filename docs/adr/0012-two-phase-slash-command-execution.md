# 0012 Slash Commands While Busy

## Decision

The app server queue contains structured user turns only. The Textual client
does not keep a second queue for slash commands, shell commands, or deferred
settings writes.

Slash commands have two execution policies:

- Commands with `side_channel=True` may run immediately while an agent turn or
  shell command is active. `SideChannelController` allows one such command at a
  time.
- All other slash commands require an idle session. If the user submits one
  while busy, the client keeps the text in the input and shows a warning.

Shell commands also require an idle session. They are rejected while an agent
turn, another shell command, or accepted queued prompt is active.

Settings commands such as `/model`, `/thinking`, `/theme`, `/voice`, and
`/proxy-setup` open their picker only while idle. Once the user confirms a
choice, the client sends the app-server config request directly and waits for
the result. There is no deferred client-side persistence queue.

## Rationale

The app server is the single owner of turn ordering. A second Textual queue for
commands and shell work duplicated scheduling rules and forced the UI to keep a
drain task, command barriers, discard callbacks, and picker coordination.

Rejecting non-side-channel commands while busy makes the behavior explicit and
keeps the client small. It also avoids ordering ambiguities such as whether a
queued `/clear` should discard prompts that the app server has already
accepted.

Settings writes require the session to be idle because they can rebuild model,
tool, skill, and prompt state. Since their commands cannot open while busy,
their handlers can persist directly without retry or deferral logic.

## Side-channel rules

A side-channel command must be safe to run alongside active work. Suitable
commands display information, copy data, or exit. They must not mutate runtime
configuration, session history, or other state used by the active turn.

If one side-channel command is already running, a second one is rejected with a
warning. Side-channel commands are not queue items and do not appear in the
queued-message UI.

## Agent Guidance

- Add `side_channel=True` only when the command is safe during an active turn or
  shell command.
- Keep lifecycle commands such as `/clear`, `/compact`, `/rewind`, `/resume`,
  and `/retry` on the idle-only path.
- Keep settings commands on the idle-only path and write config directly after
  the user confirms the picker.
- Do not add command, shell, callback, or barrier variants to the prompt queue.
  New queued work belongs in an explicit app-server API.
- Preserve the submitted input when rejecting a busy command so the user can
  retry it after the active work finishes.

## Flag To User When

- A proposed side-channel command mutates state that the active turn can read.
- A change introduces a client-side deferred queue or local ordering barrier.
- A delivery surface needs a new kind of queued work that the app-server
  protocol does not support.
