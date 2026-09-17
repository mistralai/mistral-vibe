# pyright: reportMissingImports=false
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_MANIFEST = Path("vibe/cli-rust/Cargo.toml")
_BIN = "vibe-rs.exe" if sys.platform == "win32" else "vibe-rs"


# Compiles and bundles vibe-rs for real wheel builds; skips editable installs, VIBE_SKIP_CARGO=1, and missing cargo, honoring CARGO_BUILD_FLAGS.
class CargoBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if version == "editable" or os.environ.get("VIBE_SKIP_CARGO"):
            return
        cmd = [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            str(_MANIFEST),
            "--bin",
            "vibe-rs",
            *shlex.split(os.environ.get("CARGO_BUILD_FLAGS", "")),
        ]
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            # cargo is optional: sdist/pip installs without a Rust toolchain
            # still get the default Python CLI.
            return
        built = Path("vibe/cli-rust/target/release") / _BIN
        build_data["force_include"][str(built)] = f"vibe/_bin/{_BIN}"
        build_data["pure_python"] = False
        # The bundled vibe-rs is a standalone executable, not a Python extension,
        # so the wheel is platform-specific but interpreter-independent. Tag it
        # py3-none-<platform> instead of inferring a cp3XX-cp3XX tag, so a single
        # wheel per platform installs on every supported CPython (>=3.12).
        build_data["tag"] = f"py3-none-{_platform_tag()}"


# packaging.tags derives the macOS platform from platform.mac_ver() (the build
# machine's OS), ignoring MACOSX_DEPLOYMENT_TARGET. Pin the floor to 11.0 so the
# wheel installs on macOS 11+ instead of only the runner's OS version. Other
# platforms use the host-derived tag (manylinux on Linux, win on Windows).
def _platform_tag() -> str:
    if sys.platform == "darwin":
        from packaging.tags import mac_platforms

        return next(mac_platforms((11, 0)))
    from packaging.tags import sys_tags

    return next(sys_tags()).platform
