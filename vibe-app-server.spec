# -*- mode: python ; coding: utf-8 -*-
# Onedir build for vibe-app-server — no per-launch extraction overhead.
# Build: uv run --group build pyinstaller vibe-app-server.spec
# Output: dist/vibe-app-server-dir/vibe-app-server  (+  dist/vibe-app-server-dir/_internal/)
# UPX stays off: it rewrites the Mach-O header and invalidates the macOS code signature.

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# The Unified Runtime and its native extension are required contents of this
# executable; the build fails when they cannot be collected.
sys.path.insert(0, os.path.join(SPECPATH, "pyinstaller"))
from unified_runtime import collect_unified_runtime

_core_builtins_datas, core_builtins_binaries, core_builtins_hidden_imports = (
    collect_all("vibe.core.tools.builtins")
)
_harness_datas, harness_binaries, harness_hidden_imports = collect_unified_runtime()

# rich lazily loads Unicode width tables via importlib.import_module() at runtime,
# which PyInstaller's static analysis cannot discover.
hidden_imports = [
    "truststore",
    "vibe.app_server._unified_harness_backend_adapter",
] + collect_submodules("rich._unicode_data")
for item in core_builtins_hidden_imports + harness_hidden_imports:
    if isinstance(item, str):
        hidden_imports.append(item)

binaries = core_builtins_binaries + harness_binaries

# gh and glab release binaries staged by scripts/ci/download-cli-binaries.sh.
# They land in _internal/vibe/bin/, where the session artifact detector looks
# them up (approach C). Absent in dev builds: detection falls back to PATH.
_cli_bin_dir = os.path.join(SPECPATH, ".native-build", "cli-binaries")
for _cli_name in ("gh", "glab"):
    _cli_exe = f"{_cli_name}.exe" if sys.platform == "win32" else _cli_name
    _cli_src = os.path.join(_cli_bin_dir, _cli_exe)
    if os.path.isfile(_cli_src):
        binaries.append((_cli_src, "vibe/bin"))

datas = collect_data_files(
    "vibe",
    includes=[
        "core/prompts/*.md",
        "core/tools/builtins/prompts/*.md",
    ],
)
datas += [("vibe/core/tools/builtins/*.py", "vibe/core/tools/builtins")]
# Built-in skills are read from source files at runtime, so collect_data_files
# must be allowed to include .py files here. By default it filters .py/.pyc out.
datas += collect_data_files(
    "vibe.core.skills.builtins", includes=["*.py"], include_py_files=True
)
datas += _harness_datas

a = Analysis(
    ["vibe/app_server/entrypoint.py"],
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
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name="vibe-app-server-dir",
)
