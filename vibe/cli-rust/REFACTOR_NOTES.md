# Refactor Notes

Living tracker for the **in-place refactor** of the PoC in `vibe/cli-rust/`
toward the target in [rust-cli-architecture.md](../../docs/design/rust-cli-architecture.md),
governed by [rust-cli-implementation-contract.md](../../docs/design/rust-cli-implementation-contract.md),
with rationale in the same architecture design doc. The architecture rule is
[ADR 0016](../../docs/adr/0016-rust-cli-delivery-surface.md).

The PoC is a working, near-full-parity TUI (126 client-e2e parity scenarios
passing, 6 skipped, 0 failed) that substantially deviates from the target
architecture. The docs above are **normative target**, not a description of the
current code. This file records the gap and tracks convergence.

## Current state (HEAD)

HEAD merged `origin/poc_rust_rewrite` on top of an earlier structural refactor.
The earlier refactor's "single crate, `lib.rs` exposes only `pub mod server`"
state no longer holds: `src/lib.rs` now exposes 47 `pub mod`s (full PoC surface).
Single-crate and both binaries (`vibe-rs`, `vibe-replay`) are preserved.

## Gap against target invariants

Evidence is against current `vibe/cli-rust/` source at HEAD.

| Invariant (target) | Status | Current evidence |
|---|---|---|
| `Action -> reduce(&mut AppStore) -> Vec<Effect>` | NOT met | `event_handler.rs` takes `&Arc<Client>`, does I/O inline (`submission::start_queued_agent_turn`, `config::load`) |
| Pure reducer (no I/O, no Ratatui, no channels, no raw JSON) | NOT met | `app.rs` imports `ratatui::{Frame, layout::Rect}`, `mpsc::{Sender, UnboundedSender}`; raw `serde_json::Value` in `event_handler.rs` |
| `application/` package; `server/codec.rs` typed `ServerEvent` | NOT met | No `application/` dir; no `codec.rs`; notifications reduced as raw `Value` |
| One `ui/input.rs` key seam; app never sees `KeyEvent` | NOT met | `KeyEvent` flows to `input.rs`, `config.rs`, `resume_picker.rs`, `mcp/`, `question_input.rs` |
| 3-party runtime (input OS thread + server task + main loop), one bounded event channel | PARTIAL | Input OS thread DONE: `src/input_thread.rs` (blocking `poll(100ms)` + `AtomicBool` stop flag, `blocking_send` over bounded cap-256 channel, RAII stop->close->join on `Drop`); `EventStream` + `event-stream` feature removed. Server task owning `Client` + single unified event channel still NOT met (`Client` is `Arc<Client>` across 15 files; `event_loop/steady.rs` still owns the multi-source `tokio::select!`) |
| All channels bounded | PARTIAL | `CommandEvent` (cap 16) and `VoiceEvent` (cap 256) now use bounded `mpsc::Sender` + `try_send` (drop on full); the audio-chunks channel (`voice/transcribe.rs`) is still unbounded — deferred |
| Current-thread runtime | MET | `#[tokio::main(flavor = "current_thread")]` + `tokio` `rt` feature (no `rt-multi-thread`) in `Cargo.toml`/`main.rs`; e2e 132 passed |
| One-in-flight request; reject non-matching response ID | NOT met | `HashMap<u64, oneshot::Sender>` pending allows multiple; non-matching ID silently dropped (`server/process.rs`) |
| `cli.rs` clap types | MET | `src/cli.rs` (`Cli` with validated `--workdir`); cwd changes before terminal startup in `main.rs`; unknown args (e.g. `-p`) rejected before terminal |
| `AppStore` flat shape with `dirty`/`busy` | NOT met | `App` mixes pure state, `Rect`, channels; `dirty` is a local in `event_loop/steady.rs` |
| Strict `MAX_FRAME` before-exceed | PARTIAL | 16 MiB cap enforced post-read (`server/process.rs`); over-allocates one line before rejecting |
| `SHUTDOWN_GRACE` stdin-close-then-kill | MET | `ChildHandle::wait_with_grace` (`server/child.rs`); `session/stop` in `event_loop::run` |
| Single crate; concrete `ProcessAppServer` | MET | `Cargo.toml`; `server/process.rs` |
| First-draw-before-spawn | MET | draw before spawn in `main.rs` |
| RAII `TerminalGuard` Drop restores | MET | `terminal.rs` |
| `VIBE_STARTUP_TIMINGS` milestones | MET | `startup.rs`, `main.rs` |
| `MAX_FRAME` / `MAX_PENDING_NOTIFICATIONS` declared | MET | constants in `server/process.rs` (post-read; backlog via channel cap) |
| `vibe-replay` second binary | MET | `[[bin]]` in `Cargo.toml` |

## Refactor plan (in-place, phases)

Order: entrypoints + runtime backbone first, then server edge, then
application purity, then UI leaves. Each phase is a vertical slice, one PR,
with the contract and this file updated in the same PR. The 126-scenario e2e
parity suite stays green as the regression net; fixture/ordering assumptions
are fixed when the reroute changes timing.

### Phase A — Entrypoints and runtime backbone
- A1 `cli.rs` clap `Cli` (`--workdir`, optional initial prompt; reject `--prompt`/`-p`
  before terminal). `main.rs` becomes parse + `app::run`. Fix `cli_parsed`
  ordering (record before `terminal::init()`). — DONE
- A2 `app.rs` as composition root: RAII terminal -> first draw -> spawn ->
  initialize -> session/start -> steady loop. Add first-draw-before-spawn
  `TestBackend` ordering test. — DEFERRED to the A3-full PR: extracting
  `app::run` with a `TestBackend`-parameterizable terminal is invasive while
  `event_loop.rs` takes `ratatui::DefaultTerminal` and `App` is a 581-line
  state struct (the `App`→`AppStore` split is Phase C). First-draw-before-spawn
  is already met in `main.rs`.
- A3 Current-thread runtime + 3-party shape: drop `rt-multi-thread`; input OS
  thread (blocking `crossterm::poll()` + 100 ms stop flag), server task owning
  `Client`, main loop owning `AppStore` + terminal. Introduce the one bounded
  event channel (cap 256). — PARTIAL: current-thread runtime DONE; input OS
  thread DONE (`src/input_thread.rs`: dedicated OS thread, blocking
  `crossterm::event::poll(100ms)` + `AtomicBool` stop flag, `blocking_send`
  over a bounded `mpsc::channel(EVENT_CHANNEL_CAP=256)`, RAII `InputThread`
  with stop -> close receiver -> join on `Drop`; `EventStream` and the
  `crossterm` `event-stream` feature removed). Remaining: the server task
  owning `Client` (today `Client` is `Arc<Client>` shared across 15 files) and
  the single unified event channel are a top-to-bottom rewrite of the rest of
  `event_loop/steady.rs` (still a multi-source select). That is the highest-risk change for
  e2e timing; it unblocks A2 and A5 and should land as its own focused PR
  with full e2e validation.
- A4 Bound every channel: replace `unbounded_channel` for `CommandEvent` and
  `VoiceEvent`; declare capacities and overflow behavior. — DONE for those
  two (cap 16 / 256, drop on full). The audio-chunks channel
  (`voice/transcribe.rs`) is still unbounded; it is internal to the recording
  pipeline and is deferred.
- A5 Graceful shutdown: `ServerCommand::Shutdown` -> close stdin ->
  `SHUTDOWN_GRACE=2s` -> kill/reap. Order: input stop flag -> Shutdown -> await
  server task -> join input thread -> `TerminalGuard` Drop. Test saturated
  event/command channel shutdown. — DONE: `session/stop` sent in `event_loop::run`
  after `steady()` returns; `ChildHandle::wait_with_grace` (2s timeout -> SIGKILL
  to the child's whole process group, `server/child.rs`)
  in `main.rs` after the event loop drops; `kill_on_drop(true)` + the group kill
  in `ChildHandle`'s `Drop` remain as the panic/crash safety net. OS signal handlers (SIGTERM/SIGHUP/SIGINT) trigger the
  same exit path. SIGTSTP (Ctrl+Z) is detected by `input::request_suspend` before
  overlay dispatch and handled by `TerminalGuard` in `event_loop/steady.rs`. App-server
  crash surfaced via `watch::channel` from reader task to `steady()` select.

### Phase B — Server edge
- B1 `server/codec.rs`: `parse_event` raw notification -> typed `ServerEvent`;
  reject mixed append/replace; preserve `Append`/`Replace` op kind.
- B2 One-in-flight + reject non-matching ID as protocol error (replace
  `HashMap` pending). — INTERDEPENDENCY: `whoami` (`commands/simple.rs`) fires
  two concurrent requests via `tokio::join!(identity, account)`. Enforcing
  one-in-flight would break it unless `whoami` is serialized first. Land B2
  alongside serializing `whoami`'s two reads, and re-run the e2e suite (the
  replay fixture drives interleaved requests).
- B3 Strict `MAX_FRAME` before-exceed (capped read loop).

### Phase C — Application purity
- C1 `application/{action,effect,reducer,store}.rs`; `AppStore` flat shape.
- C2 Reroute I/O out of the reducer into `Effect`s; outcomes return as
  `Action`s (method-by-method, keep e2e green).
- C3 Substates `composer.rs`, `conversation.rs` (200-entry cap, visible
  truncation, no-evict-active-turn), `help.rs`.
- C4 Enforce `application` imports no Ratatui/Crossterm/tokio/serde_json
  (guardrail check).

### Phase D — UI leaves
- D1 `ui/input.rs` single key seam; consolidate `KeyEvent` handling.
- D2 `ui/view.rs` consolidated, viewport-bounded rendering; narrow-terminal
  behavior defined and tested. — PARTIAL: stable assistant Markdown now uses a
  bounded prepared-render cache, while oversized single entries remain the
  documented viewport-bounding gap.
- D3 `ui/terminal.rs` RAII + real-PTY tests (panic/signal/resize/restoration).

### Phase E — Drift guardrails
- E1 CI check: `application/` imports no Ratatui/Crossterm/tokio/serde_json;
  `server/` no Ratatui/Crossterm; only `ui/input.rs` references
  `crossterm::event::KeyEvent`.
- E2 Contract-as-checklist: every PR into `cli-rust/` updates
  [rust-cli-implementation-contract.md](../../docs/design/rust-cli-implementation-contract.md) in the same PR.
- E3 PoC leaf retirement as Phase D converges; `lib.rs` shrinks toward the
  target module tree.

## Progress

- [x] Phase A1 — `cli.rs` clap types + `main.rs` parse/`app::run` reshape
- [~] Phase A3 (input-thread party) — dedicated OS thread blocking on
  `crossterm::poll(100ms)` + stop flag, bounded cap-256 channel, RAII
  stop->close->join. e2e parity gate: 132 passed / 4 skipped at `-n 8` (3/3
  clean); the 24-worker run occasionally flakes a mouse-selection step, which
  is pre-existing parallel-load flakiness (baseline flakes the same family of
  tests under 24 workers; the repo ships a `test_flaky` target for this).
- [ ] Phase A2 — `app.rs` composition root + first-draw ordering test
- [x] Phase A5 — graceful shutdown (input-thread half + server-task half)
- [ ] Phase B1..A5-server, C, D, E — not started
