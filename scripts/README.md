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

## Primary: real Vibe executable

```text
fixture API -> vibe -p ... --watchcat --auto-approve
            -> programmatic runner -> AgentLoop -> todo tool
            -> Watchcat recovery -> verified changed action
```

```bash
uv run python scripts/watchcat_live_demo.py
```

Default pacing: `0.3s` per model fixture response. Instant mode:

```bash
uv run python scripts/watchcat_live_demo.py --delay 0
```

In Vibe: `/watchcat demo headless`.

## Exhaustive matrix + live canary

In Vibe: `/watchcat demo all` runs the real headless canary first, then every
direct deterministic scenario, and opens `/watchcat report`.

## Fast fallback harness

Run the direct assertion-backed matrix and persist its traces:

```bash
uv run python scripts/watchcat_demo.py
```

Then open Vibe and run `/watchcat report`.

Run one scenario with a paced trace:

```bash
uv run python scripts/watchcat_demo.py --scenario recovery --delay 0.05
```

The direct matrix defaults to `0.02s` between trace rows. Use `--delay 0` for
fast automated coverage.

| Scenario | Demonstrates |
| --- | --- |
| `threshold-1..3` | Every below-threshold repeat iteration → no incident |
| `result-changed` | Fourth result differs → suspected incident closes as false positive |
| `repository-changed` | Fourth repository fingerprint differs → progress recognized |
| `recovery` | Four exact failures → one context injection → changed action → closed incident |
| `signal` | Signal quality degraded → score `92` → deterministic context injection → closed incident |
| `signal-blocked` | Signal quality degraded → score `40` → intervention blocked |
| `signal-eval-failed` | Harness scoring error → failure recorded → intervention blocked |
| `verification-failed` | Same next action → mitigation retried → verification remains open |
| `degraded` | Recovery delivery failure → persisted degraded incident |
