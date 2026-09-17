---
name: migrate-cli-to-rust
description: Process for migrating the Vibe CLI from Python/Textual to Rust/Ratatui (vibe/cli-rust/). Use when picking work from the Linear project board, creating migration tickets, authoring parity scenarios, running the parity gate, or updating the parity matrix.
metadata:
  display-name: Migrate CLI to Rust
  short-description: "Rust CLI migration: Linear board, scenarios, parity gate, MVP process"
  default-prompt: Use $migrate-cli-to-rust when working on the Rust CLI migration, picking work from the Linear project board, creating migration tickets, authoring parity scenarios, running the gate, or updating the parity matrix.
---

# Migrate CLI to Rust

The Rust CLI (`vibe/cli-rust/`) is a thin-client TUI over the Python
`vibe-app-server`. The architecture, behavior contract, and rules live in the
design docs — this skill is the migration process only.

## Changelog fragments: ignore

Do **not** create changelog fragments (`changelog.d/`) for Rust CLI migration
work. The `changelog-fragments` skill does not apply to `vibe/cli-rust/` or
`client-e2e/`. Skip the one-per-PR check entirely for changes scoped to the
migration.

## Linear project board

The Linear project is the source of truth for migration work:

- **Board:** <https://linear.app/mistral-ai/project/vibe-cli-migration-4adc4ea879cf/issues>
- **Team:** VIBE (Vibe Code CLI)

### Picking work

On the user's request, list open issues in the project (`list_issues` with
`project = "Vibe CLI migration"`) and filter by status `Todo` or `In Progress`.
Each ticket references the Python source file, the Rust target file, and the
relevant `PARITY_CHECKLIST.md` section in its description — use these to scope
the change. Pick the ticket the user points to (or the highest-priority one
whose dependencies are met if they ask you to choose).

### Creating new tickets

On the user's request, create a Linear issue in the project via `save_issue`
with `team = "Vibe Code CLI"` and `project = "Vibe CLI migration"`. Do not
create tickets unprompted. Include:

- A clear title prefixed with `Rust CLI:` or `Rust TUI:`.
- The Python reference file path and the Rust target file path.
- The `PARITY_CHECKLIST.md` section it maps to (if applicable).
- Repro steps or evidence (profile, screenshot description, failing scenario
  name).

### Relationship to the roadmap and parity matrix

The [migration roadmap](../../../docs/migration/rust-cli-migration.md) MVP
table and the [parity matrix](../../../docs/design/python-cli-parity-matrix.md)
define the overall structure and exit criteria. Linear tickets are the
actionable units within each MVP — when an MVP's tickets are all merged, update
the parity matrix and roadmap per the "Parity matrix update" section below.

## References

- **Architecture rule:** [ADR 0016](../../../docs/adr/0016-rust-cli-delivery-surface.md)
- **Target architecture:** [design doc](../../../docs/design/rust-cli-architecture.md)
- **Implementation contract:** [design doc](../../../docs/design/rust-cli-implementation-contract.md)
- **Migration roadmap:** [roadmap](../../../docs/migration/rust-cli-migration.md) (MVP table, dependencies, exit criteria)
- **Parity matrix:** [matrix](../../../docs/design/python-cli-parity-matrix.md) (feature status, unique IDs)
- **Refactor tracker:** `vibe/cli-rust/REFACTOR_NOTES.md`
- **Local rules:** `vibe/cli-rust/AGENTS.md`

## Parity gate (mandatory at every step)

Every MVP slice must pass the parity gate before it is considered done. The
gate follows a TDD approach — study the Python reference **before** writing
the Rust code, not after:

1. **Read the Python reference.** The Linear ticket names the Python source
   file. Read it end to end — exact wordings, UI structure, guard conditions,
   state transitions, error messages, widget styling. This is the spec; do
   not approximate from the ticket title alone.
2. **Check for existing Python snapshots.** Look in `client-e2e/goldens/` and
   `client-e2e/scenarios/` for any pre-existing scenario that covers the
   feature (even partially). If one exists, read its scenario `.py` and its
   golden SVGs — they encode the expected rendering the Rust CLI must match.
3. **Identify if a scenario already exists** for the feature in
   `client-e2e/scenarios/`. If not, create one (see `client-e2e/AGENTS.md` for
   scenario authoring details). The scenario must encode the behavior observed
   in step 1-2, not a guess.
4. **Create the missing scenario** that captures the expected behavior. Run
   `make test` to confirm it fails (red).
5. **Implement** the feature in the Rust CLI, matching the Python wordings,
   UI structure, guard conditions from step 1 with unit tests for any new pure
   logic (parsers, serde types, helpers, state machines): one focused test
   binary in `vibe/cli-rust/tests/<name>.rs` with a one-line `//!` doc.
   Inline `#[cfg(test)]` in src files is banned; rendering behavior belongs to
   parity scenarios and goldens, not unit tests.
6. **Run tests / update golden snapshots if required.** `make test` builds the
   Rust binary, runs `cargo test`, then runs the `client-e2e` parity suite,
   which drives both CLIs over PTYs against a deterministic replay server and
   diffs the rendered grids cell-by-cell (text + color). Re-run the whole suite
   every PR to guard regressions. Compare the Rust golden SVGs against the
   Python snapshots from step 2 — they must match.

## Running

From the `vibe/` project root:

- `make build` — optimized Rust binary.
- `make test_rust` — Rust unit tests.
- `make test` — build + cargo test + run the full `client-e2e` parity suite (Py vs Rust) + golden snapshot gate.
- `make test_golden` — Rust-only golden snapshot gate (no Python CLI needed).
- `make store_golden` — generate/regenerate all Rust golden snapshots.
- `uv run python client-e2e/run.py <scenario>` — capture one committed scenario.
- `uv run python client-e2e/probe.py <file.py>` — test an ad-hoc scenario.
- `make fmt` / `make lint` — format and clippy.

## Golden snapshots

`test_golden.py` captures the Rust CLI and compares against committed goldens in
`client-e2e/goldens/<scenario>/` — SVG per snapshot (for human review) + `requests.json`
(normalized RPCs). The test compares rendered SVG byte-for-byte and normalized
requests. No Python CLI needed. Generate with `make store_golden`; regenerate
when rendering intentionally changes. See `client-e2e/AGENTS.md` for details.

`cargo test` runs in the repo-root pre-commit hook (scoped to `vibe/cli-rust/`
Rust source changes). The `client-e2e` parity suite is not in pre-commit or
CI yet; run `make test` manually before pushing.

## Parity matrix update (mandatory)

When a slice lands (feature implemented, gate green), update the
[parity matrix](../../../docs/design/python-cli-parity-matrix.md):

1. Change the status of every affected row to `Available` (if passing all
   parity assertions) or `In-Progress` (if partial).
2. Add a dated changelog entry at the bottom of the matrix summarizing the
   change.
3. Update the migration roadmap MVP status to match.

A slice is not done until the parity matrix is updated. If the matrix is not
updated in the same PR, the review must block.
