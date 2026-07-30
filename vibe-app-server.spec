# -*- mode: python ; coding: utf-8 -*-
# Onedir build for vibe-app-server — no per-launch extraction overhead.
# Build: uv run --group build pyinstaller vibe-app-server.spec
# Output: dist/vibe-app-server-dir/vibe-app-server  (+  dist/vibe-app-server-dir/_internal/)

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

entrypoint = Path("build/vibe-app-server-entrypoint.py")
entrypoint.parent.mkdir(parents=True, exist_ok=True)
entrypoint.write_text(
    "from vibe.app_server.stdio import main\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n",
    encoding="utf-8",
)

_core_builtins_datas, core_builtins_binaries, core_builtins_hidden_imports = (
    collect_all("vibe.core.tools.builtins")
)

# rich lazily loads Unicode width tables via importlib.import_module() at runtime,
# which PyInstaller's static analysis cannot discover.
hidden_imports = ["truststore"] + collect_submodules("rich._unicode_data")
for item in core_builtins_hidden_imports:
    if isinstance(item, str):
        hidden_imports.append(item)

binaries = core_builtins_binaries

datas = collect_data_files("vibe", includes=["**/*.md", "**/*.tcss"])
datas += [("vibe/core/tools/builtins/*.py", "vibe/core/tools/builtins")]
# Built-in skills are read from source files at runtime, so collect_data_files
# must be allowed to include .py files here. By default it filters .py/.pyc out.
datas += collect_data_files(
    "vibe.core.skills.builtins", includes=["*.py"], include_py_files=True
)

a = Analysis(
    [str(entrypoint)],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["pyinstaller/runtime_hook_truststore.py"],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vibe-app-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="vibe-app-server-dir",
)
