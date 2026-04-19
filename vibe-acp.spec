# -*- mode: python ; coding: utf-8 -*-
# Onedir build for vibe-acp — no per-launch extraction overhead.
# Build: uv run --group build pyinstaller vibe-acp.spec
# Output: dist/vibe-acp-dir/vibe-acp  (+  dist/vibe-acp-dir/_internal/)

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect all dependencies (including hidden imports and binaries) from builtins modules
core_builtins_deps = collect_all('vibe.core.tools.builtins')
acp_builtins_deps = collect_all('vibe.acp.tools.builtins')

# Extract hidden imports and binaries, filtering to ensure only strings are in hiddenimports
# rich lazily loads Unicode width tables via importlib.import_module() at runtime,
# which PyInstaller's static analysis cannot discover.
hidden_imports = ["truststore"] + collect_submodules("rich._unicode_data")
for item in core_builtins_deps[2] + acp_builtins_deps[2]:
    if isinstance(item, str):
        hidden_imports.append(item)

binaries = core_builtins_deps[1] + acp_builtins_deps[1]

a = Analysis(
    ['vibe/acp/entrypoint.py'],
    pathex=[],
    binaries=binaries,
    datas=[
        # By default, pyinstaller doesn't include the .md files
        ('vibe/core/prompts/*.md', 'vibe/core/prompts'),
        ('vibe/core/tools/builtins/prompts/*.md', 'vibe/core/tools/builtins/prompts'),
        # We also need to add all setup files
        ('vibe/setup/*', 'vibe/setup'),
        # This is necessary because tools are dynamically called in vibe, meaning there is no static reference to those files
        ('vibe/core/tools/builtins/*.py', 'vibe/core/tools/builtins'),
        ('vibe/acp/tools/builtins/*.py', 'vibe/acp/tools/builtins'),
    ],
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
    name='vibe-acp',
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
    name='vibe-acp-dir',
)
