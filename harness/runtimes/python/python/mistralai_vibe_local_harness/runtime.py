import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Annotated, Literal

from pydantic import Field, JsonValue

from mistralai_vibe_local_harness._native import HarnessSession
from mistralai_vibe_local_harness.protocol import (
    RUNTIME_BUILTIN_TOOL_NAMES,
    RustAcceptCandidate,
    RustAcceptedApplyResult,
    RustAction,
    RustActionsNextAction,
    RustCompletedTurn,
    RustCompletionFailedEvent,
    RustCompletionFinishReason,
    RustCompletionHookAccept,
    RustCompletionModelInputResyncRequestedEvent,
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustContentBlock,
    RustDeterminismContext,
    RustDispatchActionDirective,
    RustEvent,
    RustFailedTurn,
    RustFilesystemAction,
    RustFilesystemFailedEvent,
    RustFilesystemSucceededEvent,
    RustFilesystemWriteResult,
    RustHarnessConfig,
    RustHarnessHookBinding,
    RustHarnessInput,
    RustHarnessNotification,
    RustHookCallAction,
    RustHookCompletedEvent,
    RustHookContinue,
    RustHookFailedEvent,
    RustHookResult,
    RustInterruptedTurn,
    RustInterruptEvent,
    RustLLMCallAction,
    RustMessage,
    RustModelMessageAppend,
    RustModelMessageReplace,
    RustModelToolCatalogKeep,
    RustModelToolCatalogReplace,
    RustNotificationEvent,
    RustPostAgentTurnHookAction,
    RustPostAgentTurnHookResult,
    RustPostLlmCallHookAction,
    RustPostLlmCallHookResult,
    RustPostToolCallHookAction,
    RustPostToolCallHookInput,
    RustPostToolCallHookResult,
    RustPostToolCallOutput,
    RustPreAgentTurnContinue,
    RustPreAgentTurnHookAction,
    RustPreAgentTurnHookInput,
    RustPreAgentTurnHookResult,
    RustPreLlmCallHookAction,
    RustPreLlmCallHookResult,
    RustPreToolCallContinue,
    RustPreToolCallHookAction,
    RustPreToolCallHookInput,
    RustPreToolCallHookResult,
    RustProtocolError,
    RustProtocolModel,
    RustProvidedToolCallAction,
    RustReasoningContent,
    RustReasoningPart,
    RustReasoningSummaryContent,
    RustReasoningTextContent,
    RustRefreshActionDirective,
    RustRejectedApplyResult,
    RustRuntimeBuiltinToolCallAction,
    RustRuntimeBuiltinToolName,
    RustSessionInspection,
    RustSessionTransition,
    RustTextContentBlock,
    RustTokenUsage,
    RustToolCallAction,
    RustToolDefinition,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
    RustUserMessageEvent,
    parse_apply_result,
)


class ModelRequest(RustProtocolModel):
    action_id: str
    turn_id: str | None
    purpose: Literal["agent", "compaction"]
    iteration: int = Field(ge=0)
    max_iterations: Annotated[int, Field(gt=0)] | None
    messages: list[RustMessage]
    tools: list[RustToolDefinition]


class ModelCompletionFinished(RustProtocolModel):
    type: Literal["finished"] = "finished"
    finish_reason: RustCompletionFinishReason
    usage: RustTokenUsage | None = None


class ModelAssistantContentDelta(RustProtocolModel):
    type: Literal["assistant_content"] = "assistant_content"
    content: list[RustContentBlock] = Field(min_length=1)


class ModelReasoningTextDelta(RustProtocolModel):
    type: Literal["reasoning_text"] = "reasoning_text"
    text: str = Field(min_length=1)


class ModelReasoningSummaryDelta(RustProtocolModel):
    type: Literal["reasoning_summary"] = "reasoning_summary"
    summary: str = Field(min_length=1)


class ModelToolCallStartedDelta(RustProtocolModel):
    type: Literal["tool_call_started"] = "tool_call_started"
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    meta: dict[str, JsonValue] | None = Field(default=None, alias="_meta")


class ModelToolCallArgumentsDelta(RustProtocolModel):
    type: Literal["tool_call_arguments_appended"] = "tool_call_arguments_appended"
    call_id: str = Field(min_length=1)
    json_fragment: str = Field(alias="json", min_length=1)


type ModelCompletionDelta = (
    ModelAssistantContentDelta
    | ModelReasoningTextDelta
    | ModelReasoningSummaryDelta
    | RustReasoningPart
    | ModelToolCallStartedDelta
    | ModelToolCallArgumentsDelta
)

type ModelStreamItem = ModelCompletionDelta | ModelCompletionFinished


class ToolExecutionResult(RustProtocolModel):
    output: JsonValue = None
    content: list[RustContentBlock] = Field(default_factory=list)
    error: str | None = None


type ModelHandler = Callable[[ModelRequest], AsyncIterator[ModelStreamItem]]
type ToolHandler = Callable[
    [RustToolCallAction], ToolExecutionResult | Awaitable[ToolExecutionResult]
]
type RuntimeBuiltinToolHandlers = Mapping[RustRuntimeBuiltinToolName, ToolHandler]
type ProvidedToolHandler = Callable[
    [RustProvidedToolCallAction], ToolExecutionResult | Awaitable[ToolExecutionResult]
]
type FilesystemHandler = Callable[[RustFilesystemAction], str | Awaitable[str]]
type HookHandler = Callable[[RustHookCallAction], RustHookResult | Awaitable[RustHookResult]]
type CompletionObserver = Callable[
    [str, Literal["agent", "compaction"], ModelCompletionDelta],
    None | Awaitable[None],
]


class HarnessInterrupted(RuntimeError):  # noqa: N818
    pass


class HarnessCompletionFailed(RuntimeError):  # noqa: N818
    def __init__(self, error: RustProtocolError) -> None:
        super().__init__(error.message)
        self.error = error


class HarnessCommandRejected(RuntimeError):  # noqa: N818
    def __init__(self, result: RustRejectedApplyResult) -> None:
        super().__init__(f"harness command rejected: {result.rejection.code}")
        self.rejection = result.rejection


class _ModelInputRevisionMismatch(RuntimeError):  # noqa: N818
    pass


class InMemoryHarnessRuntime:
    def __init__(
        self,
        config: RustHarnessConfig,
        *,
        initial_history: list[RustMessage],
        model_handler: ModelHandler,
        runtime_builtin_handlers: RuntimeBuiltinToolHandlers,
        provided_tool_handler: ProvidedToolHandler,
        filesystem_handler: FilesystemHandler | None = None,
        hook_handlers: Mapping[str, HookHandler] | None = None,
        completion_observer: CompletionObserver | None = None,
    ) -> None:
        self._session = HarnessSession.create(
            config.model_dump_json(exclude_none=True),
            json.dumps(
                [
                    message.model_dump(by_alias=True, exclude_none=True)
                    for message in initial_history
                ]
            ),
        )
        self._model_handler = model_handler
        expected_builtins = set(RUNTIME_BUILTIN_TOOL_NAMES)
        missing = expected_builtins - runtime_builtin_handlers.keys()
        if missing:
            raise ValueError(
                "Harness Runtime does not implement built-ins: " + ", ".join(sorted(missing))
            )
        unexpected = runtime_builtin_handlers.keys() - expected_builtins
        if unexpected:
            raise ValueError(
                "Harness Runtime registered unknown built-ins: " + ", ".join(sorted(unexpected))
            )
        self._runtime_builtin_handlers = dict(runtime_builtin_handlers)
        self._provided_tool_handler = provided_tool_handler
        self._filesystem_handler = filesystem_handler
        self._hook_handlers = dict(hook_handlers or {})
        self._completion_observer = completion_observer
        hook_bindings = [
            binding
            for capabilities in [
                config.capabilities,
                *(plugin.capabilities for plugin in config.plugins),
            ]
            for binding in capabilities.hook_bindings
        ]
        if set(self._hook_handlers) != {binding.id for binding in hook_bindings}:
            raise ValueError("hook handler IDs must exactly match configured hook binding IDs")
        self._hook_bindings: dict[str, RustHarnessHookBinding] = {
            binding.id: binding for binding in hook_bindings
        }
        self._buffer_completion_output = any(
            binding.point in {"post_llm_call", "post_agent_turn"} for binding in hook_bindings
        )
        self._next_input_id = 1
        self._model_context: list[RustMessage] = []
        self._model_context_revision: int | None = None
        self._model_context_update_key: str | None = None
        self._model_tool_catalog: list[RustToolDefinition] = []
        self._model_tool_catalog_revision: int | None = None
        self._model_tool_catalog_update_key: str | None = None

    @property
    def checkpoint(self) -> str:
        return self._session.checkpoint()

    @property
    def inspection(self) -> RustSessionInspection:
        return self._inspection()

    async def run_turn(
        self,
        content: str | list[RustContentBlock],
        *,
        mode: Literal["queue", "steer"] = "queue",
        turn_id: str | None = None,
    ) -> list[RustContentBlock]:
        if turn_id is None:
            turn_id = (
                f"turn-{secrets.token_hex(16)}"
                if mode == "queue"
                else self._required_active_turn_id()
            )
        transition = self._apply(
            RustUserMessageEvent(
                turn_id=turn_id,
                content=(
                    [RustTextContentBlock(text=content)] if isinstance(content, str) else content
                ),
                mode=mode,
            )
        )
        pending: set[asyncio.Task[RustSessionTransition]] = set()

        def schedule(next_transition: RustSessionTransition) -> None:
            if not isinstance(next_transition.next, RustActionsNextAction):
                return
            for directive in next_transition.next.directives:
                if isinstance(directive, RustDispatchActionDirective):
                    pending.add(asyncio.create_task(self._execute(directive.action)))

        schedule(transition)
        while pending:
            completed, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in completed:
                transition = task.result()
                schedule(transition)
        return _terminal_output(transition)

    def interrupt(
        self,
        reason: str | None = None,
        *,
        expected_turn_id: str | None = None,
    ) -> RustSessionTransition:
        return self._apply(
            RustInterruptEvent(
                expected_turn_id=(
                    self._required_active_turn_id()
                    if expected_turn_id is None
                    else expected_turn_id
                ),
                reason=reason,
            )
        )

    def notify(self, notification: RustHarnessNotification) -> RustSessionTransition:
        """Deliver Runtime-owned async information without driving a new action."""
        return self._apply(RustNotificationEvent(notification=notification))

    async def _execute(self, action: RustAction) -> RustSessionTransition:
        if isinstance(action, RustLLMCallAction):
            return await self._execute_completion(action)
        if isinstance(
            action,
            RustPreAgentTurnHookAction
            | RustPreLlmCallHookAction
            | RustPostLlmCallHookAction
            | RustPostAgentTurnHookAction
            | RustPreToolCallHookAction
            | RustPostToolCallHookAction,
        ):
            return await self._execute_hook(action)
        if isinstance(action, RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction):
            try:
                if isinstance(action, RustRuntimeBuiltinToolCallAction):
                    result = await _resolve_tool(
                        self._runtime_builtin_handlers[action.call.name](action)
                    )
                else:
                    result = await _resolve_tool(self._provided_tool_handler(action))
            except Exception as error:  # noqa: BLE001
                return self._apply(
                    RustToolFailedEvent(
                        action_id=action.action_id,
                        call_id=action.call_id,
                        result=RustToolFailureResult(
                            error=RustProtocolError(
                                code="tool_executor_failed",
                                message=str(error),
                                retryable=False,
                                details=None,
                            )
                        ),
                    )
                )
            if result.error is not None:
                failure = (
                    RustToolFailureResult(
                        content=result.content,
                        structured_content=result.output,
                        error=RustProtocolError(
                            code="tool_failed",
                            message=result.error,
                            retryable=False,
                            details=None,
                        ),
                    )
                    if "output" in result.model_fields_set
                    else RustToolFailureResult(
                        content=result.content,
                        error=RustProtocolError(
                            code="tool_failed",
                            message=result.error,
                            retryable=False,
                            details=None,
                        ),
                    )
                )
                return self._apply(
                    RustToolFailedEvent(
                        action_id=action.action_id,
                        call_id=action.call_id,
                        result=failure,
                    )
                )
            success = (
                RustToolSuccessResult(
                    content=result.content,
                    structured_content=result.output,
                )
                if "output" in result.model_fields_set
                else RustToolSuccessResult(content=result.content)
            )
            return self._apply(
                RustToolSucceededEvent(
                    action_id=action.action_id,
                    call_id=action.call_id,
                    result=success,
                )
            )
        if isinstance(action, RustFilesystemAction):
            try:
                handler = self._filesystem_handler
                if handler is None:
                    raise RuntimeError("filesystem support is not configured")
                model_path = handler(action)
                model_path = await model_path if isinstance(model_path, Awaitable) else model_path
                if not isinstance(model_path, str) or not model_path:
                    raise TypeError("filesystem handler must return a non-empty model path")
                return self._apply(
                    RustFilesystemSucceededEvent(
                        action_id=action.action_id,
                        result=RustFilesystemWriteResult(model_path=model_path),
                    )
                )
            except Exception as error:  # noqa: BLE001
                return self._apply(
                    RustFilesystemFailedEvent(
                        action_id=action.action_id,
                        error=RustProtocolError(
                            code="filesystem_write_failed",
                            message=str(error),
                            retryable=False,
                            details=None,
                        ),
                    )
                )
        raise AssertionError(f"unsupported harness action: {action!r}")

    async def _execute_completion(self, action: RustLLMCallAction) -> RustSessionTransition:
        action = self._prepare_completion_action(action)
        request = ModelRequest(
            action_id=action.action_id,
            turn_id=action.turn_id,
            purpose=action.purpose,
            iteration=action.iteration,
            max_iterations=action.max_iterations,
            messages=list(self._model_context),
            tools=list(self._model_tool_catalog),
        )
        assembler = _CompletionResultAssembler()
        result: RustCompletionResult | None = None
        try:
            async for item in self._model_handler(request):
                if result is not None:
                    raise RuntimeError("model stream emitted data after its finished item")
                if isinstance(item, ModelCompletionFinished):
                    result = assembler.finish(
                        finish_reason=item.finish_reason,
                        usage=item.usage,
                    )
                    continue
                assembler.push(item)
                if (
                    action.purpose == "agent"
                    and not self._buffer_completion_output
                    and self._completion_observer is not None
                ):
                    await _resolve_completion_observer(
                        self._completion_observer(
                            action.action_id,
                            action.purpose,
                            item,
                        )
                    )
        except Exception as error:  # noqa: BLE001
            return self._apply(
                RustCompletionFailedEvent(
                    action_id=action.action_id,
                    error=RustProtocolError(
                        code="model_stream_failed",
                        message=str(error),
                        retryable=False,
                        details=None,
                    ),
                )
            )
        if result is None:
            return self._apply(
                RustCompletionFailedEvent(
                    action_id=action.action_id,
                    error=RustProtocolError(
                        code="model_stream_incomplete",
                        message="model stream ended without a finished item",
                        retryable=False,
                        details=None,
                    ),
                )
            )
        return self._apply(
            RustCompletionSucceededEvent(
                action_id=action.action_id,
                result=result,
            )
        )

    def _select_hook_handlers(self, action: RustHookCallAction) -> list[tuple[str, HookHandler]]:
        """Validate the complete selected-ID list before any hook executes.

        Runtime validation is limited to registry membership, uniqueness, point
        compatibility, and order. Only the Core decides which selectors match.
        """
        selected: list[tuple[str, HookHandler]] = []
        seen: set[str] = set()
        previous_order = -1
        for binding_id in action.hook_binding_ids:
            if binding_id in seen:
                raise RuntimeError(f"core selected duplicate hook binding {binding_id!r}")
            seen.add(binding_id)
            binding = self._hook_bindings.get(binding_id)
            handler = self._hook_handlers.get(binding_id)
            if binding is None or handler is None:
                raise RuntimeError(f"core selected unknown hook binding {binding_id!r}")
            if binding.point != action.hook:
                raise RuntimeError(
                    f"hook binding {binding_id!r} belongs to {binding.point!r}, not {action.hook!r}"
                )
            if binding.order <= previous_order:
                raise RuntimeError("core selected hook bindings out of pipeline order")
            previous_order = binding.order
            selected.append((binding_id, handler))
        return selected

    async def _execute_hook(self, action: RustHookCallAction) -> RustSessionTransition:
        try:
            selected = self._select_hook_handlers(action)
            current = action
            result = _identity_hook_result(action)
            for binding_id, handler in selected:
                value = handler(current)
                result = await value if isinstance(value, Awaitable) else value
                if result.hook != action.hook:
                    raise RuntimeError(
                        f"hook binding {binding_id!r} returned {result.hook!r} for {action.hook!r}"
                    )
                current, terminal = _continue_hook_pipeline(current, result)
                if terminal:
                    break
            return self._apply(RustHookCompletedEvent(action_id=action.action_id, result=result))
        except Exception as error:  # noqa: BLE001
            return self._apply(
                RustHookFailedEvent(
                    action_id=action.action_id,
                    error=RustProtocolError(
                        code="hook_pipeline_failed",
                        message=str(error),
                        retryable=False,
                        details=None,
                    ),
                )
            )

    def _apply(self, command: RustEvent) -> RustSessionTransition:
        payload = RustHarnessInput(
            input_id=self._next_input_id,
            determinism=RustDeterminismContext(
                time_unix_ms=time.time_ns() // 1_000_000,
                random_seed=secrets.randbits(32),
            ),
            command=command,
        )
        result = parse_apply_result(
            self._session.apply(payload.model_dump_json(exclude_none=False, by_alias=True))
        )
        if isinstance(result, RustRejectedApplyResult):
            raise HarnessCommandRejected(result)
        if not isinstance(result, RustAcceptedApplyResult):
            raise TypeError(f"unsupported apply result: {result!r}")
        transition = result.transition
        if transition.input_id != self._next_input_id:
            raise RuntimeError("harness returned a transition for the wrong input")
        self._next_input_id += 1
        return transition

    def _prepare_completion_action(self, action: RustLLMCallAction) -> RustLLMCallAction:
        try:
            self._apply_model_input_update(action)
            return action
        except _ModelInputRevisionMismatch:
            transition = self._apply(
                RustCompletionModelInputResyncRequestedEvent(action_id=action.action_id)
            )
            refreshed = (
                next(
                    (
                        directive
                        for directive in transition.next.directives
                        if isinstance(directive, RustRefreshActionDirective)
                        and directive.action.action_id == action.action_id
                    ),
                    None,
                )
                if isinstance(transition.next, RustActionsNextAction)
                else None
            )
            if refreshed is None:
                raise TypeError(
                    "harness model-input resync did not return a refresh action"
                ) from None
            refreshed_action = refreshed.action
            if refreshed_action.action_id != action.action_id:
                raise RuntimeError(
                    "harness context refresh changed the completion action id"
                ) from None
            self._apply_model_input_update(
                refreshed_action,
                allow_replace_same_revision=True,
            )
            return refreshed_action

    def _apply_model_input_update(
        self,
        action: RustLLMCallAction,
        *,
        allow_replace_same_revision: bool = False,
    ) -> None:
        message_update = action.model_input.messages
        message_update_key = message_update.model_dump_json(exclude_none=False)
        if isinstance(message_update, RustModelMessageReplace):
            if (
                self._model_context_revision == message_update.revision
                and self._model_context_update_key != message_update_key
                and not allow_replace_same_revision
            ):
                raise _ModelInputRevisionMismatch(
                    f"message revision {message_update.revision} was reused with a different update"
                )
            next_messages = list(message_update.messages)
            next_message_revision = message_update.revision
        elif isinstance(message_update, RustModelMessageAppend):
            if self._model_context_revision == message_update.revision:
                if self._model_context_update_key != message_update_key:
                    raise _ModelInputRevisionMismatch(
                        f"message revision {message_update.revision} was reused "
                        "with a different update"
                    )
                next_messages = self._model_context
                next_message_revision = self._model_context_revision
            else:
                if self._model_context_revision != message_update.base_revision:
                    raise _ModelInputRevisionMismatch(
                        f"message append expected revision {message_update.base_revision}, "
                        f"got {self._model_context_revision}"
                    )
                next_messages = [*self._model_context, *message_update.messages]
                next_message_revision = message_update.revision
        else:
            raise AssertionError(f"unsupported model message update: {message_update!r}")

        tool_update = action.model_input.tool_catalog
        if isinstance(tool_update, RustModelToolCatalogKeep):
            if self._model_tool_catalog_revision != tool_update.revision:
                raise _ModelInputRevisionMismatch(
                    f"tool catalog keep expected revision {tool_update.revision}, "
                    f"got {self._model_tool_catalog_revision}"
                )
            next_tools = self._model_tool_catalog
            next_tool_revision = self._model_tool_catalog_revision
            next_tool_update_key = self._model_tool_catalog_update_key
        elif isinstance(tool_update, RustModelToolCatalogReplace):
            tool_update_key = tool_update.model_dump_json(exclude_none=False)
            if (
                self._model_tool_catalog_revision == tool_update.revision
                and self._model_tool_catalog_update_key != tool_update_key
                and not allow_replace_same_revision
            ):
                raise _ModelInputRevisionMismatch(
                    f"tool catalog revision {tool_update.revision} was reused "
                    "with a different update"
                )
            next_tools = list(tool_update.tools)
            next_tool_revision = tool_update.revision
            next_tool_update_key = tool_update_key
        else:
            raise AssertionError(f"unsupported model tool-catalog update: {tool_update!r}")

        self._model_context = next_messages
        self._model_context_revision = next_message_revision
        self._model_context_update_key = message_update_key
        self._model_tool_catalog = next_tools
        self._model_tool_catalog_revision = next_tool_revision
        self._model_tool_catalog_update_key = next_tool_update_key

    def _inspection(self) -> RustSessionInspection:
        return RustSessionInspection.model_validate_json(self._session.inspect())

    def _required_active_turn_id(self) -> str:
        turn_id = self._inspection().active_turn_id
        if turn_id is None:
            raise RuntimeError("harness has no active turn")
        return turn_id


class _CompletionResultAssembler:
    def __init__(self) -> None:
        self._parts: list[RustCompletionResultPart] = []
        self._tool_call_indexes: dict[str, int] = {}

    def push(self, delta: ModelCompletionDelta) -> None:
        match delta:
            case ModelAssistantContentDelta(content=content):
                for block in content:
                    self._push_content(block)
            case ModelReasoningTextDelta(text=text):
                self._push_reasoning(RustReasoningTextContent(text=text))
            case ModelReasoningSummaryDelta(summary=summary):
                self._push_reasoning(RustReasoningSummaryContent(text=summary))
            case RustReasoningPart():
                self._parts.append(delta.model_copy(deep=True))
            case ModelToolCallStartedDelta(
                call_id=call_id,
                name=name,
                meta=meta,
            ):
                if call_id in self._tool_call_indexes:
                    raise RuntimeError(f"duplicate model tool call ID {call_id!r}")
                self._tool_call_indexes[call_id] = len(self._parts)
                self._parts.append(
                    RustCompletionResultToolCallPart.model_validate(
                        {
                            "id": call_id,
                            "name": name,
                            "arguments_json": "",
                            **({} if meta is None else {"_meta": meta}),
                        }
                    )
                )
            case ModelToolCallArgumentsDelta(
                call_id=call_id,
                json_fragment=json_fragment,
            ):
                index = self._tool_call_indexes.get(call_id)
                if index is None:
                    raise RuntimeError(
                        f"model tool call arguments arrived before call {call_id!r} started"
                    )
                part = self._parts[index]
                if not isinstance(part, RustCompletionResultToolCallPart):
                    raise TypeError(f"model tool call {call_id!r} is not available")
                self._parts[index] = part.model_copy(
                    update={"arguments_json": part.arguments_json + json_fragment}
                )

    def finish(
        self,
        *,
        finish_reason: RustCompletionFinishReason,
        usage: RustTokenUsage | None,
    ) -> RustCompletionResult:
        return RustCompletionResult(
            parts=self._parts,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _push_content(self, block: RustContentBlock) -> None:
        previous = self._parts[-1] if self._parts else None
        if (
            isinstance(previous, RustTextContentBlock)
            and isinstance(block, RustTextContentBlock)
            and previous.annotations == block.annotations
            and previous.meta == block.meta
        ):
            self._parts[-1] = previous.model_copy(update={"text": previous.text + block.text})
            return
        self._parts.append(block)

    def _push_reasoning(self, content: RustReasoningContent) -> None:
        previous = self._parts[-1] if self._parts else None
        if not isinstance(previous, RustReasoningPart) or previous.meta is not None:
            self._parts.append(RustReasoningPart(content=[content]))
            return
        previous_content = previous.content[-1]
        if isinstance(previous_content, RustReasoningTextContent) and isinstance(
            content, RustReasoningTextContent
        ):
            self._parts[-1] = previous.model_copy(
                update={
                    "content": [
                        *previous.content[:-1],
                        previous_content.model_copy(
                            update={"text": previous_content.text + content.text}
                        ),
                    ]
                }
            )
            return
        self._parts[-1] = previous.model_copy(update={"content": [*previous.content, content]})


async def _resolve_completion_observer(value: None | Awaitable[None]) -> None:
    if isinstance(value, Awaitable):
        await value


def _identity_hook_result(action: RustHookCallAction) -> RustHookResult:
    if isinstance(action, RustPreAgentTurnHookAction):
        return RustPreAgentTurnHookResult(
            output=RustPreAgentTurnContinue(user_content=action.input.user_content)
        )
    if isinstance(action, RustPreLlmCallHookAction):
        return RustPreLlmCallHookResult(output=RustHookContinue())
    if isinstance(action, RustPostLlmCallHookAction):
        return RustPostLlmCallHookResult(
            output=RustCompletionHookAccept(acceptance=RustAcceptCandidate())
        )
    if isinstance(action, RustPostAgentTurnHookAction):
        return RustPostAgentTurnHookResult(
            output=RustCompletionHookAccept(acceptance=RustAcceptCandidate())
        )
    if isinstance(action, RustPreToolCallHookAction):
        return RustPreToolCallHookResult(
            output=RustPreToolCallContinue(
                effective_arguments=action.input.tool_call.call.arguments
            )
        )
    if isinstance(action, RustPostToolCallHookAction):
        return RustPostToolCallHookResult(
            output=RustPostToolCallOutput(tool_result=action.input.tool_result)
        )
    raise AssertionError(f"unsupported hook action: {action!r}")


def _continue_hook_pipeline(
    action: RustHookCallAction,
    result: RustHookResult,
) -> tuple[RustHookCallAction, bool]:
    if isinstance(action, RustPreAgentTurnHookAction) and isinstance(
        result, RustPreAgentTurnHookResult
    ):
        if result.output.type == "skip":
            return action, True
        return (
            action.model_copy(
                update={"input": RustPreAgentTurnHookInput(user_content=result.output.user_content)}
            ),
            False,
        )
    if isinstance(action, RustPreLlmCallHookAction) and isinstance(
        result, RustPreLlmCallHookResult
    ):
        return action, result.output.type == "skip"
    if isinstance(action, RustPostLlmCallHookAction | RustPostAgentTurnHookAction) and isinstance(
        result, RustPostLlmCallHookResult | RustPostAgentTurnHookResult
    ):
        if result.output.type != "accept":
            return action, True
        if result.output.acceptance.type == "candidate":
            return action, False
        candidate = action.input.candidate
        message = candidate.message.model_copy(update={"content": result.output.acceptance.content})
        return (
            action.model_copy(
                update={
                    "input": action.input.model_copy(
                        update={"candidate": candidate.model_copy(update={"message": message})}
                    )
                }
            ),
            False,
        )
    if isinstance(action, RustPreToolCallHookAction) and isinstance(
        result, RustPreToolCallHookResult
    ):
        if result.output.type == "skip":
            return action, True
        # A hook rewrites arguments only; the call keeps its original ownership
        # and built-in or provided identity.
        tool_call = action.input.tool_call
        return (
            action.model_copy(
                update={
                    "input": RustPreToolCallHookInput(
                        tool_call=tool_call.model_copy(
                            update={
                                "call": tool_call.call.model_copy(
                                    update={"arguments": result.output.effective_arguments}
                                )
                            }
                        )
                    )
                }
            ),
            False,
        )
    if isinstance(action, RustPostToolCallHookAction) and isinstance(
        result, RustPostToolCallHookResult
    ):
        return (
            action.model_copy(
                update={
                    "input": RustPostToolCallHookInput(
                        tool_call=action.input.tool_call,
                        tool_result=result.output.tool_result,
                    )
                }
            ),
            False,
        )
    raise RuntimeError(f"hook result {result.hook!r} does not match {action.hook!r}")


async def _resolve_tool(
    value: ToolExecutionResult | Awaitable[ToolExecutionResult],
) -> ToolExecutionResult:
    resolved = await value if isinstance(value, Awaitable) else value
    if not isinstance(resolved, ToolExecutionResult):
        raise TypeError("tool handler must return ToolExecutionResult")
    return resolved


def _terminal_output(transition: RustSessionTransition) -> list[RustContentBlock]:
    if isinstance(transition.turn, RustCompletedTurn):
        return transition.turn.output
    if isinstance(transition.turn, RustInterruptedTurn):
        raise HarnessInterrupted(transition.turn.reason or "Harness turn interrupted")
    if isinstance(transition.turn, RustFailedTurn):
        raise HarnessCompletionFailed(transition.turn.error)
    raise RuntimeError("harness became idle without completing, failing, or interrupting the turn")


__all__ = [
    "CompletionObserver",
    "FilesystemHandler",
    "HarnessCommandRejected",
    "HarnessCompletionFailed",
    "HarnessInterrupted",
    "HookHandler",
    "InMemoryHarnessRuntime",
    "ModelAssistantContentDelta",
    "ModelCompletionFinished",
    "ModelHandler",
    "ModelReasoningSummaryDelta",
    "ModelReasoningTextDelta",
    "ModelRequest",
    "ModelStreamItem",
    "ModelToolCallArgumentsDelta",
    "ModelToolCallStartedDelta",
    "ProvidedToolHandler",
    "RuntimeBuiltinToolHandlers",
    "ToolExecutionResult",
    "ToolHandler",
]
