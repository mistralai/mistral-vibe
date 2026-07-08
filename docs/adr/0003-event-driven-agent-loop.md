# 0003 Event Driven Agent Loop

## Decision

The agent loop communicates through typed events and streaming async generators. The loop owns orchestration; consumers react to events.

Events are the contract for assistant output, reasoning, user messages, tool calls, tool streams, tool results, approvals, compaction, plan review, title updates, hooks, teleport, and related lifecycle changes.

## Rationale

Streaming keeps the CLI responsive and lets ACP/programmatic consumers observe the same underlying process. Typed events keep UI rendering, protocol translation, logging, and tests from reaching into agent-loop internals.

## Agent Guidance

- Prefer adding or extending typed events over adding surface-specific callbacks into the agent loop.
- Keep event payloads small, serializable where practical, and meaningful outside one UI.
- Use `asyncio.create_task` and queues for explicit concurrent flows; avoid hiding orchestration in broad `gather` calls.
- Keep long-running work cancellable and make cancellation visible through existing event/result paths.
- Do not make consumers inspect private agent-loop state to understand what happened.

## Flag To User When

- A feature requires the UI or ACP layer to read or mutate private loop state.
- A new event is useful for one surface but would be confusing or impossible for another surface to consume.
- A change blocks streaming responsiveness or delays visible feedback until a whole turn completes.
