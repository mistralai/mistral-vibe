# 0007 Extension Mechanisms

## Decision

Reuse explicit tool, hook, skill, plugin, MCP and connector contracts. Discovery
is deterministic and lazy. Extensions have declared invocation, failure and
cleanup lifecycles; malformed optional extensions report diagnostics without
breaking unrelated features when safe.

Reserve built-in names and define override policy explicitly. Preserve adopted
external standards and intentional import adapters. Plugin containment,
content identity and restore-lock guarantees remain binding. New product
capabilities follow [0018](0018-modular-app-server-capabilities.md).

## Rationale

Predictable discovery and isolated failures let users extend Vibe without
adding hidden startup work or destabilizing unrelated capabilities.

## Agent Guidance

- Reuse generic invocation and materialization mechanisms.
- Bound hook and external-process execution with timeouts and cleanup.
- Keep authentication and configuration failures explicit.
- Validate package paths and preserve supported external schemas.

## Flag To User When

- An extension requires another discovery or invocation mechanism.
- Project extensions override built-ins implicitly.
- Integration support requires partial emulation of an unsupported contract.
