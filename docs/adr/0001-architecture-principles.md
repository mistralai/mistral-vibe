# 0001 Architecture Principles

## Decision

Optimize for fast startup, responsive interaction and changes that remain local.
Keep dependencies explicit and I/O at module boundaries. Add an abstraction
only when it protects a real boundary, removes duplication or enables required
testing. Ownership is defined in [0002](0002-core-engine-and-delivery-surfaces.md).

## Rationale

Startup work and broad dependencies affect every user. Small, understandable
modules limit the cost of changing or replacing an implementation.

## Agent Guidance

- Start with the smallest change in the owning module.
- Keep parsing and decisions independent of I/O where practical.
- Defer optional imports, discovery and network work until needed.
- Use existing contracts before adding layers or dependencies.

## Flag To User When

- A dependency adds substantial startup or per-turn work.
- A shortcut spreads one behavior across unrelated modules or clients.
