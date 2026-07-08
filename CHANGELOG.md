# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.19.1] - 2026-07-08

### Added

- Experimental managed bash tool for running and tracking shell sessions
- `--disabled-tools` CLI flag to turn off specific tools for a run
- Per-tool description overrides via `<tools-dir>/prompts/<name>.md`
- Setting to clear context when a plan is accepted
- Discovered `AGENTS.md` files are now surfaced during `read_file` tool calls

### Changed

- Reactive compaction with a dedicated summary fallback and stricter summaries
- User-invoked skills now load as a synthetic tool call
- Tool config now merges via deep merge
- MCP panel now refreshes silently in the background
- Debounced the agent-switch spinner
- Neutral color for keyboard shortcut hints
- Falls back to the first available model when the active model is unknown
- HTTP requests now honor CIDR notation in `NO_PROXY`
- History now loads lazily on resume, and session writes are off the event loop
- Faster startup by deferring heavy imports
- PII and identifying paths are now scrubbed from Sentry reports

### Fixed

- Rewind now happens in place instead of forking the session
- No longer includes pydantic URLs in error messages
- Text can now be scrolled while selecting at the chat bottom
- Click text selection is now scoped to word and paragraph
- DuplicateIds crash when the recording UI starts twice
- Git subprocess output is now decoded as UTF-8
- Corrected the truncated indicator position and removed a duplicate fetch link


## [2.19.0] - 2026-07-03

### Added

- `--worktree NAME` option to create or reuse a git worktree and run inside it
- Rewind support over ACP for both the agent and the host

### Changed

- Teleport URL in the CLI is now clickable
- Raised the Leanstral context limit
- Sentry auto integrations are now disabled

### Fixed

- Path prompt work is now offloaded from the UI thread
- StopIteration crash in `QuestionApp` when submitting an empty multi-select
- User message index now accounts for the deferred system prompt
- No longer reports benign Sentry noise (KeyboardInterrupt and destroyed pending tasks)


## [2.18.4] - 2026-07-01

### Changed

- Whole-line content now shown in the edit diff
- Declined or skipped tool calls now render as a muted square
- Auto-approve now works in lean mode

### Fixed

- Session resume with `--continue` now matches the resolved working directory
- Long tool call titles now wrap instead of being cropped
- Duplicate `mcp_servers` names in config are now rejected
- MarkupError crash when a tool error contained square brackets
- Teleport now uses the matched GitHub remote
- Raw compaction user messages are now preserved
- ACP now honors `default_agent` on new and resumed sessions
- Ambiguous teleport session creates are now retried


## [2.18.3] - 2026-06-30

### Added

- `j`/`k` navigation in selectable lists across the TUI (questions, theme picker, rewind, voice, MCP panels)
- `ask_confirmation_on_exit` config option to prompt before quitting
- Project-level `.vibe/config.toml` now persists config option changes

### Changed

- Consistent styling and casing for keyboard shortcut hints across the TUI
- `ask_user_question` now supports more than 4 options and questions

### Fixed

- No longer prompts to log in for disabled MCP servers
- Teleport diff no longer mutates the real git index


## [2.18.2] - 2026-06-29

### Added

- Sentry crash reporting for unhandled exceptions in the TUI (gated by `enable_telemetry`; off when telemetry is disabled)

### Changed

- Recoverable tool-call errors are now muted in the UI; only terminal errors render as a hard failure

### Fixed

- MarkupError crash when tool output contained square brackets
- OpenTelemetry chat/LLM spans missing after Mistral Python SDK 2.4.10+ telemetry opt-in gate


## [2.18.1] - 2026-06-26

### Added

- `/mcp add` slash command for adding OAuth MCP servers
- Petit chat animation idle pauses

### Changed

- Bare exit synonyms (exit, quit, :q, :quit) now treated as slash commands instead of prompts

### Fixed

- MCP OAuth login crash when keyring backend is un-loadable


## [2.18.0] - 2026-06-25

### Added

- Clickable URLs in web fetch and web search tool output
- Context window usage display in tokens at the bottom of the TUI
- Clipboard image paste support in the TUI (macOS)
- Max generated tokens set_config_option in ACP

### Changed

- MCP OAuth authentication UX improvements in `/mcp` panel
- Diff gutter and body split into separate widgets
- Faster UI startup with lazy heavy imports
- Improved connector performance with bootstrap caching

### Fixed

- macOS keychain access for Vibe credentials
- Standalone denylist incorrectly blocking commands with heredocs
- Copy selected text from prompt input and text areas
- Repeated keyring requests in Vibe Code
- Brew upgrade now always runs even when uv upgrade succeeds
- Terminal kill/release bounding for ACP to prevent hangs


## [2.17.1] - 2026-06-19

### Changed

- Commands in `/help` are now listed alphabetically in both the CLI and ACP
- `/teleport` is now always available and shows an explicit error when its prerequisites aren't met, instead of being hidden


## [2.17.0] - 2026-06-19

### Added

- `/mcp login`, `/mcp logout`, and `/mcp status` commands to authenticate OAuth-backed MCP servers from the TUI
- `vibe --check-upgrade` to force an immediate update check and exit
- `--yolo` as an alias for `--auto-approve`
- ACP now accepts inline image content blocks

### Changed

- API keys are now stored in the OS keyring instead of plain text
- Edit diff view now shows all replaced occurrences instead of just the first
- Completion popup now uses a two-column layout
- Chat messages now have right padding so text no longer collapses into the scrollbar
- Faster CLI shutdown by deferring resource cleanup on exit

### Fixed

- Skill autocomplete popup now dismisses after Tab completion
- Stdio MCP connections now persist across tool calls
- Retryable 5xx responses from the Mistral backend are now retried instead of failing


## [2.16.1] - 2026-06-16

### Added

- ACP workspace trust gated behind the `workspace-trust` client capability

### Changed

- `/resume` now opens much faster
- Startup update-failure message is now gentler and no longer alarms when an update can't be applied


## [2.16.0] - 2026-06-15

### Added

- Fuzzy search in slash command autocomplete
- Syntax-highlighted, line-numbered, theme-aware diffs for file edits

### Changed

- `/resume` now scopes the session picker to the current folder
- Clipboard copy confirmations now appear inline instead of stacking toast notifications
- ACP teleports now use the client title as the Vibe Code project name when no explicit project name is configured

### Fixed

- ACP max-output-token responses now stop gracefully with `max_tokens` instead of surfacing internal errors
- Context-too-long responses wrapped as HTTP 422 are now classified correctly
- SSE streams are parsed on CR/LF boundaries only, avoiding crashes on valid Unicode line separators in JSON strings
- CLI banner now shows the Free plan correctly
- Mistral backend no longer sends reasoning content when model thinking is off


## [2.15.0] - 2026-06-12

### Added

- **[Experimental]** `before_tool` and `after_tool` hooks: shell scripts declared in `hooks.toml` that fire around every tool call; hooks can deny the call, rewrite tool inputs, or append context to the output — enable with `enable_experimental_hooks = true`
- Message queue: messages typed while the agent or a `!bash` command is running are queued and shown above the input; Esc pauses the queue, Ctrl+C drops the last queued message (LIFO), Enter flushes the queue when paused
- Tool result output is now collapsed by default to keep responses scannable
- Common read-only shell commands (`ls`, `cat`, `pwd`, etc.) are allowed without approval by default
- Session deletion available directly from the resume picker
- `[mcp_servers.auth]` block in config for per-server MCP authentication
- Collapsed web tool output now shows the URL and search query at a glance
- `max_turns` support exposed over ACP via `set_config_option`

### Changed

- Compaction now re-injects prior user messages so the agent retains the original task goals across context resets
- **[Breaking — experimental hooks]** `post_agent_turn` retries no longer use exit code `2`; hooks must now exit `0` and return `{"decision": "deny", "reason": "..."}` on stdout to trigger a retry — exit code `2` is treated as a failure
- Model refusal stop reason is now surfaced to the user instead of stopping silently

### Fixed

- CLI UI no longer goes blank during app switches
- User messages are preserved correctly across repeated compactions
- Non-UTF-8 input is handled gracefully across all CLI surfaces
- `--resume` now shows history when launched from a dangerous directory
- Startup no longer crashes in unowned folders or when a git ancestor is unreadable
- Plain-string answers from `web_search` are handled without crashing
- NaN SGR mouse reports no longer leak into the chat input


## [2.14.1] - 2026-06-08

### Added

- `/teleport` slash command exposed over ACP, mirroring the TUI command for IDE integrations
- Startup prompt to install a pending Vibe update before continuing the session

### Fixed

- JetBrains IDEs no longer trigger a preemptive auth prompt over ACP and no longer drop terminal arguments
- Initial ACP slash-command advertisement is delayed so Zed registers commands like `/help` instead of rejecting them
- Built-in tool prompts no longer reference the removed `search_replace` name and now point to `edit`


## [2.14.0] - 2026-06-04

### Added

- Image attachments via `@`-mentions in the TUI for vision-capable models
- Session deletion, exposed over ACP as the `session/delete` extension method
- Browser sign-in now shows a copyable fallback URL when the browser does not open, so SSH and remote sessions can complete onboarding
- Toast notification when a `SKILL.md` file fails to parse instead of silently dropping the skill
- Trust prompt now proposes the git repository ancestor as a trust target
- `EnvironmentLayer` in the layered configuration, populated from `VIBE_`-prefixed environment variables

### Changed

- New tool-call format for `read` and the file `edit` tool
- `write_file` is now create-only and refuses to overwrite existing files
- Bumped `agent-client-protocol` to 0.10.1

### Fixed

- LLM calls now retry on network errors and timeouts (connection, read, write, remote protocol, timeout)
- Approval modal now sizes itself so the bottom of long tool-call payloads stays visible above the options block
- Banner connector count and `/mcp` panel ordering now reflect which connectors are actually usable and the user's enable/disable choices
- `disabled_tools` from runtime is merged with the TOML configuration instead of replacing it, and is enforced inside `ToolManager.get()`
- `shift+backspace` and `shift+delete` now work in the chat input
- Crash in the ACP `todo` plan-update handler when the model's tool call failed validation

### Removed

- Recursive search for nested harness files


## [2.13.0] - 2026-05-29

### Added

- `enable_system_trust_store` config flag to switch the shared SSL context to the OS trust store for corporate TLS / private CA setups

### Changed

- MCP HTTP transport now uses Vibe's shared SSL context so it honors `SSL_CERT_FILE` / `SSL_CERT_DIR` and the system trust store opt-in
- API key onboarding and plan-upgrade CTAs now link to the new Mistral Vibe Code extensions page
- Compaction summaries are now injected into the conversation instead of replacing it

### Fixed

- Crash during initialization
- VS Code extension promo banner now sits at the top of the conversation instead of being pinned above the input

## [2.12.1] - 2026-05-27

### Fixed

- VS Code extension promo link in the CLI banner now points to the renamed `mistralai.mistral-vibe-code` extension


## [2.12.0] - 2026-05-27

### Changed

- `/teleport` now uses the new Vibe Code Web sessions

## [2.11.1] - 2026-05-27

### Added

- Custom compaction prompts via the `compaction_prompt_id` config setting, resolved from `~/.vibe/prompts/` or `.vibe/prompts/`
- `--max-tokens` flag for programmatic mode (`-p`)
- ACP `_auth/status` and `_auth/signOut` extension methods so IDE extensions can show auth state and sign the user out
- Auth source assessment in `vibe.setup.auth` to classify the active credential source
- VS Code extension promo line in the CLI banner when launched from a VS Code, Cursor, or VS Code Insiders terminal
- `OverridesLayer` and `ProjectConfigLayer` for the layered configuration system

### Changed

- Programmatic mode (`-p`) no longer auto-approves tool calls by default
- Browser sign-in is enabled by default for Mistral providers; the `enable_experimental_browser_sign_in` flag is no longer required (stale entries are silently ignored)
- Compaction quality improved
- Refreshed user message styling and slash/teleport variants
- `/teleport` to Vibe Code Web now carries the working diff and last commit, matching the legacy teleport
- Reworded `/teleport` help and completion copy to say "Vibe Code Web"

### Removed

- Zed extension publishing job from the release workflow


## [2.11.0] - 2026-05-25

### Added

- Load skills from `~/.agents/skills` so they can be shared across agents
- Restore the textual theme selection system with an onboarding theme picker
- Surface unauthenticated connectors in `/mcp` with `needs auth` / `needs setup` labels and start the OAuth flow from inside the CLI
- Current date injected into the system prompt
- `minimal` system prompt variant for eval and training

### Changed

- Refreshed manual API key onboarding screen to match the new onboarding panel layout
- Newly discovered connectors are now disabled by default; existing user choices are preserved

### Fixed

- Preserve original line endings in ACP `search_replace`
- Show MCP/connectors count as `enabled/total` in the banner
- ACP grep tool displays the search path as a chip and no longer drops the filename


## [2.10.1] - 2026-05-20

### Added

- Pretty session titles that format `@mention` syntax for human-readable display
- Auto-emit `SessionInfoUpdate` on the first prompt so IDE session pickers reflect the title immediately
- End-to-end layered config read path with `TomlFileLayer`, `ConfigBuilder`, and `ConfigOrchestrator`

### Fixed

- MCP menu no longer shows `D`/`E` shortcuts in the detail view when there are no tools to enable or disable
- Parallel `bash` tool calls in ACP each render their own live terminal instead of racing on shared state


## [2.10.0] - 2026-05-19

### Added

- GrowthBook A/B testing layer with first system prompt experiment
- `--add-dir` flag to pull additional repository roots into a session
- `--no-autofill` flag for bump_version.py script
- TTY-keyed `--continue` scoping to current terminal
- Improved plan mode readability with live-editable plan display (Ctrl+G to edit)
- ACP dispatch user rating telemetry
- `enable_connectors` config flag to control connector availability
- Connectors migrated to public GA endpoints

### Changed

- Combine git subprocess calls in SessionLogger for improved performance by [@MichisGitIsKing](https://github.com/MichisGitIsKing)
- Indent assistant message content to align with other messages
- Deprecate retrying workflow status and expose retry source
- Drop consecutive user-message merging in LLM backends

### Fixed

- Share session permissions with subagents
- Bump pydantic-settings to >=2.13.0
- Avoid closing in-use backend on agent reload
- Slash commands broken when automatic IDE context is enabled
- Debounce tool approvals to avoid interrupting user typing
- Preserve newline style in search_replace
- Reset cursor position on up-arrow
- Remove hardcoded API key env var in websearch
- Fix whoami hardcoded url
- Pin dependencies in published wheel


## [2.9.6] - 2026-05-11

### Added

- Syntax-highlighted file diffs for `write_file` and `search_replace` in the IDE agent webview
- Spinner and loader for `!` (bang) command output
- `prepare release` script can resume after resolving merge conflicts

### Fixed

- Strip wildcard suffix when persisting bash allowlist patterns
- Build a combined SSL context for all outbound HTTPS requests
- Hide non-interactive tools from the LLM in ACP and programmatic mode


## [2.9.5] - 2026-05-06

### Added

- `/loop` command to run a prompt or slash command on a recurring interval
- `default_agent` config setting
- Telemetry instrumentation for the `teleport` command
- Logging environment variables documented in `--help`

### Changed

- `enable_telemetry` now takes precedence over `enable_otel`

### Fixed

- Non-retryable errors raised by sub-activities now correctly stop the retry when the agent loop runs inside a Temporal activity (deepens the 2.9.4 fix to walk the exception cause chain)
- Compacted session IDs displayed correctly
- Reload the history file before writing so parallel instances don't clobber each other
- `read_file` flagged as truncated when the limit is reached
- CLI loader left-alignment
- Default scroll sensitivity in the TUI restored
- Loosened the "no git commit" constraint
- Ensure `enable_telemetry` takes precedence over `enable_otel`


## [2.9.4] - 2026-05-05

### Added

- `/rename` command to rename the current session
- `feat: vibe.at_mention_inserted` telemetry event
- "Always allow" tool permissions persist across sessions
- Eager agent-loop warmup so `vibe.ready` telemetry fires sooner

### Changed

- `bash` (`!command`) bang commands run via async subprocess for better latency
- Bumped `mistral` SDK to 2.4.4
- Bumped `cryptography` to address upstream CVEs

### Fixed

- Preserve `non_retryable` flag on exceptions raised through `_chat` / `_chat_streaming`, so callers driving the agent loop from a Temporal activity can signal "do not retry"
- `/clear` no longer chains `parent_session_id` to the previous session
- `vibe.new_session` telemetry no longer fires when resuming a session

### Removed

- Windows ARM build artifacts (no longer published; required to bump `cryptography`)


## [2.9.3] - 2026-04-30

### Added

### Changed

### Fixed

- Fix textual version

### Removed


## [2.9.2] - 2026-04-29

### Fixed

- Teleport surfaces the latest GitHub connection status while polling


## [2.9.1] - 2026-04-29

### Added

- Connector OAuth authentication flow in `/mcp` menu
- `ConfigPatch` operation types for Vibe Code
- `extra_headers` field to `ProviderConfig`
- Structured metadata on ACP tool results
- `vibe.user_cancelled_action` ACP telemetry coverage
- `vibe.new_session` telemetry event emitted whenever the session is reset

### Changed

- Migrated default model to `mistral-medium-3.5`


## [2.9.0] - 2026-04-28

### Added

- Scratchpad directory for temporary working files shared with subagents
- `/copy` slash command
- Experimental hooks system with post-agent-turn lifecycle
- OpenAI Responses API adapter
- ACP session fork and session close support
- Thinking level picker in ACP CLI
- `--trust` session-only flag and fail-fast behavior in `-p` mode
- Opus 4.7 model support
- `ConfigLayer` for layered configuration resolution
- `~/.vibe/prompts` overrides for builtin prompts
- Enable/disable MCP servers and individual tools from `/mcp` menu
- Custom compaction instructions via `/compact`
- `vibe.ready` telemetry event
- Usage updates sent after every LLM turn for ACP
- Headless section in system prompt to prevent bad model behavior

### Changed

- Renamed `auto_approve` config to `bypass_tool_permissions`
- Increased feedback bar frequency with cooldown and TOML cache
- Feedback bar only shown when active model is Mistral
- Centralized telemetry metadata construction and wired through entrypoints
- Preserved stable session identity across compact/fork/rewind
- Filtered remote sessions by current user and deduped continue-as-new
- `--continue` now only looks for sessions of the current working directory
- Batched widget mounts and narrowed CSS selectors for UI performance

### Fixed

- Autocomplete popup height calculation for wrapped lines
- Autocomplete popup dismissed on tab completion and escape
- Double Ctrl+C/Ctrl+D required to quit instead of killing session immediately
- Context window overrun now shows a friendly error message
- `MallocStackLogging` error messages suppressed in CLI input
- `index.lock` leftover on interrupted deferred init
- Safe `find` commands allowed by default
- Session ID preserved when resuming sessions through ACP
- Usage updates sent after tool results instead of tool streams in ACP
- KV cache warming via x-affinity in count tokens


## [2.8.1] - 2026-04-21

### Fixed

- Fixed changelog and whats_new


## [2.8.0] - 2026-04-21

### Added

- Builtin skills system with self-awareness skill
- `cwd` configuration parameter for MCP stdio servers
- `/connectors` as alias for `/mcp` and `R` refresh shortcut in MCP browser
- `MergeFieldMetadata` and annotated merge strategy helpers for config schemas
- `vibe.request_sent` telemetry event fired before each LLM API call
- Model alias to `tool_call_finished` telemetry event

### Changed

- Deferred heavy init in subagents and ACP sessions to background thread
- Renamed `request_sent` telemetry fields and added `nb_prompt_chars`
- Sorted connectors in `/mcp` menu by connection state then alphabetically

### Fixed

- `/debug` command no longer throws
- Race condition in banner initialization dropping initial state

### Removed

- `/terminal-setup` command

## [2.7.6] - 2026-04-16

### Added

- `MergeStrategy` enum and merge logic for configuration
- `call_source=vibe_code` field in LLM request metadata
- "Other" task type for non-code requests in CLI prompt

### Changed

- Parallelized git subprocess calls during startup
- Extracted command registry and refactored skill resolution
- 1M context window and thinking budget max for opus
- Updated default telemetry URL to `api.mistral.ai`

### Fixed

- Markdown fence context loss causing streaming rendering problems
- Proxy chain URLs in `api_base` parsing

### Removed

- Alt+Left / Alt+Right key bindings from chat input

## [2.7.5] - 2026-04-14

### Changed

- Display detected files and LLM risks in trust folder dialog
- Text-to-speech via the Mistral SDK with telemetry tracking
- Deferred MCP and git I/O to background thread for faster CLI startup
- Made telemetry URL configurable
- Bumped Textual to 8.2.1

### Fixed

- Encoding detection fallback in `read_safe` for non-UTF-8 files
- Config saving logic cleanup

## [2.7.4] - 2026-04-09

### Added

- Console View for enhanced debugging and monitoring
- `/mcp` command to display MCP servers and their status
- Manual command output forwarding to agent context

### Changed

- Improved web_fetch content truncation for better readability
- Lazily load heavy dependencies to improve startup time
- Optimized folder parsing at startup using scandir
- Include file name in search_replace result display

### Fixed

- Stale configurations from subagent switch
- ValueError on OTEL context detach in agent_span
- Clipboard toast preview replaced with fixed text
- Only agents with type "agent" are loadable with --agent flag
- Made chat_url nullable in ChatAssistantPublicData
- Normalized OTEL span exporter endpoint
- Removed redundant permission prompts for parallel tool calls needing the same permission
- Removed bottom margin issue in UI
- Never crash before ACP server starts
- Use skill in recent commands via the up-arrow navigation
- Fixed loading order issues in vibe initialization

## [2.7.3] - 2026-04-03

### Added

- `/data-retention` slash command to view Mistral AI's data retention notice and privacy settings

## [2.7.2] - 2026-04-01

### Added

- Alt+Left / Alt+Right keyboard shortcuts for word-wise cursor movement in chat input

### Changed

- Refactored narrator into a dedicated narrator manager

### Fixed

- Broken build on Linux
- Errored MCP servers are now excluded from the banner count
- Improved bash denylist matching and error messages
- Command messages are now skipped during rewind navigation

## [2.7.1] - 2026-03-31

### Added

- ACP message-id support for reliable message boundary identification
- Reasoning effort parameter for supported models

### Changed

- Updated MistralAI SDK
- Updated ACP SDK dependency
- Refined system prompt wording and structure
- Reduced scroll sensitivity to 1 line per tick for smoother scrolling

### Fixed

- Non-standard HTTP 529 status codes now handled gracefully in error formatting and retried
- Text selection errors when copying from unmounting components
- Excluded "injected" field from user messages in generic backend

## [2.7.0] - 2026-03-24

### Added

- Rewind mode to navigate and fork conversation history

### Fixed

- Preserve message_id when aggregating streaming LLM chunks
- Improved error handling for SDK response errors

## [2.6.2] - 2026-03-23

### Changed

- Pinned agent-client-protocol dependency back to 0.8.1

### Removed

- Context usage updates via ACP

## [2.6.1] - 2026-03-23

### Changed

- Loosened agent-client-protocol version constraint from pinned to minimum bound

## [2.6.0] - 2026-03-23

### Added

- OTEL tracing support for observability
- Skill tool for managing task lists and workflows
- Text-to-speech (TTS) functionality
- Standalone --resume command for session picker
- BFS for vibe folders to improve startup performance
- List-based model picker for /model command
- is_user_prompt flag to Mistral metadata header
- Correlation ID in user feedback calls
- Current date added to system prompt in vibe-work
- TypeScript type inference for large tool outputs in vibe-work-harness

### Changed

- Updated agent-client-protocol to 0.9.0a1
- Changed inline code color from yellow to green
- Removed "You have no internet access" from CLI prompt
- Fine-grained permission system improvements
- Inject system certs into vibe-acp frozen binary via truststore

### Fixed

- Streaming for currently streamed message when switching agents
- Proper UI updates when tools switch current agents
- Space key functionality when holding shift
- Empty TextChunk not appended when reasoning has no text content
- Messages removed from user feedback event
- Bash allowlist/denylist activation on Windows
- Improved scrolling performance
- ACP error handling in webview
- Context usage updates sent via ACP
- Include `exit_plan_mode` tool only in plan mode

## [2.5.0] - 2026-03-16

### Added

- Dedicated theorem proving agent powered by leanstral, setup with /leanstall
- More advanced AGENTS.md support:
  - AGENTS.md in ~/.vibe/ folder for user-level agent instructions
  - AGENTS.md for subfolders and in parent folders
- Mistral Code API key info displayed in CLI banner
- Voice mode with real-time transcription support
- Parallel tool execution for improved performance
- Structured ACP error classes for better error handling

### Changed

- Bash allowlist/denylist now active on Windows
- Auto-completion relevance improved with better filename and path matching
- History navigation no longer filters by prefix
- Updated to Mistral SDK v2 import structure
- Removed `find` from bash default allowlist to prevent -exec abuse

### Fixed

- Improved scrolling performance
- Web search tool now infers server URL from provider config

## [2.4.2] - 2026-03-12

### Added

- Session ID included in telemetry events for better tracing

### Changed

- Skills now extract arguments when invoked, improving parameter handling
- Auto-compact threshold falls back to global setting when not defined at model level
- Update notification toast no longer times out, ensuring the user sees the restart prompt
- Removed `file_content_before` from Vibe Code, reducing payload size

## [2.4.1] - 2026-03-10

### Added

- `HarnessFilesManager` for selective loading of harness files, enabling SDK usage without accessing the file system.

### Changed

- Web search tool infers server URL from provider config instead of hardcoded production API
- `ask_user_questions` tool disabled in prompt mode

### Fixed

- Space key fix extended to all `Input` widgets (question prompts, proxy setup) in VS Code terminal
- Ruff isort/formatter config conflict resolved (`split-on-trailing-comma` set to `false`)

## [2.4.0] - 2026-03-09

### Added

- User plan displayed in the CLI banner
- Reasoning effort configuration and thinking blocks adapter

### Changed

- Auto-compact threshold is now per-model
- Removed expensive file scan from system prompt; cached git operations for faster agent switching
- Improved plan mode
- Updated `whoami` response handling with new plan type and name fields

### Fixed

- Space key works again in VSCode 1.110+
- Arrow-key history navigation at wrapped-line boundaries in chat input
- UTF-8 encoding enforced when reading metadata files
- Update notifier no longer crashes on unexpected response fields

## [2.3.0] - 2026-02-27

### Added

- /resume command to choose which session to resume
- Web search and web fetch tools for retrieving and searching web content
- MCP sampling support: MCP servers can request LLM completions via the sampling protocol
- MCP server discovery cache (`MCPRegistry`): survives agent switches without re-discovering unchanged servers
- Chat mode for ACP (`session/set_config_options` with `mode=chat`)
- ACP `session/set_config_options` support for switching mode and model
- Tool call streaming: tool call arguments are now streamed incrementally in the UI
- Notification indicator in CLI: terminal bell and window title change on action required or completion
- Subagent traces saved in `agents/` subfolder of parent session directory
- IDE detection in `new_session` telemetry
- Discover agents, tools, and skills in subfolders of trusted directories (monorepo support)
- E2E test infrastructure for CLI TUI

### Changed

- System prompts rewritten for improved model behavior (3-phase Orient/Plan/Execute workflow, brevity rules)
- Tool call display refactored with `ToolCallDisplay`/`ToolResultDisplay` models and per-tool UI customization
- Middleware pipeline replaces observer pattern for system message injections
- Improved permission handling for `write_file`, `read_file`, `search_replace` (allowlist/denylist globs, out-of-cwd detection)
- Proxy setup UI updated with guided bottom-panel wizard
- Smoother color transitions in CLI loader animation
- Dead tool state classes removed (`Grep`, `ReadFile`, `WriteFile` state)

### Fixed

- Agent switch (Shift+Tab) no longer freezes the UI (moved to thread worker)
- Empty assistant messages are no longer displayed
- Tool results returned to LLM in correct order matching tool calls
- Auto-scroll suspended when user has scrolled up; resumes at bottom
- Retry and timeout handling in Mistral backend (backoff strategy, configurable timeout)

### Removed

## [2.2.1] - 2026-02-18

### Added

- Multiple clipboard copy strategies: OSC52 first, then pyperclip fallback when system clipboard is available (e.g. local GUI, SSH without OSC52)
- Ctrl+Z to put Vibe in background

### Changed

- Improve performance around streaming and scrolling
- File watcher is now opt-out by default; opt-in via config
- Bump Textual version in dependencies
- Inline code styling: yellow bold with transparent background for better readability

### Fixed

- Banner: sync skills count after initial app mount (fixes wrong count in some cases)
- Collapsed tool results: strip newlines in truncation to remove extra blank line
- Context token widget: preserve stats listeners across `/clear` so token percentage updates correctly
- Vertex AI: cache credentials to avoid blocking the event loop on every LLM request
- Bash tool: remove `NO_COLOR` from subprocess env to fix snapshot tests and colored output

## [2.2.0] - 2026-02-17

### Added

- Google Vertex AI support
- Telemetry: user interaction and tool usage events sent to datalake (configurable via `enable_telemetry`)
- Skill discovery from `.agents/skills/` (Agent Skills standard) in addition to `.vibe/skills/`
- ACP: `session/load` and `session/list` for loading and listing sessions
- New model behavior prompts (CLI and explore)
- Proxy Wizard (PoC) for CLI and for ACP
- Proxy setup documentation
- Documentation for JetBrains ACP registry

### Changed

- Trusted folders: presence of `.agents` is now considered trustable content
- Logging handling updated
- Pin `cryptography` to >=44.0.0,<=46.0.3; uv sync for cryptography

### Fixed

- Auto scroll when switching to input
- MCP stdio: redirect stderr to logger to avoid unwanted console output
- Align `pyproject.toml` minimum versions with `uv.lock` for pip installs
- Middleware injection: use standalone user messages instead of mutating flushed messages
- Revert cryptography 46.0.5 bump for compatibility
- Pin banner version in UI snapshot tests for stability

## [2.1.0] - 2026-02-11

### Added

- Incremental load of long sessions: windowing (20 messages), "Load more" to fetch older messages, scroll to bottom when resuming
- ACP support for thinking (agent-client-protocol 0.8.0)
- Support for FIFO path for env file

### Changed

- **UI redesign**: new look and layout for the CLI
- Textual UI optimizations: ChatScroll to reduce style recalculations, VerticalGroup for messages, stream layout for streaming blocks, cached DOM queries
- Bumped agent-client-protocol to 0.8.0
- Use UTC date for timestamps
- Clipboard behavior improvements
- Docs updated for GitHub discussions
- Made the Upgrade to Pro banner less prominent

### Fixed

- Fixed inaccurate token count in UI in some cases
- Fixed agent prompt overrides being ignored
- Terminal setup: avoid overwriting Wezterm config

### Removed

- Legacy terminal theme module and agent indicator widget
- Standalone onboarding theme selection screen (replaced by redesign)

## [2.0.2] - 2026-01-30

### Added

- Allow environment variables to be overridden by dotenv files
- Display custom rate limit messages depending on plan type

### Changed

- Made plan offer message more discreet in UI
- Speed up latest session scan and harden validation
- Updated pytest-xdist configuration to schedule single test chunks

### Fixed

- Prevent duplicate messages in persisted sessions
- Fix ACP bash tool to pass full command string for chained commands
- Fix global agent prompt not being loaded correctly
- Do not propose to "resume" when there is nothing to resume

## [2.0.1] - 2026-01-28

### Fixed

- Fix encoding issues in Windows

## [2.0.0] - 2026-01-27

### Added

- Subagent support
- AskUserQuestion tool for interactive user input
- User-defined slash commands through skills
- What's new message display on version update
- Auto-update feature
- Environment variables and timeout support for MCP servers
- Editor shortcut support
- Shift+enter support for VS Code Insiders
- Message ID property for messages
- Client notification of compaction events
- debugpy support for macOS debugging

### Changed

- Mode system refactored to Agents
- Standardized managers
- Improved system prompt
- Updated session storage to separate metadata from messages
- Use shell environment to determine shell in bash tool
- Expanded user input handling
- Bumped agent-client-protocol to 0.7.1
- Refactored UI to require AgentLoop at VibeApp construction
- Updated README with new MCP server config
- Improved readability of the AskUserQuestion tool output

### Fixed

- Use ensure_ascii=False for all JSON dumps
- Delete long-living temporary session files
- Ignore system prompt when saving/loading session messages
- Bash tool timeout handling
- Clipboard: no markup parsing of selected texts
- Canonical imports
- Remove last user message from compaction
- Pause tool timer while awaiting user action

### Removed

- instructions.md support
- workdir setting in config file

## [1.3.5] - 2026-01-12

### Fixed

- bash tool not discovered by vibe-acp

## [1.3.4] - 2026-01-07

### Fixed

- markup in blinking messages
- safety around Bash and AGENTS.md
- explicit permissions to GitHub Actions workflows
- improve render performance in long sessions

## [1.3.3] - 2025-12-26

### Fixed

- Fix config desyncing issues

## [1.3.2] - 2025-12-24

### Added

- User definable reasoning field

### Fixed

- Fix rendering issue with spinner

## [1.3.1] - 2025-12-24

### Fixed

- Fix crash when continuing conversation
- Fix Nix flake to not export python

## [1.3.0] - 2025-12-23

### Added

- agentskills.io support
- Reasoning support
- Native terminal theme support
- Issue templates for bug reports and feature requests
- Auto update zed extension on release creation

### Changed

- Improve ToolUI system with better rendering and organization
- Use pinned actions in CI workflows
- Remove 100k -> 200k tokens config migration

### Fixed

- Fix `-p` mode to auto-approve tool calls
- Fix crash when switching mode
- Fix some cases where clipboard copy didn't work

## [1.2.2] - 2025-12-22

### Fixed

- Remove dead code
- Fix artefacts automatically attached to the release
- Refactor agent post streaming

## [1.2.1] - 2025-12-18

### Fixed

- Improve error message when running in home dir
- Do not show trusted folder workflow in home dir

## [1.2.0] - 2025-12-18

### Added

- Modular mode system
- Trusted folder mechanism for local .vibe directories
- Document public setup for vibe-acp in zed, jetbrains and neovim
- `--version` flag

### Changed

- Improve UI based on feedback
- Remove unnecessary logging and flushing for better performance
- Update textual
- Update nix flake
- Automate binary attachment to GitHub releases

### Fixed

- Prevent segmentation fault on exit by shutting down thread pools
- Fix extra spacing with assistant message

## [1.1.3] - 2025-12-12

### Added

- Add more copy_to_clipboard methods to support all cases
- Add bindings to scroll chat history

### Changed

- Relax config to accept extra inputs
- Remove useless stats from assistant events
- Improve scroll actions while streaming
- Do not check for updates more than once a day
- Use PyPI in update notifier

### Fixed

- Fix tool permission handling for "allow always" option in ACP
- Fix security issue: prevent command injection in GitHub Action prompt handling
- Fix issues with vLLM

## [1.1.2] - 2025-12-11

### Changed

- add `terminal-auth` auth method to ACP agent only if the client supports it
- fix `user-agent` header when using Mistral backend, using SDK hook

## [1.1.1] - 2025-12-10

### Changed

- added `include_commit_signature` in `config.toml` to disable signing commits

## [1.1.0] - 2025-12-10

### Fixed

- fixed crash in some rare instances when copy-pasting

### Changed

- improved context length from 100k to 200k

## [1.0.6] - 2025-12-10

### Fixed

- add missing steps in bump_version script
- move `pytest-xdist` to dev dependencies
- take into account config for bash timeout

### Changed

- improve textual performance
- improve README:
  - improve windows installation instructions
  - update default system prompt reference
  - document MCP tool permission configuration

## [1.0.5] - 2025-12-10

### Fixed

- Fix streaming with OpenAI adapter

## [1.0.4] - 2025-12-09

### Changed

- Rename agent in distribution/zed/extension.toml to mistral-vibe

### Fixed

- Fix icon and description in distribution/zed/extension.toml

### Removed

- Remove .envrc file

## [1.0.3] - 2025-12-09

### Added

- Add LICENCE symlink in distribution/zed for compatibility with zed extension release process

## [1.0.2] - 2025-12-09

### Fixed

- Fix setup flow for vibe-acp builds

## [1.0.1] - 2025-12-09

### Fixed

- Fix update notification

## [1.0.0] - 2025-12-09

### Added

- Initial release
