---
name: automatisation-triggers
description: Scheduled tasks, CI/GitHub webhooks, file watch, skill macros
license: Apache-2.0
user-invocable: true
allowed-tools:
  - read_file
  - grep
  - bash
  - search_replace
  - write_file
  - ask_user_question
---

# Plugin Automatisation & Triggers

Scheduled tasks, webhooks, file watching, macros.

## Sub-features

### Scheduled tasks
`vibe --at 09:00 "check PRs"` — run at fixed time (cron/launchd).

### Webhook triggers
Trigger a session from CI failure, GitHub push, Slack. HTTP endpoint + event → prompt mapping.

### Watch mode
`vibe --watch src/` — monitor files, react to changes (analysis, lint, suggestions).

### Macro skills
Chain multiple skills into one shortcut. E.g.: `full-audit` = ui-ux audit + accessibility + design-system.

## Usage

- `vibe "/plugin automatisation-triggers"` — explain options
- `vibe --at 09:00 "check open PRs"`
- `vibe --watch src/ "analyze each change"`
