"""Register aether hooks and companion skills in the user's vibe home directory."""

from __future__ import annotations

import sys
from pathlib import Path


def _vibe_home() -> Path:
    import os
    return Path(os.environ.get("VIBE_HOME", Path.home() / ".vibe"))


def _hooks_toml() -> Path:
    return _vibe_home() / "hooks.toml"


def _config_toml() -> Path:
    return _vibe_home() / "config.toml"


def _skills_dir() -> Path:
    return _vibe_home() / "skills"


def enable(yes: bool = False) -> None:
    import tomlkit

    print(
        "\n⚠️  Aether uses vibe's experimental hook system.\n"
        "   Hook behaviour may change across vibe versions.\n"
    )
    if not yes:
        try:
            answer = input("Enable aether discipline hooks? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if answer and answer not in ("y", "yes"):
            print("Aborted.")
            return

    vibe_home = _vibe_home()
    vibe_home.mkdir(parents=True, exist_ok=True)

    command = f"{sys.executable} -m vibe.core.hooks.aether.runner"

    # --- hooks.toml ---
    hooks_path = _hooks_toml()
    if hooks_path.exists():
        doc = tomlkit.parse(hooks_path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()

    hooks = doc.get("hooks", tomlkit.aot())
    existing = {h.get("name") for h in hooks}

    if "aether" in existing:
        for hook in hooks:
            if hook.get("name") == "aether":
                hook["command"] = command
        doc["hooks"] = hooks
        hooks_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        print(f"✓ Hook command updated in {hooks_path}")
    else:
        entry = tomlkit.table()
        entry.add("name", "aether")
        entry.add("type", "before_tool")
        entry.add("match", "bash")
        entry.add("command", command)
        entry.add("description", "Aether discipline gates (whetstone/bonsai/temper/cairn)")
        hooks.append(entry)
        doc["hooks"] = hooks
        hooks_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        print(f"✓ Hook registered in {hooks_path}")

    # --- config.toml ---
    config_path = _config_toml()
    if config_path.exists():
        config_doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    else:
        config_doc = tomlkit.document()

    config_doc["enable_experimental_hooks"] = True
    _register_bonsai_servers(config_doc)
    config_path.write_text(tomlkit.dumps(config_doc), encoding="utf-8")
    print(f"✓ enable_experimental_hooks = true written to {config_path}")

    # --- skills ---
    _write_skills()

    # --- AGENTS.md ---
    _write_agents_md()

    print(
        "\n✓ Aether enabled. Active gates:\n"
        "  whetstone — blocks commits when plan is uncritiqued (/autocritic)\n"
        "  bonsai    — nudges toward AST tools on .py/.ts/.tsx files\n"
        "  temper    — blocks large/critical commits without review (/temper)\n"
        "  cairn     — nudges toward better commit messages (/cairn-commit)\n"
        "\nBonsai MCP servers registered (started on first use):\n"
        "  bonsai-py — uvx bonsai-python\n"
        "  bonsai-ts — npx --yes bonsai-ts@latest\n"
        "\nBypass any gate: append # aether:skip to your bash command.\n"
        "Disable: vibe --disable-aether\n"
    )


def disable() -> None:
    import tomlkit

    hooks_path = _hooks_toml()
    if not hooks_path.exists():
        print("No hooks.toml found — nothing to disable.")
        return

    doc = tomlkit.parse(hooks_path.read_text(encoding="utf-8"))
    hooks = doc.get("hooks", tomlkit.aot())
    filtered = tomlkit.aot()
    removed = False
    for hook in hooks:
        if hook.get("name") == "aether":
            removed = True
        else:
            filtered.append(hook)

    doc["hooks"] = filtered
    hooks_path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    if removed:
        print(f"✓ Aether hook removed from {hooks_path}")
    else:
        print("Aether hook not found — nothing changed.")

    _remove_agents_md_section()


def status() -> None:
    import tomlkit

    hooks_path = _hooks_toml()
    config_path = _config_toml()

    hook_registered = False
    if hooks_path.exists():
        doc = tomlkit.parse(hooks_path.read_text(encoding="utf-8"))
        hook_registered = any(h.get("name") == "aether" for h in doc.get("hooks", []))

    experimental_on = False
    if config_path.exists():
        config_doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        experimental_on = bool(config_doc.get("enable_experimental_hooks", False))

    active = hook_registered and experimental_on
    print(f"Hook registered:      {'✓' if hook_registered else '✗'}")
    print(f"Experimental hooks:   {'✓' if experimental_on else '✗'}")
    print(f"Status:               {'enabled' if active else 'disabled'}")


_BONSAI_SERVERS = [
    {
        "name": "bonsai-py",
        "transport": "stdio",
        "command": "uvx",
        "args": ["bonsai-python"],
        "prompt": "AST-aware Python refactoring tools (pyfindrefs, pyrename, pymove, pysignature, pygrep, pycallers, pyfindunused)",
    },
    {
        "name": "bonsai-ts",
        "transport": "stdio",
        "command": "npx",
        "args": ["--yes", "bonsai-ts@latest"],
        "prompt": "AST-aware TypeScript/JavaScript refactoring tools (tsfindrefs, tsrename, tsmove, tsmovesymbol, tssignature)",
    },
]


_AGENTS_MD_START = "<!-- aether:bonsai-start -->"
_AGENTS_MD_END = "<!-- aether:bonsai-end -->"
_AGENTS_MD_SECTION = """\
<!-- aether:bonsai-start -->
## AST refactoring tools (bonsai)

You have two MCP servers available for safe, AST-aware code changes on Python
and TypeScript/JavaScript files. Prefer these over shell text tools — they
track imports, re-exports, and type references that grep/sed/mv silently miss.

### When to use bonsai instead of bash

| Task | Use instead of | Tool |
|------|---------------|------|
| Rename a symbol (function, class, variable) | `sed` / manual edit | `pyrename` (Python) · `tsrename` (TS/JS) |
| Move a file and rewrite its imports | `mv` | `pymove` (Python) · `tsmove` (TS/JS) |
| Move a symbol to another module | manual cut-paste | `pymovesymbol` · `tsmovesymbol` |
| Find all references to a symbol | `grep` | `pyfindrefs` · `tsfindrefs` |
| Change a function's signature everywhere | manual search | `pysignature` · `tssignature` |
| Find dead / unreferenced code | manual audit | `pyfindunused` |
| Search with AST context | `grep` | `pygrep` |

Always run mutating tools with `dry_run=true` first, review the diff, then re-run
without the flag to apply.
<!-- aether:bonsai-end -->"""


def _agents_md_path() -> Path:
    return _vibe_home() / "AGENTS.md"


def _write_agents_md() -> None:
    path = _agents_md_path()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    if _AGENTS_MD_START in existing:
        return  # already present

    separator = "\n\n" if existing.strip() else ""
    path.write_text(existing + separator + _AGENTS_MD_SECTION + "\n", encoding="utf-8")
    print(f"✓ Bonsai tool guidance written to {path}")


def _remove_agents_md_section() -> None:
    path = _agents_md_path()
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    start = text.find(_AGENTS_MD_START)
    end = text.find(_AGENTS_MD_END)
    if start == -1 or end == -1:
        return

    before = text[:start].rstrip("\n")
    after = text[end + len(_AGENTS_MD_END):].lstrip("\n")
    cleaned = (before + ("\n" if after else "") + after)
    path.write_text(cleaned, encoding="utf-8")
    print(f"✓ Bonsai tool guidance removed from {path}")


def _register_bonsai_servers(config_doc: dict) -> None:  # type: ignore[type-arg]
    import tomlkit

    existing_raw = list(config_doc.get("mcp_servers") or [])
    existing_names = {s.get("name") for s in existing_raw}

    to_add = [s for s in _BONSAI_SERVERS if s["name"] not in existing_names]
    if not to_add:
        return

    # Rebuild as a proper AoT — avoids the invalid-TOML bug where appending
    # a table() to an existing inline [] produces `mcp_servers = [key = val ...]`.
    aot = tomlkit.aot()
    for entry_data in [*existing_raw, *to_add]:
        entry = tomlkit.table()
        for k, v in dict(entry_data).items():
            if isinstance(v, list):
                arr = tomlkit.array()
                for item in v:
                    arr.append(item)
                entry.add(k, arr)
            else:
                entry.add(k, v)
        aot.append(entry)

    # Remove the old inline [] key, then add the AoT at the same logical slot
    if "mcp_servers" in config_doc:
        del config_doc["mcp_servers"]
    config_doc["mcp_servers"] = aot  # type: ignore[index]

    for spec in to_add:
        print(f"✓ MCP server registered: {spec['name']}")


def _write_skills() -> None:
    import importlib.resources as pkg_resources

    skills_root = _skills_dir()
    try:
        pkg = pkg_resources.files("vibe.core.hooks.aether.skills")
        for skill_dir in pkg.iterdir():
            skill_md = skill_dir / "SKILL.md"
            try:
                content = skill_md.read_text(encoding="utf-8")
            except Exception:
                continue
            dest = skills_root / skill_dir.name / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            print(f"✓ Skill installed: ~/.vibe/skills/{skill_dir.name}/")
    except Exception:
        pass
