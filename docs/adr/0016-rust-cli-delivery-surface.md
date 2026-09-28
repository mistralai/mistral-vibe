# 0016 Rust CLI Delivery Surface

## Decision

The Rust `vibe-rs` binary is a delivery surface for Vibe, parallel to the
Python Textual CLI under [0002](0002-core-engine-and-delivery-surfaces.md). It is
a thin client over the Python `vibe-app-server` ([0009](0009-app-server-boundary.md)),
connected by newline-delimited JSON-RPC 2.0 on stdio. It never imports Python
internals or duplicates the agent runtime.

The application follows a strict unidirectional `Action -> reduce(&mut AppStore)
-> Vec<Effect>` loop. The reducer is pure: no I/O, no Ratatui, no Crossterm, no
raw JSON. One `vibe-rs` single crate holds the binary. Dependencies point one
way: `main -> app -> { server, application, ui, startup }`. The `application`
package imports nothing from `ui` and no terminal library types.

This boundary is independent of [0011](0011-unified-harness-backend.md): the Rust
client targets the app-server protocol, not a specific session backend.

## Rationale

Rust gives a single static binary, predictable startup, and a memory-safe
immediate-mode TUI (Ratatui/Crossterm) that fits the unidirectional state model.
A concrete protocol client and declared bounds keep the single adapter honest.
Detail lives in the [architecture design doc](../design/rust-cli-architecture.md).

## Agent Guidance

- Keep the reducer pure; effects are the only side-effect surface. Route all
  input through one bounded event channel.
- Add new app-server methods to the concrete client only when a wire operation
  is required; do not add a trait for a hypothetical adapter.
- Declare a bound for every new queue, retained collection, frame, log, or
  cache; document overflow behavior.
- Do not render the full transcript per frame; use the viewport-bounded window.
- Do not add speculative abstractions (clipboard, selection, fold, shared
  widget abstractions) without a second consumer.
- Preserve first-draw-before-spawn and RAII terminal restoration on all exit
  paths.

## Flag To User When

- A change introduces shared mutable application state (`Arc<Mutex<App>>`) or a
  second session/configuration database in the Rust client.
- A change adds an `AppServerPort` trait or second adapter without a real second
  transport.
- A change adds an unbounded channel or renders the full transcript per frame.
- The Rust client needs to import Python internals or duplicate the agent
  runtime.
