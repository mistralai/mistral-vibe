from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import time
from typing import TYPE_CHECKING, Any, cast

from vibe.core.config import VibeConfig
from vibe.core.llm.format import APIToolFormatHandler, ResolvedMessage
from vibe.core.tool_manager import ToolManager
from vibe.core.types import (
    AgentStats,
    ApprovalCallback,
    ApprovalResponse,
    LLMMessage,
    SyncApprovalCallback,
    ToolCallEvent,
    ToolDecision,
    ToolExecutionResponse,
    ToolPermission,
    ToolResultEvent,
)
from vibe.core.utils import CancellationReason, get_user_cancellation_message

if TYPE_CHECKING:
    from vibe.cli.mode_manager import ModeManager
    from vibe.core.interaction_logger import InteractionLogger

TOOL_ERROR_TAG = "tool_error"


class AgentToolExecutor:
    """Handles the execution of tools for the Agent, including approval and mode checks."""

    def __init__(
        self,
        config: VibeConfig,
        tool_manager: ToolManager,
        format_handler: APIToolFormatHandler,
        mode_manager: ModeManager | None = None,
        auto_approve: bool = False,
    ) -> None:
        self.config = config
        self.tool_manager = tool_manager
        self.format_handler = format_handler
        self.mode_manager = mode_manager
        self.auto_approve = auto_approve
        self.approval_callback: ApprovalCallback | None = None

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        """Set the callback for user approval of tool execution."""
        self.approval_callback = callback

    async def handle_tool_calls(
        self,
        resolved: ResolvedMessage,
        messages: list[LLMMessage],
        stats: AgentStats,
        interaction_logger: InteractionLogger,
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent]:
        """Process and execute tool calls from the LLM."""
        # 1. Handle failed parses
        for failed in resolved.failed_calls:
            error_msg = f"<{TOOL_ERROR_TAG}>{failed.tool_name}: {failed.error}</{TOOL_ERROR_TAG}>"

            yield ToolResultEvent(
                tool_name=failed.tool_name,
                tool_class=None,
                error=error_msg,
                tool_call_id=failed.call_id,
            )

            stats.tool_calls_failed += 1
            messages.append(
                self.format_handler.create_failed_tool_response_message(
                    failed, error_msg
                )
            )

        # 2. Handle valid calls
        for tool_call in resolved.tool_calls:
            tool_call_id = tool_call.call_id

            yield ToolCallEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                args=tool_call.validated_args,
                tool_call_id=tool_call_id,
            )

            # A. Get Tool Instance
            try:
                tool_instance = self.tool_manager.get(tool_call.tool_name)
            except Exception as exc:
                error_msg = f"Error getting tool '{tool_call.tool_name}': {exc}"
                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    error=error_msg,
                    tool_call_id=tool_call_id,
                )
                messages.append(
                    LLMMessage.model_validate(
                        self.format_handler.create_tool_response_message(
                            tool_call, error_msg
                        )
                    )
                )
                continue

            # B. Check Permissions & Approval
            decision = await self._should_execute_tool(
                tool_instance, tool_call.args_dict, tool_call_id
            )

            if decision.verdict == ToolExecutionResponse.SKIP:
                stats.tool_calls_rejected += 1
                skip_reason = decision.feedback or str(
                    get_user_cancellation_message(
                        CancellationReason.TOOL_SKIPPED, tool_call.tool_name
                    )
                )

                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    skipped=True,
                    skip_reason=skip_reason,
                    tool_call_id=tool_call_id,
                )

                messages.append(
                    LLMMessage.model_validate(
                        self.format_handler.create_tool_response_message(
                            tool_call, skip_reason
                        )
                    )
                )
                continue

            # C. Execute Tool
            stats.tool_calls_agreed += 1

            try:
                start_time = time.perf_counter()
                result_model = await tool_instance.invoke(**tool_call.args_dict)
                duration = time.perf_counter() - start_time

                text = "\n".join(
                    f"{k}: {v}" for k, v in result_model.model_dump().items()
                )

                messages.append(
                    LLMMessage.model_validate(
                        self.format_handler.create_tool_response_message(
                            tool_call, text
                        )
                    )
                )

                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    result=result_model,
                    duration=duration,
                    tool_call_id=tool_call_id,
                )

                stats.tool_calls_succeeded += 1

            except asyncio.CancelledError:
                cancel = str(
                    get_user_cancellation_message(CancellationReason.TOOL_INTERRUPTED)
                )
                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    error=cancel,
                    tool_call_id=tool_call_id,
                )
                messages.append(
                    LLMMessage.model_validate(
                        self.format_handler.create_tool_response_message(
                            tool_call, cancel
                        )
                    )
                )
                # Important: Save on interrupt/crash
                await interaction_logger.save_interaction(
                    messages, stats, self.config, self.tool_manager
                )
                raise

            except KeyboardInterrupt:
                cancel = str(
                    get_user_cancellation_message(CancellationReason.TOOL_INTERRUPTED)
                )
                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    error=cancel,
                    tool_call_id=tool_call_id,
                )
                messages.append(
                    LLMMessage.model_validate(
                        self.format_handler.create_tool_response_message(
                            tool_call, cancel
                        )
                    )
                )
                await interaction_logger.save_interaction(
                    messages, stats, self.config, self.tool_manager
                )
                raise

            except Exception as exc:
                error_msg = f"Tool '{tool_call.tool_name}' failed: {exc}"
                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    error=error_msg,
                    tool_call_id=tool_call_id,
                )
                messages.append(
                    LLMMessage.model_validate(
                        self.format_handler.create_tool_response_message(
                            tool_call, error_msg
                        )
                    )
                )
                stats.tool_calls_failed += 1

    async def _should_execute_tool(
        self, tool: Any, args: dict[str, Any], tool_call_id: str
    ) -> ToolDecision:
        """Check if a tool should be executed based on modes, permissions, and user approval."""
        # 1. Mode Blocking
        if self.mode_manager is not None:
            blocked, reason = self.mode_manager.should_block_tool(tool.get_name(), args)
            if blocked:
                mode_name = self.mode_manager.current_mode.value.upper()
                educational_feedback = f"""⛔ BLOCKED: Tool '{tool.get_name()}' is a WRITE operation.

Current mode: 📋 {mode_name} (READ-ONLY)

🚫 CRITICAL INSTRUCTION:
You are FORBIDDEN from executing write operations in this mode.
This tool call has been BLOCKED and will NOT be executed.

❌ DO NOT:
- Retry this tool or similar write tools (write_file, search_replace, bash with >, rm, mv, etc.)
- Attempt workarounds or alternative write methods
- Keep trying the same operation

✅ INSTEAD, YOU MUST:
1. Use ONLY read-only tools: read_file, grep, bash (ls/cat/find/git status)
2. Analyze the codebase thoroughly
3. Create a detailed PLAN describing what changes you WOULD make
4. Tell the user: "I've created a plan. Switch to NORMAL or AUTO mode to execute."

⚠️ This is your instruction. Acknowledge and adapt your strategy NOW.
Original block reason: {reason}"""
                return ToolDecision(
                    verdict=ToolExecutionResponse.SKIP, feedback=educational_feedback
                )

        # 2. Auto-Approval
        if self.auto_approve:
            return ToolDecision(verdict=ToolExecutionResponse.EXECUTE)

        # 3. Allowlist/Denylist
        args_model, _ = tool._get_args_and_result_models()
        validated_args = args_model.model_validate(args)

        allowlist_denylist_result = tool.check_allowlist_denylist(validated_args)
        if allowlist_denylist_result == ToolPermission.ALWAYS:
            return ToolDecision(verdict=ToolExecutionResponse.EXECUTE)
        elif allowlist_denylist_result == ToolPermission.NEVER:
            denylist_patterns = tool.config.denylist
            denylist_str = ", ".join(repr(pattern) for pattern in denylist_patterns)
            return ToolDecision(
                verdict=ToolExecutionResponse.SKIP,
                feedback=f"Tool '{tool.get_name()}' blocked by denylist: [{denylist_str}]",
            )

        # 4. Tool-Specific Permissions
        tool_name = tool.get_name()
        perm = self.tool_manager.get_tool_config(tool_name).permission

        if perm is ToolPermission.ALWAYS:
            return ToolDecision(verdict=ToolExecutionResponse.EXECUTE)
        if perm is ToolPermission.NEVER:
            return ToolDecision(
                verdict=ToolExecutionResponse.SKIP,
                feedback=f"Tool '{tool_name}' is permanently disabled",
            )

        # 5. User Approval (Interactive)
        return await self._ask_approval(tool_name, args, tool_call_id)

    async def _ask_approval(
        self, tool_name: str, args: dict[str, Any], tool_call_id: str
    ) -> ToolDecision:
        if not self.approval_callback:
            return ToolDecision(
                verdict=ToolExecutionResponse.SKIP,
                feedback="Tool execution not permitted (no approval callback).",
            )

        if asyncio.iscoroutinefunction(self.approval_callback):
            response, feedback = await self.approval_callback(
                tool_name, args, tool_call_id
            )
        else:
            sync_callback = cast(SyncApprovalCallback, self.approval_callback)
            response, feedback = sync_callback(tool_name, args, tool_call_id)

        match response:
            case ApprovalResponse.ALWAYS:
                self.auto_approve = True
                return ToolDecision(
                    verdict=ToolExecutionResponse.EXECUTE, feedback=feedback
                )
            case ApprovalResponse.YES:
                return ToolDecision(
                    verdict=ToolExecutionResponse.EXECUTE, feedback=feedback
                )
            case _:
                return ToolDecision(
                    verdict=ToolExecutionResponse.SKIP, feedback=feedback
                )
