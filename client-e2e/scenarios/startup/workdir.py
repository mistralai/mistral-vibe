"""`--workdir` changes the process working directory before startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--workdir", "vibe/cli-rust")
screen_contains = {"rust": ("Cargo.toml",), "python": ("Cargo.toml",)}
timeline: Timeline = ["@Cargo"]
