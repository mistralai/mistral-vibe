"""Validate an installed Vibe wheel owns both native delivery components."""

from __future__ import annotations

from importlib import metadata
import os
from pathlib import Path

import mistralai_vibe_local_harness._native as harness_native

import vibe


def main() -> None:
    vibe_version = metadata.version("mistral-vibe")
    installed_distributions = {
        distribution.metadata["Name"] for distribution in metadata.distributions()
    }
    if "mistralai-vibe-local-harness" in installed_distributions:
        raise RuntimeError("standalone Harness distribution is installed")

    native_path = Path(harness_native.__file__ or "")
    if not native_path.is_file():
        raise RuntimeError("Harness native extension is missing")

    vibe_module_path = vibe.__file__
    if vibe_module_path is None:
        raise RuntimeError("Vibe package location is unavailable")

    rust_cli_name = "vibe-rs.exe" if os.name == "nt" else "vibe-rs"
    rust_cli_path = Path(vibe_module_path).parent / "_bin" / rust_cli_name
    if not rust_cli_path.is_file():
        raise RuntimeError("Rust CLI is missing")
    if os.name != "nt" and not os.access(rust_cli_path, os.X_OK):
        raise RuntimeError("Rust CLI is not executable")

    print(f"mistral-vibe {vibe_version}")
    print(f"Harness extension: {native_path.name}")
    print(f"Rust CLI: {rust_cli_path.name}")


if __name__ == "__main__":
    main()
