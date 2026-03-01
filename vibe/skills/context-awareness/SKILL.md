---
name: context-awareness
description: New context sources — screen, clipboard, git diff, terminal pipe, browser
license: Apache-2.0
user-invocable: true
allowed-tools:
  - read_file
  - grep
  - analyze_design
  - bash
  - web_search
  - ask_user_question
---

# Plugin Context Awareness

Enriches context with additional sources: screen, clipboard, git diff, terminal pipe, active web page.

## Sub-features

### Screen capture
The AI can see the screen via captures (auto or on demand). If an image is provided, use `analyze_design` to interpret it.

### Clipboard watcher
Copied content (code, URL, error, text) → contextual actions: explain, format, translate, fix.

### Git diff live
Inject `git diff` and `git status` into context. Review changes in progress, suggest improvements.

### Terminal pipe
`command | vibe [prompt]` — piped output is the context. E.g.: `git log -5 | vibe "Summarize"`, `cat error.log | vibe "Diagnose"`.

### Browser companion
Extension that shares the active page. Without extension: ask for URL or snippet, use `web_search` if relevant.

## Usage

- `vibe "/plugin context-awareness"` — activate or explain context sources
- `vibe "/plugin context-awareness Review my git diff"`
- `command | vibe "Analyze this output"`
