# Project Management Scripts

This directory contains scripts that support import-time correctness checks and startup-cost analysis.

## Import checks

Run both before merging any `TYPE_CHECKING` / lazy-import change (see `AGENTS.md`).

### `check_import_contracts.py` — runtime cross-file gate

```bash
uv run python scripts/check_import_contracts.py
```

Imports every `from <mod> import <name>` across `vibe/` and `tests/` to verify it resolves at runtime. Catches cross-file re-exports ruff `TC004` (per-file) misses. Also rebuilds Pydantic models to catch lazily-failing field types. Missing non-vibe deps are non-blocking warnings.

### `suggest_lazy_imports.py` — informational

```bash
uv run scripts/suggest_lazy_imports.py          # flat listing
uv run scripts/suggest_lazy_imports.py --stats  # per-rule counts
uv run scripts/suggest_lazy_imports.py --tree   # directory tree
uv run scripts/suggest_lazy_imports.py --check  # CI gate (exit 1 on findings)
```

Reports deferral candidates: `TC001`–`TC003` (annotation-only) and `[lazy]` (single-function heuristic). Not gated.

## Import Analysis

`check_startup_import_cost.py` builds the `mistral-vibe` wheel, installs it into a
fresh venv, and reports cold import cost for each target declared in
`startup_import_cost.vibe.toml`:

- wall time and total imported module count,
- the slowest modules by self time (via `python -X importtime`),
- file-operation call count under `strace` (Linux only; skipped elsewhere),
- installed wheel size.

Each command may carry an optional `budget`. Commands without a budget are
measured and reported but never fail the run, so a config can ship budget-free
and be filled in from a baseline run (observed count + ~10% headroom). Once a
`budget` is set, exceeding it exits non-zero. The shipped `startup_import_cost.vibe.toml`
already carries baselined budgets, so a regression overshoot fails the step.

### Usage

```bash
# Run the measurement (enforces budgets when set; exits non-zero on overshoot)
uv run scripts/check_startup_import_cost.py

# Override the project or config
uv run scripts/check_startup_import_cost.py --project vibe --config path/to/config.toml
```
