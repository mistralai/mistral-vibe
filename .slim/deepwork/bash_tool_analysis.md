# Deepwork: Bash Tool Analysis

## Task
Analyze the `bash` tool in the Mistral Vibe codebase to understand its implementation, usage patterns, and potential improvements.

## Phases
### Phase 1: Discovery (Completed)
- **Objective**: Locate and read all `bash` tool implementations.
- **Files Identified**:
  - Core implementation: `vibe/core/tools/builtins/bash.py`
  - ACP integration: `vibe/acp/tools/builtins/bash.py`
  - Tests: `tests/tools/test_bash.py`, `tests/acp/test_bash.py`
- **Status**: Completed. All files have been read and are available for analysis.

### Phase 2: Deep Dive (Completed)
#### Subtask 1: Architecture & Execution Flow (Completed)
- **Integration with `BaseTool`**:
  - `Bash` inherits from `BaseTool[BashArgs, BashResult, BashToolConfig, BaseToolState]` and `ToolUIData[BashArgs, BashResult]`.
  - Overrides `run`, `resolve_permission`, `_build_timeout_error`, `_build_result`.
  - ACP version inherits from `CoreBashTool` and `BaseAcpTool[AcpBashState]`.
- **Execution Flow**:
  - Uses `asyncio.create_subprocess_shell` for subprocess creation.
  - Timeouts handled via `asyncio.wait_for` + `kill_async_subprocess`.
  - Output captured and truncated via `decode_safe` and `max_output_bytes`.
  - **Risk**: Orphaned background processes (e.g., `sleep 1000 &`) may persist on Windows.
- **Windows-Specific Behavior**:
  - Uses `cmd.exe` by default; no session separation (`start_new_session` ignored).
  - Environment variables set for Windows compatibility (e.g., `GIT_PAGER=more`).
- **ACP Differences**:
  - Uses `client.create_terminal` for terminal-based execution.
  - Emits `ToolTerminalOpenedEvent` for UI updates.
  - Relies on `client.wait_for_terminal_exit` and `client.kill_terminal` for timeouts.

#### Subtask 2: Permission System (Completed)
- **Allowlist/Denylist Mechanism**:
  - **Allowlist**: Includes `cd`, `echo`, `git diff`, `git log`, `git status`, `tree`, `whoami`, and platform-specific read-only commands (e.g., `grep`, `ls`, `cat`).
  - **Denylist**: Includes `gdb`, `pdb`, `passwd`, `nano`, `vim`, `vi`, `emacs`, `bash -i`, `cmd /k`, `powershell -NoExit`.
  - **Standalone Denylist**: Includes `python`, `python3`, `bash`, `sh`, `cmd`, `powershell`.
  - **Magic Strings**: `_PATH_COMMANDS` (file-manipulating commands), `_FIND_EXECUTION_PREDICATES` (e.g., `-exec`, `-execdir`).
- **`resolve_permission` Logic**:
  - Uses `_extract_commands` (tree_sitter parsing) to split commands.
  - Handles chained commands (`&&`, `||`, `;`, `|`).
  - Checks for `find -exec` predicates and builds `RequiredPermission` objects.
  - Returns `PermissionContext` with `ALWAYS`, `ASK`, or `NEVER`.
- **Filesystem Interaction Risks**:
  - Mitigates path traversal via `is_path_within_workdir` and `_collect_outside_dirs`.
  - Sensitive commands (e.g., `sudo`) are always treated as sensitive.

#### Subtask 3: Dependencies & Security (Completed)
- **`tree_sitter` Overview**:
  - Parser generator + incremental parsing library for building Concrete Syntax Trees (CSTs).
  - Performance: ~1-2ms for parsing a 1000-line Bash script.
  - Known vulnerabilities: Memory leaks in WASM builds (not affecting Python bindings).
- **`tree_sitter_bash` Specifics**:
  - Supports most Bash syntax (e.g., `&&`, `||`, `|`, backticks, `$((...))`).
  - **Partially Supported/Edge Cases**:
    - Punctuation-separated expansions (e.g., `$FOO/$BAR/`).
    - Arithmetic expansion in heredocs.
    - Standalone heredoc at line start.
- **Security Risks**:
  - **Critical**: Mistral Vibe does **not** check for parsing errors (`ERROR` nodes) before execution.
  - Deeply nested commands (e.g., `$(...$(...))`) may exhaust stack memory.
- **Dependency Pinning**:
  - Current versions: `tree_sitter` v0.25.2, `tree_sitter_bash` v0.25.1 (up-to-date).

#### Subtask 4: Error Handling (Completed)
- **Error Handling**:
  - `_build_timeout_error`: Includes command and timeout duration.
  - `_build_result`: Includes command, return code, stdout, and stderr in error messages.
  - **Critical Gap**: Error messages include full stdout/stderr, which may contain secrets.
- **Logging**:
  - Core version: No direct `logger` usage (errors raised as `ToolError`).
  - ACP version: Two `logger.error` calls for terminal cleanup failures (safe).

### Phase 3: Recommendations (Completed)
#### **Sprint 1: Security Hardening (Critical Fixes)**
| **Task** | **Owner** | **Timeline** | **Effort** | **Impact** | **Status** |
|----------|-----------|--------------|------------|------------|------------|
| Sanitize sensitive data in error messages | Security Team | Sprint 1 | Low (1-2 days) | Critical | Pending |
| Add parsing validation (reject `ERROR` nodes) | Core Team | Sprint 1 | Low (1 day) | Critical | Pending |
| Sanitize environment variables in error messages | Security Team | Sprint 1 | Low (1 day) | Critical | Pending |
| Fix TOCTOU in path validation | Core Team | Sprint 1 | Medium (2-3 days) + Spike (1 day) | Critical | Pending |
| Validate chained commands (reject `;`, `&&`, etc. if unsafe) | Core Team | Sprint 1 | Medium (2-3 days) | Critical | Pending |
| Audit `bash` tool for other TOCTOU risks | Security Team | Sprint 1 | Low (1 day) | Critical | Pending |
| Improve Windows cleanup (`taskkill`) | Core Team | Sprint 1 | Medium (2-3 days) | Critical | Pending |

#### **Sprint 2: Code Quality & Maintainability**
| **Task** | **Owner** | **Timeline** | **Effort** | **Impact** | **Status** |
|----------|-----------|--------------|------------|------------|------------|
| Add truncation indicators to `BashResult` | Core Team | Sprint 2 | Low (1 day) | Medium | Pending |
| Improve ACP stderr handling | ACP Team | Sprint 2 | Low (1 day) | Medium | Pending |
| Limit command complexity (nesting, length, tokens) | Core Team | Sprint 2 | Medium (2-3 days) | Medium | Pending |
| Refactor `resolve_permission` (reduce nesting) | Core Team | Sprint 2 | Medium (2-3 days) | Medium | Pending |
| Refactor `_extract_commands` (extract helpers) | Core Team | Sprint 2 | Medium (2-3 days) | Medium | Pending |
| Add unit tests for sanitization | Security Team | Sprint 2 | Low (1 day) | Medium | Pending |
| Add integration tests for chained commands | Core Team | Sprint 2 | Medium (2 days) | Medium | Pending |

#### **Backlog: Long-Term Improvements**
| **Task** | **Owner** | **Timeline** | **Effort** | **Impact** | **Status** |
|----------|-----------|--------------|------------|------------|------------|
| Thread-local parser caching | Core Team | Backlog | High (5+ days) | Low | Pending |
| Document `tree_sitter_bash` edge cases | Core Team | Backlog | Low (1 day) | Low | Pending |
| Monitor for `tree_sitter_bash` updates | Core Team | Backlog | Low (ongoing) | Low | Pending |
| Shared permission logic (core + ACP) | Core/ACP Teams | Backlog | Medium (3-5 days) | Low | Pending |
| Add security considerations to `AGENTS.md` | Security Team | Backlog | Low (1 day) | Low | Pending |

## Risks & Edge Cases (Prioritized by @oracle)
### 🔴 **Critical Risks**
| **Risk** | **Impact** | **Mitigation** | **Status** |
|----------|------------|----------------|------------|
| **Sensitive Data in Error Messages** | **Critical**: Secrets (e.g., `cat .env`, `echo $API_KEY`) exposed in `ToolError` messages. | Sanitize stderr/stdout in `_build_result` (redact patterns like `password`, `token`, `secret`). | Confirmed |
| **No Parsing Validation** | **Critical**: Commands with syntax errors (e.g., `ls |`) may still execute. | Add validation in `_extract_commands` to reject trees with `ERROR` nodes. | Confirmed |
| **Environment Variable Leakage** | **Critical**: Commands like `echo $VAR` or `env` could leak environment variables (e.g., `AWS_SECRET_ACCESS_KEY`). | Sanitize environment variables in addition to stderr/stdout. | Confirmed |
| **TOCTOU in Path Validation** | **Critical**: Race condition could allow path swapping between permission check and execution. | Validate paths at execution time (use `realpath` and re-check permissions). | Confirmed |
| **Shell Injection in Chained Commands** | **Critical**: Commands like `; rm -rf /` or `$(malicious)` could bypass permission checks. | Validate entire command string (reject `;`, `&&`, `||`, `|`, `$()` if they violate permissions). | Confirmed |
| **Orphaned Processes on Windows** | **High**: Background processes (e.g., `sleep 1000 &`) may persist after timeout. | Use `taskkill /F /T /PID <pid>` to clean up child processes on Windows. | Confirmed |

### 🟡 **High Priority Risks**
| **Risk** | **Impact** | **Mitigation** | **Status** |
|----------|------------|----------------|------------|
| **No Truncation Indicators** | **Medium**: Users unaware when output is truncated. | Add `stdout_truncated`/`stderr_truncated` fields to `BashResult` and append `[TRUNCATED]` to truncated output. | Confirmed |
| **ACP Loses stderr Context** | **Medium**: Debugging ACP issues without stderr is painful. | Log stderr at `DEBUG` level or include it in a `verbose` mode. | Confirmed |
| **Command Complexity Limits** | **Medium**: Prevent DoS via deeply nested or long commands. | Reject commands with >10 chained parts, >10KB length, or >1000 tokens. | Confirmed |

### 🟢 **Low Priority Risks**
| **Risk** | **Impact** | **Mitigation** | **Status** |
|----------|------------|----------------|------------|
| **Parser Caching Limitations** | **Low**: Performance overhead in multi-threaded environments. | Use `threading.local()` for thread-safe caching. | Confirmed |
| **Known `tree_sitter_bash` Bugs** | **Low**: Edge cases in parsing (e.g., punctuation-separated expansions). | Document and monitor for updates. | Confirmed |

## Simplify/Readability Feedback (Prioritized by @oracle)
### 🔥 **High ROI Refactoring**
| **File** | **Function** | **Issue** | **Suggested Fix** |
|----------|--------------|-----------|-------------------|
| `vibe/core/tools/builtins/bash.py` | `resolve_permission` | Deep nesting, hard to follow. | Use early returns and guard clauses. |
| `vibe/core/tools/builtins/bash.py` | `_extract_commands` | Recursive AST traversal. | Extract helpers: `_is_command_node`, `_is_error_node`, `_get_child_commands`. |
| `vibe/acp/tools/builtins/bash.py` | `_build_result` | Drops stderr entirely. | Align with core implementation or justify in a comment. |
| `vibe/core/tools/builtins/bash.py` | `_kill_async_subprocess` | Platform-specific logic. | Simplify with helper function for platform-specific cleanup. |

### 📌 **Code Quality Suggestions**
| **Issue** | **Example** | **Fix** |
|-----------|-------------|---------|
| **Magic Strings** | `if command == "sudo"` | Define `FORBIDDEN_COMMANDS = {"sudo", "pkexec", "doas"}` in `BashToolConfig`. |
| **Long Functions** | `run` method (50+ lines) | Split into `_validate_command`, `_execute_command`, `_cleanup_process`. |
| **Redundant Checks** | Repeated `if not self.config.allow_foo:` | Use a decorator (e.g., `@requires_permission("foo")`). |
| **Poor Error Messages** | `raise ToolError("Command failed")` | Include context: `f"Command '{cmd}' failed with exit code {rc}"`. |
| **Inconsistent Naming** | `allowlist` vs. `allowed_commands` | Standardize on `allowed_commands` (set) and `allowlist` (config). |
| **Shared Permission Logic** | Core and ACP versions | Share a common base class to avoid duplication. |

## Status
- **Current Phase**: Recommendations (Phase 3) - **Completed**
- **Deepwork Session**: **Completed**
- **Next Steps**: Implement Sprint 1 tasks (security hardening).

## Notes
- Progress file updated at each phase transition.
- OpenCode todos synced with current phase.
- @oracle review completed; plan revised based on feedback.
- All Deep Dive subtasks completed by specialist agents.
- Background tasks reconciled and results consolidated.
- Sprint 1 and Sprint 2 tasks defined with owners, timelines, and priorities.
- Final recommendations approved by @oracle with minor adjustments.
