# 0014 Backend Contract Compatibility

## Decision

The Vibe client must stay compatible with the range of Mistral backend versions
it can run against. Self-hosted installs upgrade the client and the backend
independently, so the client cannot assume a specific backend version.

Wire-contract changes are additive and tolerant by default:

- Parsing a backend response ignores unknown fields and tolerates missing ones.
- A field the client newly depends on has a safe default or a capability/version
  gate; it is never a hard requirement.
- One malformed item is skipped, not fatal to the whole payload.
- A change that cannot stay backward compatible is flagged in the PR under
  "Self-hosted compatibility" with the skew window and fallback, and needs
  sign-off. It is never landed silently.

## Rationale

Client and backend versions drift across deployments, so a strict client breaks
against a backend that is older or newer than it.

## Agent Guidance

- Never add a required, no-default field to a response model; default or gate it.
- Skip bad items per-item; do not let one fail the whole payload.
- Keep parsing and any local cache tolerant of both old and new shapes.
- Add a parser test for the older-backend shape (field absent).

## Flag To User When

- A change makes a response field required, or fails parsing when it is absent.
- A new behavior needs a backend field with no default and no gate.
- A wire-contract change cannot be made backward compatible.
