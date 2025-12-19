# Fix for "Out of Sequence" Error in Middleware Injection

## Problem

The error occurred when middleware tried to inject messages after tool messages were added to the conversation. The LLM API requires that conversation roles alternate properly:
- Valid sequence: `system` → `user` → `assistant` → `tool` → `assistant` → `user`
- Invalid sequence: `system` → `user` → `assistant` → `tool` → `tool` (modified)

When middleware injected a message after a tool message, the code would try to modify the tool message itself, creating an invalid sequence.

## Root Cause

The previous fix (commit 608f5a3) prevented modifying tool messages by checking if the last message was a tool message and skipping injection. However, this approach was incomplete because:

1. It just skipped the injection entirely
2. It didn't handle the case where we need to add a NEW message after a tool message
3. According to LLM API rules, after a tool message, the next message must be from the assistant

## Solution

Modified `vibe/core/agent.py` in the `_handle_middleware_result` method to handle middleware injection after tool messages correctly:

```python
if last_msg.role == Role.tool:
    # When last message is from a tool, we need to add an assistant message first
    # to maintain valid sequence: assistant -> tool -> assistant (with injected content)
    assistant_msg = LLMMessage(role=Role.assistant, content=result.message)
    self.messages.append(assistant_msg)
```

This ensures that when middleware wants to inject a message after a tool message:
1. A new assistant message is created with the injection content
2. This assistant message is appended to the conversation
3. The sequence becomes: `assistant` → `tool` → `assistant` (valid)

## Test Coverage

Added comprehensive tests in `tests/test_agent_middleware_injection_after_tool.py`:

1. `test_injection_after_tool_message_creates_valid_sequence` - Verifies that injection after tool message creates valid sequence
2. `test_injection_after_assistant_message_modifies_it` - Verifies that injection after assistant message modifies it correctly
3. `test_injection_with_empty_assistant_message` - Verifies that injection works when assistant message has no content

All existing tests continue to pass, confirming backward compatibility.

## Impact

This fix ensures that:
- Middleware can safely inject messages at any point in the conversation
- The LLM API role sequence rules are always maintained
- No invalid message sequences are sent to the LLM backend
- The fix is minimal and focused on the specific issue
