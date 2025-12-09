# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['vibe/acp/entrypoint.py'],
    pathex=[],
    binaries=[],
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
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='vibe-acp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
