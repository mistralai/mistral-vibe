# 0006 Local Sessions

## Decision

Sessions are durable local records of conversation state, metadata, tool availability, stats, and resumability data.

Session persistence should be append-friendly for messages, atomic for metadata, tolerant of old transcript shapes through migrations, and independent of one delivery surface.

## Rationale

Users rely on resume, rewind, titles, transcript inspection, and continuity across runs. Session files are also a boundary between current code and older Vibe versions, so changes must be conservative.

## Agent Guidance

- Persist messages and metadata through the session layer, not directly from UI code.
- Keep session data serializable and migration-friendly.
- Treat old transcript formats as real inputs unless a migration intentionally drops support.
- Do not store surface-only widget state in core session transcripts.
- Keep image/session attachment behavior explicit about what is persisted and what remains memory-only.

## Flag To User When

- A change breaks existing session resume or requires users to discard old transcripts.
- UI state is being added to core transcript data.
- Metadata updates are no longer atomic or messages are no longer append-safe.
