# Project Management Scripts

This directory contains scripts that support project versioning, deployment workflows, and import-time correctness checks.

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

## Versioning

### Usage

```bash
# Bump major version (1.0.0 -> 2.0.0)
uv run scripts/bump_version.py major

# Bump minor version (1.0.0 -> 1.1.0)
uv run scripts/bump_version.py minor

# Bump patch/micro version (1.0.0 -> 1.0.1)
uv run scripts/bump_version.py micro
# or
uv run scripts/bump_version.py patch
```

## Releasing

`prepare_release.py` builds the release branch from the previous public release tag, cherry-picks commits from the matching `-private` tags, and (by default) squashes them into a single release commit.

As part of release branch creation, the script **freezes the full transitive dependency graph** into both `[project].dependencies` and `[dependency-groups].build` of `pyproject.toml` using the current `uv.lock`:

```bash
uv export --no-hashes --no-dev --no-emit-project --frozen --format requirements.txt
uv export --only-group build --no-emit-project --no-hashes --frozen --format requirements.txt
```

The pinned `[project].dependencies` is what `uv build` reads in `.github/workflows/release.yml`, so the wheel published to PyPI carries `Requires-Dist:` entries pinned to exact versions (with environment markers preserved). End users installing `mistral-vibe` from PyPI get the same dependency set the team tested against.

The pinned `[dependency-groups].build` is what `uv sync --no-dev --group build` reads in `.github/workflows/build-and-upload.yml`, so the PyInstaller binaries on each release tag are built against the exact same PyInstaller / truststore versions every time.

`main` keeps `>=` ranges, so day-to-day upgrades on `main` (`uv lock --upgrade-package …`, Renovate PRs, etc.) are unaffected. Each new release re-snapshots `uv.lock` — there is no hand-maintained pin list.
