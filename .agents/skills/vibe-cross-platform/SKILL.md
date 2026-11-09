---
name: vibe-cross-platform
description: Cross-platform development rules for Mistral Vibe. Use when writing platform-specific logic, handling paths, Windows support, shell compatibility, terminal data streams, or cross-OS testing strategies.
metadata:
  display-name: Vibe Cross-Platform
  short-description: Cross-platform rules and testing for Vibe
  default-prompt: Use $vibe-cross-platform to ensure code and tests work across Linux, macOS, and Windows.
---

# Vibe Cross-Platform

Vibe ships on Linux, macOS, and Windows, but **CI runs the test suite on Linux only**. The machine you develop on is not the machine that gates the PR — code and tests must pass on every platform, and must be *provably* correct on the platforms CI cannot exercise.

## Code rules

- Never rely on host-specific behavior (shell, path separator, line ending, available binaries, `$SHELL`, case sensitivity). Assume the same code runs under `cmd.exe`, Git Bash, `zsh`, and `sh`.
- Branch platform-specific logic behind the helpers in `vibe.core.utils.platform` (`is_windows()`, `resolve_windows_shell()`), not ad-hoc `sys.platform` checks. Keep POSIX-only assumptions (forward slashes, POSIX `shlex` escaping, `$SHELL`) out of shared paths and scope Windows-only handling to `is_windows()`.
- Use `pathlib.Path` for path composition; never hardcode `/` or `\\`.

## Terminal data

- Treat terminal and PTY data as arbitrarily chunked streams: normalize CRLF before interpreting lone `\r` as an in-place redraw, buffer incomplete control sequences until their terminator arrives, and add Linux-runnable regression tests covering CRLF plus every split boundary of representative control sequences.

## Testing

- Test other-platform code paths **on Linux** by monkeypatching `sys.platform`, env vars, and probes like `shutil.which` — do not `@pytest.mark.skipif` them away. A Windows-only behavior with no Linux-runnable test is untested in CI.
- When an assertion depends on the platform, force it explicitly (e.g. `monkeypatch.setattr(module, "is_windows", lambda: True)`) instead of letting the test pass only because of the host it happened to run on. A test that would flip its result on a different OS is a bug.
