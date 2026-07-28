# 0006 Local Sessions

## Decision

Sessions are durable local records of conversation state, metadata, tool availability, stats, and resumability data.

Session persistence should be append-friendly for messages, atomic for metadata, tolerant of old transcript shapes through migrations, and independent of one delivery surface.

Private session storage and the public app-server projection are different
contracts. Only the server reads or writes session files. Clients receive a
lossy `PublicSessionState`, page public history through opaque cursors, and use
stable session, turn, entry, callback, effect, and child-session IDs. Public
events are not a persistence format.

The current local format restores completed transcript state, metadata,
statistics, and persisted child links. A reconnect to the same live harness can
recover its snapshot and open callbacks; a new process does not restore an
in-flight turn, open callback future, or live event sequence from JSONL. Do not
present live reconnect behavior as crash recovery.

## Rationale

Users rely on resume, rewind, titles, transcript inspection, and continuity across runs. Session files are also a boundary between current code and older Vibe versions, so changes must be conservative.

## Agent Guidance

- Persist messages and metadata through the session layer, not directly from UI code.
- Route list, read, resume, continue, fork, rewind, clear, compact, rename,
  delete, and history operations through app-server session resources.
- Keep session data serializable and migration-friendly.
- Treat old transcript formats as real inputs unless a migration intentionally drops support.
- Do not store surface-only widget state in core session transcripts.
- Keep image/session attachment behavior explicit about what is persisted and what remains memory-only.
- Treat compaction and context-clear session replacement as an explicit handoff:
  atomically adopt the returned session ID, public state, event watermark, and
  session-log summary.
- Treat rewind and clear as replacement-session operations when they return a
  new identity. The replacement may derive from an earlier history prefix, but
  the original stored session remains intact.
- Represent subagents as linked child sessions. Do not embed a live child
  runtime in a public parent model.

## Flag To User When

- A change breaks existing session resume or requires users to discard old transcripts.
- UI state is being added to core transcript data.
- Metadata updates are no longer atomic or messages are no longer append-safe.
- A client needs to read `messages.jsonl`, session metadata, or private loader
  APIs to render or control a session.
