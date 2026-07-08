# 0001 Architecture Principles

## Decision

Vibe should evolve toward pragmatic hexagonal architecture: core behavior depends on stable models and ports, while UI, filesystem, network, provider SDKs, subprocesses, and protocols live at the edges.

The code should optimize for:

- Fast startup and responsive interactive use.
- Minimal, simple changes that fit the existing boundary.
- Limited blast radius when a bug or feature changes.
- Replaceable modules where integrations, transports, or providers may change.
- Clear ownership of side effects.

## Rationale

Vibe is an interactive CLI agent. Slow startup, broad edits, and tangled boundaries are user-visible. Architecture should make the common path fast and make change local.

Ports are useful when they protect a real boundary or make tests simpler. They are not required for every small helper.

## Agent Guidance

- Keep orchestration, state transitions, and domain models in `vibe/core`.
- Put adapters for Textual, ACP, HTTP, files, subprocesses, provider SDKs, and local platform behavior outside the pure decision-making path when practical.
- Prefer small edits in one owning module over scattered updates across many files.
- Add an abstraction only when it reduces coupling, replaces duplication, or matches an existing boundary.
- Preserve startup time: avoid eager imports, eager network calls, broad filesystem scans, and heavyweight initialization on the launch path.

## Flag To User When

- A change is easy in the current code only by coupling two surfaces or spreading behavior across many files.
- A new dependency would run on startup, global import, or every turn without a clear need.
- A shortcut makes future replacement of a provider, tool, UI, or storage layer harder.
