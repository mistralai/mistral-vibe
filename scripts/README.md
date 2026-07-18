# Project Management Scripts

This directory contains scripts that support project versioning and deployment workflows.

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

# Watchcat demo

Run every deterministic scenario with a paced ASCII trace:

```bash
uv run python scripts/watchcat_demo.py
```

Then open Vibe and run `/watchcat report` for the persisted ASCII dashboard.

Run one scenario instantly:

```bash
uv run python scripts/watchcat_demo.py --scenario recovery --delay 0
```

| Scenario | Demonstrates |
| --- | --- |
| `guarded` | Three exact failures → threshold protection → no incident |
| `recovery` | Four exact failures → one context injection → changed action → closed incident |
| `signal` | Signal quality degraded → score `92` → deterministic context injection → closed incident |
| `signal-blocked` | Signal quality degraded → score `40` → intervention blocked |
| `degraded` | Recovery delivery failure → persisted degraded incident |
