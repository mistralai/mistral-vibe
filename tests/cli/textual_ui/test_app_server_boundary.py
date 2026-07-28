from __future__ import annotations

import ast
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import pytest

from vibe.app_server.protocol import SERVER_METHODS
from vibe.utils.io import read_safe

TEXTUAL_UI_ROOT = Path(__file__).parents[3] / "vibe" / "cli" / "textual_ui"
VIBE_ROOT = TEXTUAL_UI_ROOT.parents[1]
CORE_ROOT = VIBE_ROOT / "core"
PROGRAMMATIC_PATH = VIBE_ROOT / "cli" / "programmatic.py"
ACP_RUNTIME_PATHS = (
    VIBE_ROOT / "acp" / "agent.py",
    VIBE_ROOT / "acp" / "content.py",
    VIBE_ROOT / "acp" / "image_blocks.py",
    VIBE_ROOT / "acp" / "session.py",
    VIBE_ROOT / "acp" / "session_updates.py",
    VIBE_ROOT / "acp" / "tool_io.py",
    VIBE_ROOT / "acp" / "user_display_content.py",
    VIBE_ROOT / "acp" / "utils.py",
    *sorted((VIBE_ROOT / "acp" / "commands").rglob("*.py")),
)
PUBLIC_APP_SERVER_PATHS = tuple(
    VIBE_ROOT / "app_server" / name
    for name in (
        "__init__.py",
        "_connection_protocol.py",
        "_effect_models.py",
        "_model.py",
        "client.py",
        "client_state.py",
        "client_tools.py",
        "config.py",
        "connection.py",
        "events.py",
        "host.py",
        "models.py",
        "protocol.py",
        "resources.py",
        "session.py",
        "transport.py",
    )
)
SERVER_ONLY_APP_SERVER_MODULES = (
    "vibe.app_server._",
    "vibe.app_server.client",
    "vibe.app_server.client_state",
    "vibe.app_server.connection",
    "vibe.app_server.local",
    "vibe.app_server.server",
    "vibe.app_server.stdio",
)


def _production_files() -> list[Path]:
    return sorted(TEXTUAL_UI_ROOT.rglob("*.py"))


def _module_name(source_path: Path) -> str:
    parts = list(source_path.relative_to(VIBE_ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _production_modules() -> dict[str, Path]:
    return {_module_name(path): path for path in VIBE_ROOT.rglob("*.py")}


def _imports(source_path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(read_safe(source_path).text, filename=source_path)
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                modules = [alias.name for alias in names]
            case ast.ImportFrom(module=module) if module is not None:
                modules = [module]
            case _:
                continue

        for module in modules:
            yield node.lineno, module


def test_textual_imports_only_public_app_server_modules() -> None:
    violations = [
        f"{source_path.relative_to(TEXTUAL_UI_ROOT)}:{line}: {module}"
        for source_path in _production_files()
        for line, module in _imports(source_path)
        if module == "vibe.core"
        or module.startswith("vibe.core.")
        or module.startswith(SERVER_ONLY_APP_SERVER_MODULES)
    ]

    assert not violations, "\n".join(violations)


def test_textual_has_no_transitive_core_dependency() -> None:
    modules = _production_modules()
    imports = {
        module: {name for _, name in _imports(path)} for module, path in modules.items()
    }
    starts = {
        module
        for module, path in modules.items()
        if path.is_relative_to(TEXTUAL_UI_ROOT)
    }
    pending = deque((module, [module]) for module in starts)
    visited = set(starts)
    violations: list[str] = []

    while pending:
        module, chain = pending.popleft()
        for imported in imports[module]:
            if imported == "vibe.core" or imported.startswith("vibe.core."):
                violations.append(" -> ".join([*chain, imported]))
                continue
            resolved = imported
            while resolved and resolved not in modules:
                resolved = resolved.rpartition(".")[0]
            if not resolved or resolved in visited:
                continue
            visited.add(resolved)
            pending.append((resolved, [*chain, resolved]))

    assert not violations, "\n".join(sorted(violations))


def test_textual_does_not_reference_agent_loop() -> None:
    violations = [
        f"{source_path.relative_to(TEXTUAL_UI_ROOT)}:{node.lineno}"
        for source_path in _production_files()
        for node in ast.walk(
            ast.parse(read_safe(source_path).text, filename=source_path)
        )
        if (isinstance(node, ast.Name) and node.id == "agent_loop")
        or (isinstance(node, ast.Attribute) and node.attr == "agent_loop")
        or (isinstance(node, ast.arg) and node.arg == "agent_loop")
    ]

    assert not violations, "\n".join(violations)


def test_textual_does_not_install_callback_setters_or_observers() -> None:
    violations = [
        f"{source_path.relative_to(TEXTUAL_UI_ROOT)}:{node.lineno}: {node.attr}"
        for source_path in _production_files()
        for node in ast.walk(
            ast.parse(read_safe(source_path).text, filename=source_path)
        )
        if isinstance(node, ast.Attribute)
        and (
            (node.attr.startswith("set_") and node.attr.endswith("_callback"))
            or "observer" in node.attr
        )
    ]

    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    "source_path", [PROGRAMMATIC_PATH, *ACP_RUNTIME_PATHS, *PUBLIC_APP_SERVER_PATHS]
)
def test_app_server_clients_do_not_import_core(source_path: Path) -> None:
    violations = [
        f"{source_path.relative_to(VIBE_ROOT)}:{line}: {module}"
        for line, module in _imports(source_path)
        if module == "vibe.core" or module.startswith("vibe.core.")
    ]

    assert not violations, "\n".join(violations)


def test_core_does_not_import_app_server_or_textual() -> None:
    violations = [
        f"{source_path.relative_to(CORE_ROOT)}:{line}: {module}"
        for source_path in CORE_ROOT.rglob("*.py")
        for line, module in _imports(source_path)
        if module == "vibe.app_server"
        or module.startswith("vibe.app_server.")
        or module == "textual"
        or module.startswith("textual.")
    ]

    assert not violations, "\n".join(sorted(violations))


def test_only_app_server_runtime_constructs_agent_loop() -> None:
    allowed = VIBE_ROOT / "app_server" / "_runtime.py"
    violations = [
        f"{source_path.relative_to(VIBE_ROOT)}:{node.lineno}"
        for source_path in VIBE_ROOT.rglob("*.py")
        if source_path != allowed
        for node in ast.walk(
            ast.parse(read_safe(source_path).text, filename=source_path)
        )
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentLoop"
    ]

    assert not violations, "\n".join(violations)


def test_protocol_has_no_generic_command_escape_hatch() -> None:
    assert not [method for method in SERVER_METHODS if method.endswith("/command")]
