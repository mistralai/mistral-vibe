# Critique — i-would-like-to-elegant-eclipse.md — 2026-06-15

## Summary

| # | Critic | Severity | Finding | Recommendation |
|---|--------|----------|---------|----------------|
| 1 | Risk | 🔴 | `vibe-aether-*` executables may not be in PATH (uv tool venv bin not on shell PATH) | Use `sys.executable -m vibe.aether.*` at install time |
| 2 | Risk | 🔴 | `cwd` is session dir, not git repo root — plan/state files won't be found from subdirs | Run `git rev-parse --show-toplevel` at top of whetstone and temper |
| 3 | Impl | 🟡 | No test strategy for complex hook logic | Add tests/core/hooks/test_aether_*.py |
| 4 | Impl | 🟡 | Temper state marker semantics underspecified | Use `.git/index` mtime as proxy for last `git add` |
| 5 | Impl | 🟡 | install.py idempotency mechanism unspecified | Scan tomlkit doc["hooks"] by name before appending |
| 6 | Arch | 🟡 | `vibe/aether/` breaks module hierarchy | Use `vibe/core/hooks/aether/` instead |
| 7 | Arch | 🟡 | Skills written at enable-time drift from package on upgrade | Ship skills as package data; re-write on each enable |
| 8 | Risk | 🟡 | 4 subprocess spawns per bash call — latency | Combine into single `vibe-aether` hook script |
| 9 | Risk | 🟡 | Silently enabling experimental hooks | Print warning + require confirmation |
| 10 | Impl | 🟢 | Bonsai regex false positives | Tighten pattern anchoring |
| 11 | Arch | 🟢 | Onboarding screen integration vague | Map push_screen chain before implementing |

**Blockers:** 2 | **Significant:** 6 | **Minor:** 2
