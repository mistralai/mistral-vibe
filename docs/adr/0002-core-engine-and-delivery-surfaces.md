# 0002 Ownership Boundaries

## Decision

Vibe uses Unified Harness Core and Runtime outside `vibe.app_server`.

| Owner | Responsibility |
| --- | --- |
| Harness Core | Deterministic context, legal transitions and Action planning. |
| Runtime | Content, execution, ordering, cancellation, approval enforcement, persistence and recovery of one session. |
| App server | Product capabilities, resolved configuration, integrations, public projections, session hosting and cross-session coordination. |
| Clients | Presentation, input, navigation and explicitly client-local facilities. |

Runtime exposes typed session operations and generic capability bindings. It
invokes supplied handlers without importing app-server modules. Adapters map
resolved product inputs and dependency-neutral errors at that boundary.
`vibe.core` retains reusable utilities; it is not a second execution owner.

Each child has its own session. Hosting owns the catalogue, creation, routing
and process resources; a session retains its direct child links and results.
Runtime-private checkpoints and journals stay behind the session API. Features
must not create competing execution stores, accepted-work queues or execution
schedulers.

These boundaries guide new work. Existing product behavior and hosting inside
Runtime are migration work; current placement does not authorize more coupling.
Feature-module rules are in [0018](0018-modular-app-server-capabilities.md).

## Rationale

One execution owner keeps session state and recovery coherent. Separate product
and hosting contracts allow features to change independently.

## Agent Guidance

- Identify the owner of each state value and side effect before changing it.
- Use session capabilities while preserving leases, idempotency and cleanup.
- Keep execution-relevant feature state within existing persistence capabilities.

## Flag To User When

- A change creates two authoritative owners for execution or durable state.
- A proposed boundary exposes private engine or storage details.
