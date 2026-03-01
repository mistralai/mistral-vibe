---
name: memory-persistence
description: Persistent memory, VIBE.md project file, local knowledge base (RAG)
license: Apache-2.0
user-invocable: true
allowed-tools:
  - read_file
  - write_file
  - grep
  - ask_user_question
---

# Plugin Memory & Persistence

Memory across sessions, project context, personal knowledge base.

## Sub-features

### Persistent memory (~/.vibe/memory.md)
- Load at session start for preferences, decisions, memorized facts
- Propose saving important information
- Personalize responses without re-asking

### Project context file (VIBE.md)
- CLAUDE.md equivalent: conventions, stack, structure
- Auto-generate if missing (repository analysis)
- Update when conventions change

### Local embeddings (RAG)
- Indexed personal knowledge base
- Semantic search to enrich context
- Index notes, docs, snippets

## Usage

- `vibe "/plugin memory-persistence"` — activate or consult memory
- `vibe "/plugin memory-persistence Remember that I prefer TypeScript"`
- `vibe "/plugin memory-persistence Generate VIBE.md for this project"`
