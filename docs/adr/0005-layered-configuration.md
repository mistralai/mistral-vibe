# 0005 Layered Configuration

## Decision

`VibeConfigSchema` defines one validated effective configuration. Fields have
explicit defaults, validation and merge strategies. Product layers and
experiment assignments resolve before values reach Runtime/Core.

Keep persisted preferences, trusted project policy and session overrides
distinct. Admin policy retains its enforced precedence. Untrusted project
configuration does not participate in the merge. Forks and children receive
independent session configuration.

Writes validate prospective configuration and use atomic replacement for each
TOML file. Multiple target layers are not one transaction. Public `ConfigView`
is redacted and does not define writable paths. The
[configuration reference](../reference/configuration.md) describes the layer
stack, model pins, mutation application and API.

## Rationale

Deterministic resolution preserves user intent and enforced policy without
making clients understand private config objects or persistence.

## Agent Guidance

- Add fields with explicit merge behavior and compatible persisted defaults.
- Map experiments through config layers instead of branching on assignments.
- Distinguish an accepted write from its application to active execution.
- Return authoritative public state and keep secrets out of projections.

## Flag To User When

- A write needs atomicity across layers, provenance or conflict semantics the
  current configuration substrate cannot provide.
- A change alters enforced precedence or treats untrusted config as authoritative.
