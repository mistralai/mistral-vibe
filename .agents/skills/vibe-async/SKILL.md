---
name: vibe-async
description: Async and concurrency patterns for Mistral Vibe. Use when working with asyncio, the agent loop, Textual TUI threading, HTTP clients, streaming surfaces, or core-to-TUI communication.
metadata:
  display-name: Vibe Async
  short-description: Async and concurrency patterns for Vibe
  default-prompt: Use $vibe-async to follow Vibe async conventions when working with concurrent or event-loop code.
---

# Vibe Async Patterns

Conventions for async and concurrent code in Vibe. Apply when touching the agent loop, Textual event loop, HTTP clients, streaming, or any `async` code.

## Runtime

- `asyncio` is the orchestration runtime in the agent loop and tool execution. Use `asyncio.create_task` + queues for concurrent work, not blanket `gather`.

## Blocking work

- Never run CPU-heavy or I/O-bound code on the UI thread. The Textual TUI and the agent loop share one event loop, so anything blocking (large JSON/Pydantic serialization, `os.fsync`, subprocess calls, recursive globs) freezes the UI — offload it with `asyncio.to_thread`.
- Async file wrappers don't make blocking syscalls non-blocking.
- Use `anyio.Path` for file I/O on async paths.

## Streaming

- Streaming surfaces return `AsyncGenerator[Event, None]`, not coroutines.

## Core-to-TUI communication

- Route core-to-TUI communication through `vibe.app_server`. Server requests such as approvals and user input become canonical Vibe events; neither the TUI nor app server may register callbacks, listeners, or message observers directly on the agent loop.

## HTTP clients

- When Vibe owns an HTTP client, use `VibeAsyncHTTPClient` from `vibe.utils.http` instead of `httpx.AsyncClient` so proxy env vars are handled consistently.
- Its CIDR `NO_PROXY` matching applies only to IP-literal request hosts; do not resolve DNS before proxy selection.
- Mock outbound HTTP with `respx` in tests.
