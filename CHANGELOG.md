# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
