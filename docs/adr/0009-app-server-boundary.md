# 0009 App Server Client Boundary

## Decision

All attached delivery clients use typed, serialized app-server JSON-RPC,
including in-process calls. Clients receive public values, never private
Runtime/Core objects, managers or storage records.

Public concepts have one definition. Redaction, aggregation or lifecycle
changes justify projections; translation stays in server-only adapters.
Private execution events, public events and persistence records remain distinct.
Streaming exposes progress during work; cancellation produces explicit outcomes
and preserves the declared meaning of accepted and discarded output.

Mutations return authoritative values. Attachment establishes snapshot,
watermark and live route atomically. Clients ignore duplicate event IDs and
replace state after gaps. Handoffs replace identity, projection and watermark
together. Queue editing and queued steering commit independently of response
delivery: updates may precede the response, and a lost response does not undo
acceptance. Other accepted responses precede notifications caused by the request.

Callbacks and client-tool requests require advertised capabilities and validated
correlation. Callback delivery acknowledgement differs from the semantic
`callback/result`. Detachment, shutdown and durable restoration have distinct
lifecycles. Detachment abandons unfinished queued edits without undoing accepted
Save or Cancel operations. Detailed behavior is in the
[protocol reference](../reference/app-server-protocol.md).

## Rationale

A value boundary gives clients consistent behavior and recovery without
coupling them to implementation identity or private execution formats.

## Agent Guidance

- Keep protocol models independent of private execution types.
- Preserve serialized in-process and stdio coverage.
- Make long-running operations cancellable and bound transport work.
- Test event gaps, callback retries, handoffs and cleanup failures.

## Flag To User When

- A mutation cannot return authoritative state or a clear terminal outcome.
- A change weakens ordering, redaction or callback validation.
