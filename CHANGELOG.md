# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- Improved readability of the AskUserQuerstion tool output

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
