---
name: vibe-python-style
description: Python coding conventions for Mistral Vibe. Use when writing, reviewing, or refactoring Python code in the Vibe codebase. Covers style, type hints, imports, Pydantic patterns, logging, error handling, and file I/O.
metadata:
  display-name: Vibe Python Style
  short-description: Python style and patterns for Vibe
  default-prompt: Use $vibe-python-style to follow Vibe Python conventions when writing or reviewing code.
---

# Vibe Python Style

Conventions for writing Python in the Vibe codebase. Apply whenever writing, reviewing, or refactoring Python code.

## Style

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

### `TYPE_CHECKING` and lazy imports

Moving imports under `if TYPE_CHECKING:` or into function bodies cuts startup time but risks runtime `NameError`. Before merging any import-deferral change, run:

- **Ruff `TC004`** (pre-commit hook) — per-file: flags `TYPE_CHECKING`-only names referenced at runtime.
- **`uv run python scripts/check_import_contracts.py`** — runtime cross-file: imports every `from <mod> import <name>` across `vibe/` and `tests/` to verify it resolves; also rebuilds Pydantic models to catch lazily-failing field types. Catches cross-file re-exports `TC004` misses. Missing non-vibe deps are non-blocking warnings.
- **`uv run scripts/suggest_lazy_imports.py`** — informational: reports deferral candidates (`TC001`–`TC003` + single-function heuristic). Not gated.

## Pydantic

- Parse external data via `model_validate`, `field_validator`, or `model_validator(mode="before")` — never ad-hoc `getattr` / `hasattr` walks or custom `from_sdk` constructors.
- Set `ConfigDict(extra=…)` explicitly. Use `validation_alias` (or field aliases) for kebab-case TOML keys.
- Discriminated unions (e.g. MCP `transport`): use sibling final classes plus a shared base/mixin, and compose with `Annotated[Union[...], Field(discriminator=...)]`. Never narrow the discriminator field in a subclass — it violates LSP and pyright will reject it.
- Document `Raises:` only for exceptions the function actually raises (or that propagate from public API calls). Don't list speculative built-ins.

## Logging & errors

- Use `from vibe.observability.logging import logger` — stdlib `logging` with `StructuredLogFormatter`, not `structlog`.
- Configure via env: `LOG_LEVEL` (default `WARNING`), `LOG_MAX_BYTES`. Logs land in `~/.vibe/logs/vibe.log`.
- Pass variables as `%s` positional args, not f-string interpolation: prefer `logger.error("Failed to fetch url=%s", url)` over `logger.error(f"Failed to fetch {url}")`. This defers formatting to the logging framework (only formats if the message is emitted) and keeps messages grep-friendly.
- Define module-local exception hierarchies. Always chain with `raise NewError(...) from e`. Rich exceptions expose a `_fmt()` helper for human-readable output.

## File I/O

- Prefer `vibe.core.utils.io.read_safe` / `read_safe_async` / `decode_safe` over raw `Path.read_text()`, `Path.read_bytes().decode()`, or `open()`.
- They return `ReadSafeResult(text, encoding)` and try UTF-8, then BOM detection, then locale, then `charset_normalizer` lazily.
- Pass `raise_on_error=True` only when callers must distinguish corrupt files from valid ones; the default replaces undecodable bytes with U+FFFD.
