# AGENTS.md

Conventions for AI agents and humans contributing to **Mistral Vibe** — a Python 3.12+ CLI coding assistant managed with `uv`.

Layout: `vibe/core` is the engine (agent loop, tools, LLM backends, config); `vibe/cli` is the Textual TUI; `vibe/acp` bridges to the Agent Client Protocol; `vibe/setup` runs first-run wizards. Tests live in `tests/` with autouse fixtures in `conftest.py` and test doubles in `tests/stubs/`.

## Architecture Decisions

Before architecture-affecting changes, read the matching ADRs. If a change fits the current code but conflicts with ADR direction, flag it to the user before implementing.

| Change area | ADR |
| --- | --- |
| Architecture principles, module boundaries, startup/runtime speed, simple changes | [0001 Architecture Principles](docs/adr/0001-architecture-principles.md) |
| Core engine, Textual CLI, ACP, setup, or programmatic surfaces | [0002 Core Engine And Delivery Surfaces](docs/adr/0002-core-engine-and-delivery-surfaces.md) |
| Agent loop orchestration, streaming, typed events, cancellation, or responsiveness | [0003 Event Driven Agent Loop](docs/adr/0003-event-driven-agent-loop.md) |
| Tool contracts, permissions, tool output, UI metadata, or tool adapters | [0004 Typed Permissioned Tools](docs/adr/0004-typed-permissioned-tools.md) |
| Config models, layering, defaults, migrations, reloads, or runtime overrides | [0005 Layered Configuration](docs/adr/0005-layered-configuration.md) |
| Session logging, resume, rewind, transcript metadata, or migrations | [0006 Local Sessions](docs/adr/0006-local-sessions.md) |
| Skills, agents, subagents, hooks, MCP, connectors, custom tools, or discovery | [0007 Extension Mechanisms](docs/adr/0007-extension-mechanisms.md) |
| Adding or changing analytics instrumentation, telemetry events, or event properties | [0008 Feature Instrumentation](docs/adr/0008-feature-instrumentation.md) |

## Commands

Always go through `uv` — never invoke bare `python` or `pip`.

- `uv run vibe` / `uv run vibe-acp` — the two entry points.
- `uv run pytest` — full suite (parallel via `pytest-xdist`).
- `uv run pyright` — strict type check.
- `uv run ruff check --fix .` and `uv run ruff format .` — run both after every code change and report the files modified.
- `uv run pre-commit run --all-files` — full lint pass. Install once with `uv tool install pre-commit && uv run pre-commit install`.
- Useful uv basics: `uv sync --all-extras`, `uv add <pkg>`, `uv remove <pkg>`.

## Project layout & module conventions

- `__init__.py` exposes the public API via an explicit `__all__`.
- Private modules are prefixed with `_` (e.g. `_settings.py`, `_config.py`).
- Pydantic models live in `models.py`; configuration in `_settings.py` / `_config.py`.
- Abstract interfaces use the `_port.py` suffix (hexagonal-style ports).
- Tests mirror the source layout: a test lives in the directory mirroring its source module's package. Tests for a `vibe/core` subpackage go under the matching `tests/core/<subpackage>/` (e.g. `vibe/core/utils/http.py` → `tests/core/utils/test_http_client.py`); tests for modules that sit directly in `vibe/core/` (e.g. `loop.py`, `types.py`) stay flat in `tests/core/`. No `__init__.py` is needed in test subdirectories — pytest runs in `--import-mode=importlib`. Test doubles in `tests/stubs/` are named `Fake*`.

## Python style

- Prefer `match` / `case` over long `if` / `elif` chains.
- Use the walrus operator `:=` only when it shortens code and improves clarity.
- Be a never-nester: early returns and guard clauses over nested blocks.
- Modern type hints only: built-in generics (`list`, `dict`) and `|` unions. Never import `Optional`, `Union`, `Dict`, `List` from `typing`.
- Use `pathlib.Path` (and `anyio.Path` in async paths) instead of `os.path`.
- Use f-strings, comprehensions, and context managers; follow PEP 8.
- Enums: `StrEnum` / `IntEnum` with `auto()` and UPPERCASE members. For type-mixing, the mix-in type comes before `Enum` in the bases. Add methods or `@property` rather than parallel lookup tables.
- Write declarative, minimalist code: express intent, drop boilerplate.
- Never call a private method from outside of its class in production code. Accessing private methods in tests is acceptable.
- Avoid comments and docstrings, except for when there's a hard to spot corner case

## Typing & imports

- Pyright is strict and gates CI; fix types at the source.
- No relative imports — `ban-relative-imports = "all"`. Always `from vibe.core.x import …`.
- No inline `# type: ignore` or `# noqa`. Fix with refined signatures (TypeVar, Protocol), `isinstance` guards, `typing.cast` when control flow guarantees the type, or a small typed wrapper at the boundary.

## Pydantic

- Parse external data via `model_validate`, `field_validator`, or `model_validator(mode="before")` — never ad-hoc `getattr` / `hasattr` walks or custom `from_sdk` constructors.
- Set `ConfigDict(extra=…)` explicitly. Use `validation_alias` (or field aliases) for kebab-case TOML keys.
- Discriminated unions (e.g. MCP `transport`): use sibling final classes plus a shared base/mixin, and compose with `Annotated[Union[...], Field(discriminator=...)]`. Never narrow the discriminator field in a subclass — it violates LSP and pyright will reject it.
- Document `Raises:` only for exceptions the function actually raises (or that propagate from public API calls). Don't list speculative built-ins.

## Async

- `asyncio` is the orchestration runtime in the agent loop and tool execution. Use `asyncio.create_task` + queues for concurrent work, not blanket `gather`.
- Never run CPU-heavy or I/O-bound code on the UI thread. The Textual TUI and the agent loop share one event loop, so anything blocking (large JSON/Pydantic serialization, `os.fsync`, subprocess calls, recursive globs) freezes the UI — offload it with `asyncio.to_thread`. Async file wrappers don't make blocking syscalls non-blocking.
- Use `anyio.Path` for file I/O on async paths.
- Streaming surfaces return `AsyncGenerator[Event, None]`, not coroutines.
- When Vibe owns an HTTP client, use `VibeAsyncHTTPClient` from `vibe.core.utils.http` instead of `httpx.AsyncClient` so proxy env vars are handled consistently. Its CIDR `NO_PROXY` matching applies only to IP-literal request hosts; do not resolve DNS before proxy selection. Mock outbound HTTP with `respx` in tests.

## Tools

- Subclass `BaseTool` from `vibe/core/tools/base.py` with a Pydantic args model and a `BaseToolConfig` generic parameter.
- Implement `async def run(args, ctx: InvokeContext)` and yield events progressively.
- Raise `ToolError` for user-facing failures; raise `ToolPermissionError` for authorization failures.
- Declare permission with `ToolPermission` (`ALWAYS` / `ASK` / `NEVER`); honor it consistently.

## Logging & errors

- Use `from vibe.core.logger import logger` — stdlib `logging` with `StructuredLogFormatter`, not `structlog`.
- Configure via env: `LOG_LEVEL` (default `WARNING`), `LOG_MAX_BYTES`. Logs land in `~/.vibe/logs/vibe.log`.
- Pass variables as `%s` positional args, not f-string interpolation: prefer `logger.error("Failed to fetch url=%s", url)` over `logger.error(f"Failed to fetch {url}")`. This defers formatting to the logging framework (only formats if the message is emitted) and keeps messages grep-friendly.
- Define module-local exception hierarchies. Always chain with `raise NewError(...) from e`. Rich exceptions expose a `_fmt()` helper for human-readable output.

## File I/O

- Prefer `vibe.core.utils.io.read_safe` / `read_safe_async` / `decode_safe` over raw `Path.read_text()`, `Path.read_bytes().decode()`, or `open()`.
- They return `ReadSafeResult(text, encoding)` and try UTF-8, then BOM detection, then locale, then `charset_normalizer` lazily.
- Pass `raise_on_error=True` only when callers must distinguish corrupt files from valid ones; the default replaces undecodable bytes with U+FFFD.

## Widgets

- For selectable lists, use `NavigableOptionList` from `vibe/cli/textual_ui/widgets/navigable_option_list.py` instead of Textual's `OptionList`. It adds `j`/`k` cursor navigation on top of the arrow keys; the bare `OptionList` only handles arrows.
- Keep feature-specific Textual state and helper functions with the feature's widget package. `app.py` should orchestrate mounting and message handling, not accumulate feature-local state models, defaulting helpers, or import factories.

## TCSS

- When a rule sets `color: $text-muted;`, pair it with a nested `&:ansi { text-style: dim; }` so the muted intent survives under ANSI themes.
- Never use `ansi_*` colors (e.g. `ansi_red`, `ansi_bright_blue`). Use Textual theme variables like `$primary`, `$foreground`, `$surface`, `$error`, etc. — see https://textual.textualize.io/guide/design/. ANSI themes are derived from these variables automatically.

## Cross-platform

Vibe ships on Linux, macOS, and Windows, but **CI runs the test suite on Linux only**. The machine you develop on is not the machine that gates the PR — code and tests must pass on every platform, and must be *provably* correct on the platforms CI cannot exercise.

- Never rely on host-specific behavior (shell, path separator, line ending, available binaries, `$SHELL`, case sensitivity). Assume the same code runs under `cmd.exe`, Git Bash, `zsh`, and `sh`.
- Branch platform-specific logic behind the helpers in `vibe.core.utils.platform` (`is_windows()`, `resolve_windows_shell()`), not ad-hoc `sys.platform` checks. Keep POSIX-only assumptions (forward slashes, POSIX `shlex` escaping, `$SHELL`) out of shared paths and scope Windows-only handling to `is_windows()`.
- Use `pathlib.Path` for path composition; never hardcode `/` or `\\`.
- Test other-platform code paths **on Linux** by monkeypatching `sys.platform`, env vars, and probes like `shutil.which` — do not `@pytest.mark.skipif` them away. A Windows-only behavior with no Linux-runnable test is untested in CI.
- When an assertion depends on the platform, force it explicitly (e.g. `monkeypatch.setattr(module, "is_windows", lambda: True)`) instead of letting the test pass only because of the host it happened to run on. A test that would flip its result on a different OS is a bug.

## Tests

- Stack: `pytest` + `pytest-asyncio` + `pytest-textual-snapshot` + `respx`.
- Mark async tests with `@pytest.mark.asyncio`. Mock outbound HTTP with `respx`.
- Rely on the autouse fixtures in `tests/conftest.py` (`config_dir`, `tmp_working_directory`) for filesystem and home-dir isolation.
- No docstrings on test functions, methods, or classes — descriptive names like `test_create_user_returns_403_when_unauthorized` carry the intent. Pytest displays docstrings instead of node IDs when present, which hurts.
- Tests are exempt from the `ANN` and `PLR` ruff rules (see `per-file-ignores`).

## Git

- Never use `git commit --amend`, `git push --force`, or `git push --force-with-lease`.
- Always create new commits and push with a plain `git push`.
- Reconciling with the upstream of the current branch (e.g. push rejected because `origin/<current-branch>` advanced): rebase the current branch onto its upstream — do not merge the upstream branch into the current one, never force-push.
- Reconciling with the base branch (e.g. `origin/main`) once the PR is open: merge the base branch into the current branch — do not rebase, since rebasing rewrites already-pushed history and would require a force-push.
- Run git commands through `uv run` (e.g. `uv run git commit`, `uv run git push`) so pre-commit hooks resolve the project's venv — bare `git commit` fails pre-commit with `reportMissingImports` because pyright can't find third-party packages.

## CI / GitHub Actions

- Pin every `uses:` to a full **commit SHA** with an exact version comment: `uses: owner/action@<commit-sha> # vX.Y.Z`.
- Resolve to the commit, not the annotated-tag object: take the `refs/tags/vX^{}` line from `git ls-remote --tags`, or `gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq .object` peeled to a commit. Check with `git cat-file -t <sha>` → `commit`, not `tag`. Never pin a moving major tag (`v9`).

## Supply-chain pinning

Every external input to the build, CI, or install path must be pinned to an immutable identifier — never a mutable tag or an unverified download. Add a human-readable comment next to each pin.

- **Container images**: reference by `@sha256:<digest>`, never a bare tag (`:latest`, `:8`). Resolve the digest via the registry's `Docker-Content-Digest` header (`curl -sI -H 'Accept: application/vnd.oci.image.index.v1+json' <registry>/v2/<repo>/manifests/<tag>`). When the image lives inside a JSON matrix string, document the tag→digest mapping in an adjacent comment.
- **pre-commit hooks** (`.pre-commit-config.yaml`): pin every `rev:` to a full commit SHA with a `# vX.Y.Z` comment. Run `pre-commit autoupdate --freeze` to refresh, and resolve to the peeled commit ref (`refs/tags/vX^{}`), not the annotated-tag object — same rule as `uses:` above.
- **Build-system deps** (`pyproject.toml` `[build-system] requires`): pin `hatchling`, `hatch-vcs`, `editables` (and any addition) to exact `==` versions. These execute during source builds and are not covered by `uv.lock`.
- **External binary downloads** (e.g. `patchelf` in `scripts/ci/`): never pipe an unverified download straight into `tar`/`sh`. Download to a temp file, verify `sha256sum -c` against a known-good hash keyed by version (and arch when relevant), then extract. Hard-fail when no hash is registered for the requested version/arch so a bump forces updating the hash.

## Editor tip

In Cursor / Pyright, the "Add import" quick fix is missing — use the workspace snippets `acpschema`, `acphelpers`, `vibetypes`, `vibeconfig` to insert the import line, then rename the symbol.


## Autoimprovement

- Suggest to add new rules to AGENTS.md based on user input or PR comments, when a change request could be generalized as a rule.
- Suggest updates to the README.md file according to feature changes or additions
- Keep the builtin Vibe Skill (`vibe/core/skills/builtins/vibe.py`) up-to-date. It documents the CLI's features, such as args, flags, config options and persistence, commands, built-in agents, file discovery logic.
