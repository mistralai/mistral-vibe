---
name: collaboration-multi-agent
description: Conversation branches, shared sessions, delegation to specialized sub-agents
license: Apache-2.0
user-invocable: true
allowed-tools:
  - read_file
  - grep
  - bash
  - ask_user_question
---

# Plugin Collaboration & Multi-agent

Conversation branches, pair programming via shared session, sub-agent orchestration.

## Sub-features

### Branching conversations
Fork a session at a point: explore 2 approaches in parallel without losing either.

### Shared sessions
Share URL to join a live session. Pair programming, remote support, live review.

### Subagent specialization
The plan agent delegates to specialists (code, test, review) in parallel — beyond the task tool.

## Usage

- `vibe "/plugin collaboration-multi-agent"` — explain collaboration modes
- `vibe "/plugin collaboration-multi-agent Fork here for approach B"`
- `vibe "/plugin collaboration-multi-agent Share this session"`
