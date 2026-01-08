# Plan: Extending Tool Permissions with Time-Based and Iteration-Based Grants

## Overview

This plan extends the tool permission system to support temporary permissions that expire after a specified duration or number of tool execution steps. Currently, tools support:

- `permission = "always"` - Always allow
- `permission = "never"` - Never allow
- `permission = "ask"` - Ask each time

New permission types:

- `permission = "ask-time"` - Prompt user to grant permission for a specific duration
- `permission = "ask-iterations"` - Prompt user to grant permission for a specific number of execution steps

## Architecture Overview

### Current Flow

1. `Agent._should_execute_tool()` checks tool permission
2. If `ASK`, calls `_ask_approval()` which uses `approval_callback`
3. Approval callback returns `(ApprovalResponse, feedback)` tuple
4. Response is mapped to `ToolDecision` with `EXECUTE` or `SKIP` verdict

### New Components Needed

1. **Temporary Permission Tracker** - Track active temporary permissions (time/iterations remaining)
2. **Extended Permission Enum** - Add `ASK_TIME` and `ASK_ITERATIONS` to `ToolPermission`
3. **Extended Approval Response** - Support returning time/iteration values with approval
4. **UI Updates** - Prompt for duration/iterations in CLI and ACP interfaces
5. **Permission State Management** - Store and check temporary permission state

## Implementation Plan

### Phase 1: Core Permission System Extensions

#### 1.1 Extend `ToolPermission` Enum

**File**: `vibe/core/tools/base.py`

- Add `ASK_TIME = auto()` and `ASK_ITERATIONS = auto()` to `ToolPermission` enum
- Update `by_name()` method to handle new permission types (case-insensitive matching)
- Ensure backward compatibility with existing configs

#### 1.2 Create Temporary Permission Tracker

**New File**: `vibe/core/tools/permission_tracker.py`

Create a class to track temporary permissions with thread-safe operations:

```python
class TemporaryPermission(BaseModel):
    tool_name: str
    expires_at: datetime | None = None  # For time-based
    remaining_iterations: int | None = None  # For iteration-based
    granted_at: datetime

class PermissionTracker:
    _permissions: dict[str, TemporaryPermission]
    _locks: dict[str, asyncio.Lock]  # Per-tool locks for atomic operations

    async def grant_time_based(tool_name: str, duration_seconds: int) -> None
        # Replaces any existing temporary permission (last grant wins)
    async def grant_iteration_based(tool_name: str, iterations: int) -> None
        # Replaces any existing temporary permission (last grant wins)
    async def check_and_reserve_iteration(tool_name: str) -> tuple[bool, str]
        # Returns (is_granted, expiration_reason)
        # Atomic check+decrement, returns reason if expired/exhausted
    async def is_granted(tool_name: str) -> tuple[bool, str]
        # Returns (is_granted, expiration_reason)
        # Check if permission is valid, returns reason if expired
    async def get_remaining_info(tool_name: str) -> dict[str, Any] | None
        # Returns remaining time (seconds) or iterations for display in UI
    async def cleanup_expired() -> None  # Remove expired time-based permissions
```

**Concurrency Safety**:

- Use `asyncio.Lock` per tool_name to serialize permission checks and decrements
- Implement atomic "check-and-reserve" operation to prevent race conditions
- All permission operations must be async and use locks
- Return expiration reason to enable user-friendly re-prompting

**Expiration Reason Values**:

- `"time_expired"` - Time-based permission expired
- `"iterations_exhausted"` - Iteration-based permission exhausted
- `"none"` - Permission still valid

#### 1.3 Extend Approval Response

**File**: `vibe/core/utils.py`

Extend `ApprovalResponse` or create a new response type that includes metadata:

- Option 1: Add new enum values like `YES_TIME(duration)` and `YES_ITERATIONS(count)`
- Option 2: Create `ApprovalResult` dataclass with `response`, `duration`, `iterations` fields
- Option 3: Return tuple `(ApprovalResponse, metadata_dict)` where metadata contains duration/iterations

**Recommendation**: Option 2 - Create `ApprovalResult` class for cleaner type safety.

### Phase 2: Agent Permission Checking Logic

#### 2.1 Update `Agent._should_execute_tool()`

**File**: `vibe/core/agent.py`

Modify permission checking logic with concurrency safety:

1. **Async lock acquisition**: All permission tracker operations must be async
2. Check temporary permission tracker first (before checking config permission):
   - For iteration-based: Use atomic `check_and_reserve_iteration()`
   - For time-based: Use `is_granted()` (read-only check, no state change)
3. If temporary permission exists and valid → allow execution
4. **If temporary permission expired/exhausted** → fall through to normal permission check:
   - Track expiration reason (time expired vs iterations exhausted) for user feedback
   - Pass expiration context to approval callback if re-prompting
5. Handle new permission types `ASK_TIME` and `ASK_ITERATIONS`:
   - Call `_ask_approval()` with permission type context and expiration info
   - Process approval result with duration/iterations
   - Grant temporary permission via tracker (with lock protection)

**Expiration Handling Flow**:

```
Temporary Permission Expired
  ↓
Check Base Config Permission
  ↓
If ASK_TIME or ASK_ITERATIONS:
  → Prompt user again (with expiration notification)
  → User can grant new temporary permission OR grant "always"
If ASK:
  → Prompt user (simple yes/no, with expiration notification)
  → User can grant "always" or "once"
If ALWAYS:
  → Execute immediately (no prompt)
If NEVER:
  → Deny execution
```

**Critical Concurrency Considerations**:

- Multiple tool calls for the same tool can happen concurrently (even if processed sequentially, async operations can interleave)
- Permission check and iteration decrement must be atomic
- Use per-tool locks to serialize operations on the same tool
- Different tools can be checked in parallel (separate locks)

#### 2.2 Update `Agent._ask_approval()`

**File**: `vibe/core/agent.py`

- Update signature to accept:
  - Optional `permission_type: ToolPermission` (to know what type of prompt to show)
  - Optional `expiration_reason: str | None` (to inform user why they're being prompted again)
- Handle new approval result types that include duration/iterations
- Grant temporary permissions via `PermissionTracker` when appropriate
- Return appropriate `ToolDecision`

**Expiration Reason Handling**:

- If temporary permission expired, pass reason to approval callback:
  - `"time_expired"` - Time-based permission expired
  - `"iterations_exhausted"` - Iteration-based permission exhausted
- Approval UI should display this information to user (e.g., "Previous permission expired, grant new permission?")

#### 2.3 Integrate Permission Tracker into Agent

**File**: `vibe/core/agent.py`

- Add `PermissionTracker` instance to `Agent.__init__()`
- **Critical**: Use atomic `check_and_reserve_iteration()` in `_should_execute_tool()`:
  - This atomically checks if iterations remain AND decrements the count
  - Prevents race conditions where multiple concurrent tool calls could all see count > 0
- Only reserve iteration if execution is actually going to proceed
- **Cancelled execution handling**: If execution fails/cancelled, reservation is consumed (not restored) to prevent retry abuse - document this behavior
- Call `tracker.cleanup_expired()` periodically (e.g., before each permission check)

**Execution Flow for Iteration-Based Permissions**:

1. Check permission (atomic: check count > 0 AND decrement) → reserve one iteration
2. If reserved, proceed with execution
3. If execution fails/cancelled, optionally restore the iteration (or accept loss)
4. If execution succeeds, iteration already decremented

**Alternative Approach (Reserve on Success)**:

1. Check permission (read-only: count > 0)
2. Execute tool
3. On success: atomically decrement (with lock)
4. Risk: Multiple concurrent calls could all pass check, but only some succeed

**Recommendation**: Use "reserve then execute" pattern for safety - reserve iteration atomically during permission check, accept that cancelled executions consume the reservation.

### Phase 3: CLI UI Updates

#### 3.1 Extend ApprovalApp Widget

**File**: `vibe/cli/textual_ui/widgets/approval_app.py`

- Add support for time-based and iteration-based prompts
- Add input fields/widgets for:
  - Duration input (seconds/minutes/hours) for `ask-time` with default value of 5 minutes (300 seconds)
  - Iteration count input (integer) for `ask-iterations` with default value of 10 iterations
- **Display expiration notification**: If `expiration_reason` is provided, show message prominently like:
  - "⚠ Previous time-based permission expired. Grant new permission?"
  - "⚠ Previous permission exhausted (0 iterations remaining). Grant new permission?"
- **Display remaining status**: If temporary permission is active, show remaining time/iterations in the prompt
- Update `_handle_selection()` to capture duration/iterations when applicable
- Add new message types:
  - `ApprovalGrantedTimeBased(tool_name, tool_args, duration_seconds)`
  - `ApprovalGrantedIterationBased(tool_name, tool_args, iterations)`
- **Allow "always" grant even for temporary permission prompts**: User should be able to grant permanent permission instead of temporary
- **Save "always" to config**: When user grants "always", persist to config file (overrides base permission setting)

#### 3.2 Update VibeApp Approval Handlers

**File**: `vibe/cli/textual_ui/app.py`

- Add handlers for new approval message types
- Update `_pending_approval` result to include duration/iterations
- Ensure approval callback receives proper metadata

#### 3.3 Update Approval Callback

**File**: `vibe/cli/textual_ui/app.py`

The approval callback in `_create_approval_callback()` needs to:

- Accept `expiration_reason` parameter (if provided)
- Accept and return duration/iterations metadata
- Map UI approval responses to `ApprovalResult` objects
- Pass expiration reason to UI so user understands why they're being prompted again

### Phase 4: ACP (Agent Communication Protocol) Updates

#### 4.1 Extend ToolOption Enum

**File**: `vibe/acp/utils.py`

- Add new options:
  - `ALLOW_TIME = "allow_time"`
  - `ALLOW_ITERATIONS = "allow_iterations"`
- Update `TOOL_OPTIONS` list with new permission options
- These will require additional metadata in the ACP protocol

#### 4.2 Update ACP Permission Request

**File**: `vibe/acp/acp_agent.py`

- Modify `_create_approval_callback()` to handle new permission types
- Update `_handle_permission_selection()` to process time/iteration-based approvals
- **ACP Protocol Limitation**: ACP `PermissionOption` schema only supports `optionId`, `name`, and `kind` fields
- **Workaround Options**:
  - Option A: Encode permission type in `name` field (e.g., "Allow for 5 minutes", "Allow for 10 iterations")
  - Option B: Two-step process: first request approval, then prompt for duration/iterations via separate mechanism
  - Option C: Extend ACP protocol (requires coordination with ACP library maintainers)
- **Recommendation**: Defer ACP implementation until CLI is complete, then evaluate best approach

**Note**: ACP protocol extension may require coordination with the ACP library/specification. For initial implementation, focus on CLI support.

### Phase 5: Configuration and Persistence

#### 5.1 Config File Support

**File**: `vibe/core/config.py`

- Ensure `ToolPermission.by_name()` handles `"ask-time"` and `"ask-iterations"` (with hyphens)
- Config parsing should convert hyphenated strings to enum values
- No changes needed to config structure (permissions already stored as strings)
- **Permission persistence**: When user grants "always" permission, save to config file using `VibeConfig.save_updates()`
- **Precedence**: Most recent permission selection (including "always") should be saved and take precedence over base config

#### 5.2 Temporary Permission Persistence (Optional)

**Consideration**: Should temporary permissions persist across sessions?

- **Option A**: In-memory only (simpler, resets on restart)
- **Option B**: Persist to disk (more complex, requires serialization)
- **Recommendation**: Start with Option A, add Option B later if needed

### Phase 6: Testing

#### 6.1 Unit Tests

- Test `PermissionTracker` class (grant, check, expiration, iteration counting)
- Test `ToolPermission.by_name()` with new values
- Test `Agent._should_execute_tool()` with temporary permissions
- Test permission expiration and iteration exhaustion

#### 6.2 Integration Tests

- Test CLI approval flow with time-based permissions
- Test CLI approval flow with iteration-based permissions
- Test ACP approval flow (if protocol supports it)
- Test permission state across multiple tool executions
- Test permission expiration during active session

#### 6.3 Edge Cases

- **Permission expiration scenarios**:
  - Time-based permission expires between tool calls → user re-prompted with expiration notification
  - Iteration-based permission exhausted (last iteration used) → user re-prompted with expiration notification
  - Permission expires mid-execution → current execution completes, next call prompts
  - User grants new temporary permission after expiration → new permission active
  - User grants "always" after expiration → permanent permission set, no more prompts
- **Re-prompting behavior**:
  - Verify expiration reason is communicated to user
  - Verify user can grant "always" even when base config is "ask-time" or "ask-iterations"
  - Verify user can grant new temporary permission with different duration/iterations
- What happens when iteration count reaches zero during a batch of tool calls?
- **Concurrent tool execution with shared temporary permissions**:
  - Test multiple concurrent tool calls for same tool with iteration-based permission
  - Verify iteration count never goes negative
  - Verify exactly N iterations are consumed for N successful executions
  - Test race condition: two calls check permission simultaneously
- **Cancelled/failed executions**: Do they consume iteration count? (Recommend: yes, to prevent retry abuse)
- Config changes while temporary permission is active
- **Lock contention**: Test behavior when many tools are checked simultaneously

## Implementation Order

1. **Phase 1.1 & 1.2**: Core enum and tracker (foundation) - **CRITICAL: Implement locks and atomic operations**
2. **Phase 2**: Agent logic updates (core functionality) - **CRITICAL: Use atomic reservation in permission checks**
3. **Phase 3**: CLI UI (user-facing feature) - **Priority: Complete CLI implementation first**
4. **Phase 5.1**: Config support (ensure it works end-to-end) - **CRITICAL: Permission persistence**
5. **Phase 6**: Testing (validate implementation) - **CRITICAL: Include concurrency tests**
6. **Phase 4**: ACP updates (deferred - requires protocol workaround or extension)

**Note**: ACP implementation is deferred due to protocol limitations. CLI implementation should be completed first, then ACP support can be evaluated based on available workarounds.

## Concurrency Safety Requirements

### Critical: Race Condition Prevention

The iteration counting system must be thread-safe to prevent race conditions:

**Problem Scenario**:

1. Tool has 1 iteration remaining
2. Two tool calls happen concurrently (async interleaving)
3. Both check `remaining_iterations > 0` → both see `True`
4. Both decrement → count goes to -1 (incorrect!)

**Solution**: Atomic Check-and-Reserve

```python
async def check_and_reserve_iteration(self, tool_name: str) -> bool:
    """Atomically check if iteration available and reserve it.

    Returns True if iteration was successfully reserved, False if exhausted.
    This operation is atomic and thread-safe.
    """
    async with self._get_lock(tool_name):
        perm = self._permissions.get(tool_name)
        if perm is None:
            return False
        if perm.remaining_iterations is None:
            return True  # Time-based, no iteration limit
        if perm.remaining_iterations > 0:
            perm.remaining_iterations -= 1
            if perm.remaining_iterations == 0:
                # Auto-cleanup when exhausted
                del self._permissions[tool_name]
            return True
        return False
```

### Lock Strategy

- **Per-tool locks**: Each tool_name has its own `asyncio.Lock`
- **Lock scope**: Locks held only during permission state modification (minimal duration)
- **Parallel operations**: Different tools can be checked in parallel (no contention)
- **Lock acquisition**: Lazy initialization of locks (create on first use)

### Implementation Pattern

```python
class PermissionTracker:
    def __init__(self):
        self._permissions: dict[str, TemporaryPermission] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_lock = asyncio.Lock()  # For lock dictionary access

    async def _get_lock(self, tool_name: str) -> asyncio.Lock:
        """Get or create lock for tool_name."""
        async with self._lock_lock:
            if tool_name not in self._locks:
                self._locks[tool_name] = asyncio.Lock()
            return self._locks[tool_name]

    async def check_and_reserve_iteration(self, tool_name: str) -> bool:
        async with await self._get_lock(tool_name):
            # Atomic operation here
            ...
```

### Testing Concurrency

Must test:

1. **Concurrent same-tool calls**: 10 concurrent calls with 5 iterations → exactly 5 succeed
2. **Concurrent different-tool calls**: No lock contention between different tools
3. **Exhaustion edge case**: Last iteration with concurrent requests
4. **Lock performance**: Verify minimal overhead

## Design Decisions

### Decision 1: Permission Check Order

**Decision**: Check temporary permissions before config permissions
**Rationale**: Temporary grants should override base config, but expire naturally

### Decision 2: Iteration Counting

**Decision**: Count each tool execution, not each approval
**Rationale**: More intuitive - "allow 5 uses" means 5 executions, not 5 approvals

### Decision 2a: Iteration Reservation Strategy

**Decision**: Use "reserve then execute" pattern - atomically reserve iteration during permission check
**Rationale**: Prevents race conditions where multiple concurrent calls could all see count > 0. If execution is cancelled/fails, the reservation is consumed (prevents retry abuse).

### Decision 2b: Concurrency Safety

**Decision**: Use `asyncio.Lock` per tool_name to serialize permission operations
**Rationale**:

- Ensures atomic check-and-decrement operations
- Prevents race conditions in async environment
- Different tools can operate in parallel (separate locks)
- Minimal performance impact (locks only held during permission check, not execution)

### Decision 3: Time Granularity

**Decision**: Store duration in seconds, allow user input in various units (seconds/minutes/hours)
**Rationale**: Flexible for users, precise internally

### Decision 4: Permission State Storage

**Decision**: Store in Agent instance (in-memory)
**Rationale**: Simpler implementation, permissions are session-scoped

### Decision 5: Approval Result Type

**Decision**: Create `ApprovalResult` dataclass instead of extending enum
**Rationale**: Cleaner type safety, easier to extend in future

### Decision 6: Expiration Handling

**Decision**: When temporary permission expires, fall back to base config permission and re-prompt user with expiration notification
**Rationale**:

- User-friendly: User understands why they're being prompted again
- Flexible: User can grant new temporary permission or switch to "always"
- Transparent: Expiration reason is clearly communicated
- Consistent: Works the same for time-based and iteration-based permissions

## Resolved Questions

1. **ACP Protocol Compatibility**:

   - **Status**: ACP `PermissionOption` schema supports `optionId`, `name`, and `kind` fields only
   - **Limitation**: The ACP protocol does not natively support passing duration/iteration metadata in permission requests
   - **Solution**: For ACP, we'll need to either:
     - Use the `name` field to encode permission type (e.g., "Allow for 5 minutes", "Allow for 10 iterations")
     - Request permission twice: first for approval, then prompt for duration/iterations via a separate mechanism
     - Extend ACP protocol (requires coordination with ACP library maintainers)
   - **Recommendation**: Start with CLI implementation, defer ACP until protocol extension or workaround is determined

2. **Permission Precedence**:

   - **Decision**: The most recent selection takes precedence and is persistent in the configuration file
   - **Implementation**: When user grants "always" permission, it should be saved to config file and override base `ask-time`/`ask-iterations` setting
   - **Behavior**: If user grants "always" for a tool with `permission = "ask-time"`, the config is updated to `permission = "always"` and no more prompts occur

3. **UI/UX for Remaining Time/Iterations**:

   - **Idiomatic Pattern**: Based on codebase analysis:
     - Status information is displayed in widgets (like `ContextProgress` in bottom bar)
     - Approval prompts use `ApprovalApp` widget with Static widgets for options
     - Status text is shown inline in widgets, not tooltips
   - **Implementation**:
     - Show expiration/remaining info directly in the `ApprovalApp` widget (as part of the prompt text)
     - Display remaining iterations/time in the approval prompt itself
     - Optionally show in status area if permission is active (similar to `ContextProgress` pattern)

4. **Default Values**:

   - **Confirmed**: 5 minutes for time-based, 10 iterations for iteration-based
   - **Implementation**: Pre-fill input fields with these defaults in the approval prompt

5. **Multiple Temporary Permissions**:

   - **Confirmed**: Last grant wins - when a new temporary permission is granted, it replaces any existing temporary permission for that tool
   - **Implementation**: `PermissionTracker.grant_time_based()` and `grant_iteration_based()` should replace existing permissions

6. **Cancelled Execution Handling**:

   - **Confirmed**: Reservation is consumed (not restored) to prevent retry abuse
   - **Documentation**: Document this behavior clearly in code comments and user-facing documentation

7. **Expiration Notification**:
   - **Confirmed**: Explicit notification - show "Previous permission expired" message in prompt
   - **Implementation**: Display expiration reason prominently in `ApprovalApp` widget when re-prompting

## Files to Modify

### Core Files

- `vibe/core/tools/base.py` - Extend `ToolPermission` enum
- `vibe/core/agent.py` - Update permission checking logic
- `vibe/core/utils.py` - Extend approval response types
- `vibe/core/tools/manager.py` - (No changes needed, but verify compatibility)

### New Files

- `vibe/core/tools/permission_tracker.py` - Temporary permission tracking

### UI Files

- `vibe/cli/textual_ui/widgets/approval_app.py` - Add time/iteration input UI
- `vibe/cli/textual_ui/app.py` - Update approval handlers

### ACP Files

- `vibe/acp/utils.py` - Add new tool options
- `vibe/acp/acp_agent.py` - Update approval callback

### Config Files

- `vibe/core/config.py` - Verify config parsing (likely no changes)

### Test Files

- `tests/core/test_tool_permissions.py` - New test file
- `tests/agent/test_temporary_permissions.py` - New test file
- Update existing approval tests

## Success Criteria

### Core Functionality (Required)

1. ✅ Users can set `permission = "ask-time"` in config
2. ✅ Users can set `permission = "ask-iterations"` in config
3. ✅ CLI prompts for duration when `ask-time` is used (default: 5 minutes)
4. ✅ CLI prompts for iteration count when `ask-iterations` is used (default: 10 iterations)
5. ✅ Temporary permissions expire after specified time
6. ✅ Temporary permissions expire after specified iterations
7. ✅ Permission state is checked before each tool execution
8. ✅ Expired permissions fall back to base permission config with explicit notification
9. ✅ User can grant "always" permission which persists to config file
10. ✅ Most recent permission selection takes precedence and is saved
11. ✅ Tests cover all new functionality including concurrency
12. ✅ Documentation updated (README.md)

### ACP Support (Deferred)

- ACP implementation deferred until protocol workaround or extension is available
- CLI implementation must be complete and tested first

## Future Enhancements (Out of Scope)

- Permission persistence across sessions
- Permission templates/presets
- Permission audit logging
- Permission revocation UI
- Batch permission grants for multiple tools
- Permission inheritance from tool groups
