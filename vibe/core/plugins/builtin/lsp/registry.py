"""vibe/core/plugins/builtin/lsp/registry.py

─────────────────────────────────────────────────────────────────────────────
Maps file extensions to LSP server configurations.

Each entry describes:
  language     : canonical name used as key
  extensions   : set of file extensions (with leading dot, lower-case)
  command      : argv to launch the server in --stdio mode
  language_id  : LSP textDocument languageId string
  root_markers : filenames whose presence in the tree confirms the language

Callers can extend this registry at runtime::

    from vibe.core.plugins.builtin.lsp.registry import LSP_REGISTRY, LspConfig
    LSP_REGISTRY["rust"] = LspConfig(
        language="rust",
        extensions={".rs"},
        command=["rust-analyzer"],
        language_id="rust",
        root_markers=["Cargo.toml"],
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil

from vibe.core.paths._vibe_home import LOG_DIR, GlobalPath


@dataclass
class LspConfig:
    """Configuration for one Language Server."""

    language: str
    extensions: frozenset[str]
    command: list[str]
    language_id: str
    root_markers: frozenset[str] = field(default_factory=frozenset)

    def is_available(self) -> bool:
        """Return True if the LSP executable exists in PATH."""
        return shutil.which(self.command[0]) is not None


# ── Built-in registry ─────────────────────────────────────────────────────────

LSP_REGISTRY: dict[str, LspConfig] = {
    # pyright via basedpyright (supports lsprotocol 2025+)
    "python": LspConfig(
        language="python",
        extensions=frozenset({".py", ".pyi"}),
        command=["basedpyright"],
        language_id="python",
        root_markers=frozenset({
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
        }),
    ),
    "typescript": LspConfig(
        language="typescript",
        extensions=frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}),
        command=[
            "typescript-language-server",
            "--stdio",
        ],
        language_id="typescript",
        root_markers=frozenset({"package.json", "tsconfig.json", "jsconfig.json"}),
    ),
    "java": LspConfig(
        language="java",
        extensions=frozenset({".java"}),
        command=["jdtls"],
        language_id="java",
        root_markers=frozenset({
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            ".classpath",
        }),
    ),
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def language_for_extension(ext: str) -> str | None:
    """Return the language name for a file extension, or None."""
    ext = ext.lower()
    for lang, cfg in LSP_REGISTRY.items():
        if ext in cfg.extensions:
            return lang
    return None


def language_for_path(path: str) -> str | None:
    """Return the language name for a file path, or None."""
    return language_for_extension(Path(path).suffix)


def detect_languages_in_dir(root: str, max_files: int = 1000) -> set[str]:
    """Walk *root* and return the set of languages whose files are present.

    Stops scanning after *max_files* entries to stay fast on large repos.
    Skips common non-source directories.
    """
    from pathlib import Path  # local import to avoid cycle

    IGNORE = frozenset({
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".env",
        "target",
        "build",
        "dist",
        ".idea",
        ".vscode",
    })

    found: set[str] = set()
    count = 0

    for _dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            lang = language_for_extension(ext)
            if lang:
                found.add(lang)
            count += 1
            if count >= max_files:
                return found

    return found
