# Contributing to Mistral Vibe

Thank you for your interest in Mistral Vibe! We appreciate your enthusiasm and support.

## We Do Not Accept Pull Requests

**Mistral Vibe does not accept pull requests from the community.** Only maintainers can open pull requests on this repository.

Instead, tell us what you need, and our team will take it from there:

- **Found a bug?** [Open an issue](https://github.com/mistralai/mistral-vibe/issues/new/choose).
- **Want a feature or a documentation improvement?** [Start a discussion](https://github.com/mistralai/mistral-vibe/discussions/new?category=ideas) in the Ideas category.

**We especially encourage**:

- **Bug reports** – Help us uncover and squash issues
- **Feedback & ideas** – Tell us what works, what doesn't, and what could be even better
- **Documentation improvements** – Suggest clarity improvements or highlight missing pieces

Mistral Vibe is in active development and our team iterates quickly, so we may take some time to answer issues and discussions.

## How to Provide Feedback

### Bug Reports

If you encounter a bug, please open an issue with the following information:

1. **Description**: A clear description of the bug
2. **Steps to Reproduce**: Detailed steps to reproduce the issue
3. **Expected Behavior**: What you expected to happen
4. **Actual Behavior**: What actually happened
5. **Environment**:
   - Python version
   - Operating system
   - Vibe version
6. **Error Messages**: Any error messages or stack traces
7. **Configuration**: Relevant parts of your `config.toml` (redact any sensitive information)

### Feature Requests and Feedback

We'd love to hear your ideas! When submitting feedback or feature request discussions:

1. **Avoid duplicates**: Check opened discussions before creating a new one
2. **Clear Description**: Explain what you'd like to see or improve
3. **Use Case**: Describe your use case and why this would be valuable
4. **Alternatives**: If applicable, mention any alternatives you've considered

## Development Setup

This section is for developers who want to set up the repository for local development, for example to reproduce a bug before reporting it. We do not accept pull requests.

### Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) - Modern Python package manager
- [Rust](https://rustup.rs) - Builds the `vibe-rs` terminal UI

### Setup

1. Clone the repository:

   ```bash
   git clone <repository-url>
   cd mistral-vibe
   ```

2. Install dependencies:

   ```bash
   uv sync --all-extras
   ```

   This will install both runtime and development dependencies.

3. (Optional) Install pre-commit hooks:

   ```bash
   uv run pre-commit install
   ```

   Pre-commit hooks will automatically run checks before each commit.

4. Run Vibe:

   ```bash
   uv run vibe
   ```

   In a source checkout, the first run builds the `vibe-rs` terminal UI from
   `vibe/cli-rust` with cargo. After changing the Rust sources, rebuild it with
   `make build`. On Linux without the ALSA development files, set
   `CARGO_BUILD_FLAGS=--no-default-features` to build it without voice mode.

### Building distributions

`uv build --wheel` produces one platform-specific wheel containing the Python
package (`vibe-acp` and `vibe-app-server`), the Rust terminal UI, and the
Unified Harness runtime. The build requires a working Rust toolchain and fails
if either native component cannot be built.
On Linux, a wheel built from a checkout is a portable `manylinux_2_28` wheel:
the terminal UI is linked with zig for glibc 2.28 and built without voice mode,
like the published Linux wheels, with a warning. Voice mode needs a wheel for
this machine only (`MATURIN_PEP517_ARGS="--compatibility linux"`) and pkg-config
with the ALSA development files (`libasound2-dev` or `alsa-lib-devel`). Without
them, that build also prints a warning and leaves voice mode out.
Setting `CARGO_BUILD_FLAGS` to select features (`--features`/`-F`,
`--no-default-features`, `--all-features`) or a `--target` skips this detection.

`VIBE_SKIP_RUST_TUI=1` may be used by development-only checks that do not need
the terminal binary, and by test-only vibe-acp bundles that skip the setup
wizard (vibe-acp bundles the terminal binary for `--setup`). Public releases always build both native
components. The source distribution bundles both Rust sources, so building from
it needs Rust.

### Logging Configuration

Logs are written to `~/.vibe/logs/vibe.log` by default. Control logging via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) | `WARNING` |
| `LOG_MAX_BYTES` | Max log file size in bytes before rotation | `10485760` (10 MB) |
| `DEBUG_MODE` | When `true`, forces `DEBUG` logging | - |

Example:

```bash
LOG_LEVEL=DEBUG uv run vibe
```

#### Runtime log level

You can change the log level at runtime without restarting:

```text
/log-level                       # show current levels (session, env, config, effective)
/log-level set DEBUG             # override for this process
/log-level set-global INFO       # also persist to config.toml
/log-level unset                 # clear the session override
```

You can also set `log_level` in `config.toml` (optional, defaults to `WARNING` when absent):

```toml
log_level = "DEBUG"
```

Precedence: session override > `LOG_LEVEL` env var > `log_level` in config.toml > default.

The debug console (`Ctrl+\`) streams the file log in real time — change the level with `/log-level` and new entries appear immediately.

#### Logging conventions for contributors

- Import the logger from `vibe.observability.logging`:
  ```python
  from vibe.observability.logging import logger
  ```
- Pass variables as `%s` positional args, not f-string interpolation — this defers formatting to the logging framework (skipped entirely when the level gates the message out):
  ```python
  logger.info("Model call completed model=%s duration_ms=%d", alias, duration_ms)
  ```
- Use `DEBUG` for "about to do X" diagnostics and `INFO` for "here's what happened" outcomes with metadata (duration, token counts, error type). Never log raw model messages, tool arguments, or tool output — only metadata.

### Running Tests

Run all tests:

```bash
uv run pytest
```

By default, local runs use `max(1, ceil(available CPUs / 2) - 1)` workers to
leave resources available for the rest of the machine. CI runners (detected via
`BUILDKITE` or `GITHUB_ACTIONS`) use every available CPU so the full suite stays
fast; the generic `CI` variable is deliberately ignored because it is often set
in developer shells. Set `PYTEST_XDIST_AUTO_NUM_WORKERS` to a positive integer
to override either default:

```bash
PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run pytest
```

Run tests with verbose output:

```bash
uv run pytest -v
```

Run a specific test file:

```bash
uv run pytest tests/test_agent_tool_call.py
```

### Linting and Type Checking

#### Ruff (Linting and Formatting)

Check for linting issues (without fixing):

```bash
uv run ruff check .
```

Auto-fix linting issues:

```bash
uv run ruff check --fix .
```

Format code:

```bash
uv run ruff format .
```

Check formatting without modifying files (useful for CI):

```bash
uv run ruff format --check .
```

#### Pyright (Type Checking)

Run type checking:

```bash
uv run pyright
```

#### Pre-commit Hooks

Run all pre-commit hooks manually:

```bash
uv run pre-commit run --all-files
```

The pre-commit hooks include:

- Ruff (linting and formatting)
- Pyright (type checking)
- Typos (spell checking)
- YAML/TOML validation
- Action validator (for GitHub Actions)

### Code Style

- **Line length**: 88 characters (Black-compatible)
- **Type hints**: Required for all functions and methods
- **Docstrings**: Follow Google-style docstrings
- **Formatting**: Use Ruff for both linting and formatting
- **Type checking**: Use Pyright (configured in `pyproject.toml`)

See `pyproject.toml` for detailed configuration of Ruff and Pyright.

## Code Contributions

We do not accept code contributions, and pull requests are restricted to maintainers. To report a bug, open an issue; to propose an improvement, start a discussion in the Ideas category. Give as much context as possible (use case, reproduction steps, suggested approach).

## Questions?

If you have questions about using Mistral Vibe, please check the [README](README.md) first. For other inquiries, feel free to open a discussion.

Thank you for helping make Mistral Vibe better! 🙏
