# 0006 Local Sessions

## Decision

Execution restoration uses Runtime-private durable state. Public snapshots and
events are projections, never restore formats. The future-turn queue is
currently process-local; session durability does not persist queued prompts.

Compaction preserves session identity and the complete public transcript,
appending a checkpoint while replacing model context. Rewind explicitly
chooses a new fork that preserves the source, or in-place history truncation.
Never infer destructive rewind from an omitted option. File restoration is a
separate choice. Metadata replacement remains atomic.

Historical Vibe 2.0+ folders (`meta.json`, `messages.jsonl`) import validated
committed history into an independent Unified session with provenance. The
importer never executes stored tools, restores source checkpoints or modifies
the source. Vibe 1.x single-JSON sessions are unsupported.

## Rationale

Explicit durability and destructive choices protect history while allowing
private recovery formats to evolve independently of client projections.

## Agent Guidance

- Define feature-state behavior for resume, fork, rewind and cleanup.
- Preserve source history on import and fork.
- Test durable recovery through session operations, including failure paths.
- Keep ordinary history writes append-friendly outside explicit truncation.

## Flag To User When

- A change breaks supported resume/import or silently discards history.
- Compaction changes identity or rewrites earlier public transcript entries.
- Recovery would promise exactly-once external effects beyond its guarantees.
