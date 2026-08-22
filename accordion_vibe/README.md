# `accordion_vibe` — the Accordion bridge

This fork of mistral-vibe can host [Accordion](https://get-accordion.dev/)'s pi
extension without becoming pi. A Node **sidecar** loads the extension and speaks
a JSON-lines protocol over stdio; everything on this side lives in this package,
so the in-tree diff against upstream vibe stays a handful of lines.

The contract both halves build against is
`docs/sidecar-protocol.md` in the Accordion repo. Read it before changing
anything here.

## Turning it on

```bash
export ACCORDION_REPO=/path/to/accordion    # a checkout, or an npm install root
```

or in `~/.vibe/config.toml`:

```toml
[accordion]
repo = "/path/to/accordion"
app  = "/path/to/app.exe"     # optional: forwarded as the `accordion-app` flag
```

With neither set the bridge is **inert**: `build_agent_loop` hands back a plain
`AgentLoop`, no process is spawned, and vibe behaves byte-for-byte as upstream.

> **`ACCORDION_REPO`, not `ACCORDION_HOME`.** `ACCORDION_HOME` already belongs
> to the Accordion extension itself, where it relocates the `~/.accordion/`
> state directory (registry, door secret, controller lease). Reusing the name
> would have made the sidecar write its state into the checkout being pointed
> at. For the same reason the sidecar is spawned with `ACCORDION_HOME` stripped
> from its environment — see `child_environment()` in `_config.py`. The
> tradeoff: a user who deliberately relocated Accordion's state gets the
> default location for sidecar-hosted sessions.

The bridge spawns `node <repo>/extension/sidecar.mjs` (falling back to
`<repo>/extension/dist/sidecar.mjs`) with the session cwd, and appends the
sidecar's stderr to `~/.accordion/logs/vibe-sidecar-<session>.log`.

## In-tree seams (everything else is in this package)

| file | change |
|---|---|
| `vibe/app_server/_runtime.py` | `_AgentLoopBlueprint.build` calls `build_agent_loop(...)` instead of `AgentLoop(...)` |
| `vibe/cli/commands.py` | `_build_commands` splices in `_sidecar_commands()`; `EXTENSION_HANDLER_PREFIX` constant |
| `vibe/cli/textual_ui/app.py` | `_invoke_resolved_command` routes an `extension:` handler to `_run_extension_command` |
| `vibe/core/config/models.py` + `vibe_schema.py` | additive `[accordion]` config section |

## Hook coverage

Every hook pi exposes, and what this bridge does with it. "Bridged" means the
sidecar receives it with vibe-native payloads.

### Bridged

| pi hook | where it is emitted | notes |
|---|---|---|
| `context` | `_messages_for_backend` | the only **blocking** hook: 250 ms budget, then passthrough. Only the main agent completion consults it (see *Call classification*). An empty wire is never sent, and a reply longer than the request is rejected as a passthrough (Accordion may collapse a group, so shorter is legal) |
| `session_start` | first `act()` | carries the already-loaded messages so the Truth is seeded. `reason` is always `"start"` — see *Known gaps* |
| `session_shutdown` | `aclose()` | followed by `shutdown` and a reaped process |
| `session_before_compact` / `session_compact` | `compact()` override | notification form (no `req`); `session_compact` carries the post-compaction `messages` so the map refreshes at once. Manual `/compact` is deliberately not special-cased |
| `before_agent_start` | `act()` entry | a returned `systemPrompt` replaces `messages[0].content` for that run only, non-destructively, inside `_messages_for_backend` |
| `agent_start` / `agent_end` | `act()` entry / `finally` | `agent_end.messages` is the slice appended during that run |
| `turn_start` / `turn_end` | `_perform_llm_turn` | one vibe LLM turn = one pi turn. `turn_end` carries the assistant message plus that turn's tool messages |
| `message_start` | `_chat_streaming` (streamed) / message drain (everything else) | |
| `message_update` | `_chat_streaming` | accumulated partial, throttled to ≤ 10/s |
| `message_end` | message drain at turn/run boundaries | covers user, assistant and tool messages |
| `tool_execution_start` / `tool_execution_end` | `_invoke_tool` | `result` is the tool's result model as JSON; a raised `ToolError` reports `isError` |
| `model_select` | `_get_context` | vibe has no model-change event, so the active model is compared once per turn. First observation reports `source:"restore"` |
| `resources_discover` | right after `session_start` | the reply's `skillPaths` are folded into `SkillManager`'s search paths on the first `act()`. pi returns each skill *directory*; vibe searches its parent, so each one is lifted a level. `promptPaths`/`themePaths` are ignored |
| `usage` (not a pi hook) | `_update_stats` | sent after the model call and before that message's `message_end`, which is what token calibration pairs on. Secondary calls are excluded |

### Not bridged

| pi hook | why |
|---|---|
| `tool_call` | vibe's pre-tool path is a multi-stage pipeline in `AgentLoopHooksMixin` (`_PreToolResolution`, denial synthesis, permission re-validation). Honouring `block`/`args` correctly means reproducing that; the extension does not register it today |
| `tool_result` | same pipeline, same reason; `tool_execution_end` already carries the outcome |
| `tool_execution_update` | vibe streams partial tool output as `ToolStreamEvent`s; forwarding every one is a high-rate hook nobody consumes |
| `project_trust` | vibe's trust decision is made before any agent loop (and therefore any sidecar) exists |
| `resources_discover` → `promptPaths` / `themePaths` | vibe has no equivalent runtime search-path list to extend |
| `session_info_changed` | vibe's session title is app-server state; Accordion does not read it |
| `session_before_switch` / `session_before_fork` / `session_before_tree` / `session_tree` | vibe replaces or rebinds the whole `AgentLoop`; the bridge's answer is a new sidecar, not a hook |
| `agent_settled` | vibe has no equivalent boundary distinct from `agent_end` |
| `before_provider_headers` / `before_provider_request` / `after_provider_response` | provider-level, below the message abstraction the sidecar speaks |
| `input` / `user_bash` | CLI-side, and the sidecar has no way to answer them synchronously |
| `thinking_level_select` | notification-only, unused by Accordion |

That is every hook in pi's `ExtensionEvent` list; nothing is silently unaccounted for.

## Call classification

`_messages_for_backend` is also vibe's path for title generation, compaction
summaries and teleport summaries. Only the **main agent completion** may consult
the sidecar; the discriminator is object identity:

```python
messages is self.messages
```

`_chat` and `_chat_streaming` pass `self.messages` itself; every secondary call
(`compaction/manager.py`, `_summarize_teleport_context`, …) builds a fresh list.
The same flag gates the `usage` hook, so a compaction call's token counts never
land on the calibration anchor.

## Compaction

When the sidecar reports `folding{enabled:true}`, `_setup_middleware` drops
`AutoCompactMiddleware` from the pipeline — Accordion owns the context, and
vibe's own auto-compaction would fight it. `enabled:false` restores it. The
swap happens at a turn boundary (`_get_context`) and only when the flag actually
changed, so middleware counters are not reset on every turn. Manual `/compact`
is never touched.

## Tools

`unfold` and `recall` are registered on the loop's `ToolManager` **only** when
the bridge attaches, and both relay straight to the sidecar. Their arg schema is
`{codes: string[]}`.

## Testing

```bash
uv run pytest tests/accordion_vibe
```

Every test drives a **real subprocess** — `tests/accordion_vibe/fake_sidecar.py`,
a scriptable Python stand-in — because the interesting behaviour lives in the
pipes: a 250 ms timeout, a reader thread, a process that dies mid-request. The
seam is `accordion_vibe._bridge.sidecar_command`, monkeypatched per test.

`tests/accordion_vibe/test_real_sidecar_manual.py` runs the same paths against
the actual Node bundle and is skipped unless you point it at a checkout:

```bash
ACCORDION_E2E_REPO=/path/to/accordion uv run pytest tests/accordion_vibe
```

## Failure semantics

Spawn failure, a missing `ready` within 5 s, a `context` timeout, a crash, or a
broken pipe all resolve to the same thing: the bridge marks itself detached,
records a one-time notice, and every later hook is a no-op. Nothing on the
model-call path can raise because of the sidecar.

## Known gaps / follow-ups

- **`session_start.reason` is always `"start"`.** Distinguishing resume / new /
  fork needs the app-server's session intent, which `AgentLoop` does not see.
- **Dynamic tool generation.** `ready.tools` carries JSON schemas, but the two
  tools are hand-written. A mismatch logs a warning instead of generating a
  `BaseTool`.
- **Conductor `complete()` relay.** Not part of the protocol yet.
- **Slash-command discovery is mildly racy.** `CommandRegistry` is built while
  the handshake may still be in flight; it waits up to 1 s and otherwise
  announces `/accordion` from a static fallback.
- **`_register_discovered_tool_variant` is a private `ToolManager` method.**
  The alternative (appending a search path and re-running file discovery) is
  both more private and unusable in a frozen build.
