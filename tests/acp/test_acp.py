from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import json
import os
from typing import Any

from acp import (
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    ReadTextFileRequest,
    ReadTextFileResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    WriteTextFileRequest,
)
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    SessionNotification,
    TextContentBlock,
)
from pydantic import BaseModel
import pytest

from tests import TESTS_ROOT
from tests.mock.utils import get_mocking_env, mock_llm_chunk
from vibe.acp.utils import ToolOption
from vibe.core.types import FunctionCall, ToolCall

RESPONSE_TIMEOUT = 2.0
MOCK_ENTRYPOINT_PATH = "tests/mock/mock_entrypoint.py"
PLAYGROUND_DIR = TESTS_ROOT / "playground"


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str
    method: str
    params: Any | None = None


class JsonRpcError(BaseModel):
    code: int
    message: str
    data: Any | None = None


class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: Any | None = None
    error: JsonRpcError | None = None


class JsonRpcNotification(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Any | None = None


type JsonRpcMessage = JsonRpcResponse | JsonRpcNotification | JsonRpcRequest


class InitializeJsonRpcRequest(JsonRpcRequest):
    method: str = "initialize"
    params: InitializeRequest | None = None


class InitializeJsonRpcResponse(JsonRpcResponse):
    result: InitializeResponse | None = None


class NewSessionJsonRpcRequest(JsonRpcRequest):
    method: str = "session/new"
    params: NewSessionRequest | None = None


class NewSessionJsonRpcResponse(JsonRpcResponse):
    result: NewSessionResponse | None = None


class PromptJsonRpcRequest(JsonRpcRequest):
    method: str = "session/prompt"
    params: PromptRequest | None = None


class PromptJsonRpcResponse(JsonRpcResponse):
    result: PromptResponse | None = None


class UpdateJsonRpcNotification(JsonRpcNotification):
    method: str = "session/update"
    params: SessionNotification | None = None


class RequestPermissionJsonRpcRequest(JsonRpcRequest):
    method: str = "session/request_permission"
    params: RequestPermissionRequest | None = None


class RequestPermissionJsonRpcResponse(JsonRpcResponse):
    result: RequestPermissionResponse | None = None


class ReadTextFileJsonRpcRequest(JsonRpcRequest):
    method: str = "fs/read_text_file"
    params: ReadTextFileRequest | None = None


class ReadTextFileJsonRpcResponse(JsonRpcResponse):
    result: ReadTextFileResponse | None = None


class WriteTextFileJsonRpcRequest(JsonRpcRequest):
    method: str = "fs/write_text_file"
    params: WriteTextFileRequest | None = None


class WriteTextFileJsonRpcResponse(JsonRpcResponse):
    result: None = None


async def get_acp_agent_process(
    mock: bool = True, mock_env: dict[str, str] | None = None
) -> AsyncGenerator[asyncio.subprocess.Process]:
    current_env = os.environ.copy()
    cmd = ["uv", "run", MOCK_ENTRYPOINT_PATH if mock else "vibe-acp"]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=TESTS_ROOT.parent,
        env={
            **current_env,
            **(mock_env or {}),
            **({"MISTRAL_API_KEY": "mock"} if mock else {}),
        },
    )

    try:
        yield process
    finally:
        # Cleanup
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            except TimeoutError:
                process.kill()
                await process.wait()


async def send_json_rpc(
    process: asyncio.subprocess.Process, message: JsonRpcMessage
) -> None:
    if process.stdin is None:
        raise RuntimeError("Process stdin not available")

    request = message.model_dump_json()
    request_json = request + "\n"
    process.stdin.write(request_json.encode())
    await process.stdin.drain()


async def read_response(
    process: asyncio.subprocess.Process, timeout: float = RESPONSE_TIMEOUT
) -> str | None:
    if process.stdout is None:
        raise RuntimeError("Process stdout not available")

    try:
        # Keep reading lines until we find a valid JSON line
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)

            if not line:
                return None

            line_str = line.decode().strip()
            if not line_str:
                continue

            try:
                json.loads(line_str)
                return line_str
            except json.JSONDecodeError:
                # Not JSON, skip it (it's a log message)
                continue
    except TimeoutError:
        return None


async def read_response_for_id(
    process: asyncio.subprocess.Process,
    expected_id: int | str,
    timeout: float = RESPONSE_TIMEOUT,
) -> str | None:
    loop = asyncio.get_running_loop()
    end_time = loop.time() + timeout

    while (remaining := end_time - loop.time()) > 0:
        response = await read_response(process, timeout=remaining)
        if response is None:
            return None

        response_json = json.loads(response)
        if response_json.get("id") == expected_id:
            return response
        print(
            f"Skipping response with id={response_json.get('id')}, expecting {expected_id}"
        )

    return None


async def read_multiple_responses(
    process: asyncio.subprocess.Process,
    max_count: int = 10,
    timeout_per_response: float = RESPONSE_TIMEOUT,
) -> list[str]:
    responses = []
    for _ in range(max_count):
        response = await read_response(process, timeout=timeout_per_response)
        if response:
            responses.append(response)
        else:
            break
    return responses


def parse_conversation(message_texts: list[str]) -> list[JsonRpcMessage]:
    parsed_messages: list[JsonRpcMessage] = []
    for message_text in message_texts:
        message_json = json.loads(message_text)
        cls = None
        has_method = message_json.get("method", None) is not None
        has_id = message_json.get("id", None) is not None
        has_result = message_json.get("result", None) is not None

        is_request = has_method and has_id
        is_notification = has_method and not has_id
        is_response = has_result

        if is_request:
            match message_json.get("method"):
                case "session/prompt":
                    cls = PromptJsonRpcRequest
                case "session/request_permission":
                    cls = RequestPermissionJsonRpcRequest
                case "fs/read_text_file":
                    cls = ReadTextFileJsonRpcRequest
                case "fs/write_text_file":
                    cls = WriteTextFileJsonRpcRequest
        elif is_notification:
            match message_json.get("method"):
                case "session/update":
                    cls = UpdateJsonRpcNotification
        elif is_response:
            # For responses, since we don't know the method, we need to find
            # the matching request.
            matching_request = next(
                (
                    m
                    for m in parsed_messages
                    if isinstance(m, JsonRpcRequest) and m.id == message_json.get("id")
                ),
                None,
            )
            if matching_request is None:
                # No matching request found in the conversation, it most probably was
                # not included in the conversation. We use a generic response class.
                cls = JsonRpcResponse
            else:
                match matching_request.method:
                    case "session/prompt":
                        cls = PromptJsonRpcResponse
                    case "session/request_permission":
                        cls = RequestPermissionJsonRpcResponse
                    case "fs/read_text_file":
                        cls = ReadTextFileJsonRpcResponse
                    case "fs/write_text_file":
                        cls = WriteTextFileJsonRpcResponse
        if cls is None:
            raise ValueError(f"No valid message class found for {message_json}")
        parsed_messages.append(cls.model_validate(message_json))
    return parsed_messages


async def initialize_session(acp_agent_process: asyncio.subprocess.Process) -> str:
    await send_json_rpc(
        acp_agent_process,
        InitializeJsonRpcRequest(id=1, params=InitializeRequest(protocolVersion=1)),
    )
    initialize_response = await read_response_for_id(
        acp_agent_process, expected_id=1, timeout=5.0
    )
    assert initialize_response is not None

    await send_json_rpc(
        acp_agent_process,
        NewSessionJsonRpcRequest(
            id=2, params=NewSessionRequest(cwd=str(PLAYGROUND_DIR), mcpServers=[])
        ),
    )
    session_response = await read_response_for_id(acp_agent_process, expected_id=2)
    assert session_response is not None
    session_response_json = json.loads(session_response)
    session_response_obj = NewSessionJsonRpcResponse.model_validate(
        session_response_json
    )
    assert session_response_obj.result is not None, "No result in response"
    return session_response_obj.result.sessionId


class TestSessionManagement:
    @pytest.mark.asyncio
    async def test_multiple_sessions_unique_ids(self) -> None:
        mock_env = get_mocking_env(mock_chunks=[mock_llm_chunk() for _ in range(3)])
        async for process in get_acp_agent_process(mock_env=mock_env):
            await send_json_rpc(
                process,
                InitializeJsonRpcRequest(
                    id=1, params=InitializeRequest(protocolVersion=1)
                ),
            )
            await read_response_for_id(process, expected_id=1, timeout=5.0)

            session_ids = []
            for i in range(3):
                await send_json_rpc(
                    process,
                    NewSessionJsonRpcRequest(
                        id=i + 2,
                        params=NewSessionRequest(
                            cwd=str(PLAYGROUND_DIR), mcpServers=[]
                        ),
                    ),
                )
                text_response = await read_response_for_id(
                    process, expected_id=i + 2, timeout=RESPONSE_TIMEOUT
                )
                assert text_response is not None
                response_json = json.loads(text_response)
                response = NewSessionJsonRpcResponse.model_validate(response_json)
                assert response.error is None, f"JSON-RPC error: {response.error}"
                assert response.result is not None, "No result in response"
                session_ids.append(response.result.sessionId)

            assert len(set(session_ids)) == 3


class TestSessionUpdates:
    @pytest.mark.asyncio
    async def test_agent_message_chunk_structure(self) -> None:
        mock_env = get_mocking_env([mock_llm_chunk(content="Hi") for _ in range(2)])
        async for process in get_acp_agent_process(mock_env=mock_env):
            # Check stderr for error details if process failed
            if process.returncode is not None and process.stderr:
                stderr_data = await process.stderr.read()
                if stderr_data:
                    # Log stderr for debugging test failures
                    pass  # Could add proper logging here if needed

            session_id = await initialize_session(process)

            await send_json_rpc(
                process,
                PromptJsonRpcRequest(
                    id=3,
                    params=PromptRequest(
                        sessionId=session_id,
                        prompt=[TextContentBlock(type="text", text="Just say hi")],
                    ),
                ),
            )
            text_response = await read_response(process)
            assert text_response is not None
            response = UpdateJsonRpcNotification.model_validate(
                json.loads(text_response)
            )

            assert response.params is not None
            assert response.params.update.sessionUpdate == "agent_message_chunk"
            assert response.params.update.content is not None
            assert response.params.update.content.type == "text"
            assert response.params.update.content.text is not None
            assert response.params.update.content.text == "Hi"

    @pytest.mark.asyncio
    async def test_tool_call_update_structure(self) -> None:
        mock_env = get_mocking_env([
            mock_llm_chunk(content="Hey"),
            mock_llm_chunk(
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="grep", arguments='{"pattern": "auth"}'
                        ),
                        type="function",
                        index=0,
                    )
                ],
                name="bash",
                finish_reason="tool_calls",
            ),
            mock_llm_chunk(
                content="The files containing the pattern 'auth' are ...",
                finish_reason="stop",
            ),
        ])
        async for process in get_acp_agent_process(mock_env=mock_env):
            session_id = await initialize_session(process)

            await send_json_rpc(
                process,
                PromptJsonRpcRequest(
                    id=3,
                    params=PromptRequest(
                        sessionId=session_id,
                        prompt=[
                            TextContentBlock(
                                type="text",
                                text="Show me files that are related to auth",
                            )
                        ],
                    ),
                ),
            )
            text_responses = await read_multiple_responses(process, max_count=10)
            assert len(text_responses) > 0
            responses = [
                UpdateJsonRpcNotification.model_validate(json.loads(r))
                for r in text_responses
            ]

            tool_call = next(
                (
                    r
                    for r in responses
                    if isinstance(r, UpdateJsonRpcNotification)
                    and r.params is not None
                    and r.params.update.sessionUpdate == "tool_call"
                ),
                None,
            )
            assert tool_call is not None
            assert tool_call.params is not None
            assert tool_call.params.update is not None

            assert tool_call.params.update.sessionUpdate == "tool_call"
            assert tool_call.params.update.kind == "search"
            assert tool_call.params.update.title == "grep: 'auth'"
            assert (
                tool_call.params.update.rawInput
                == '{"pattern":"auth","path":".","max_matches":null,"use_default_ignore":true}'
            )


async def start_session_with_request_permission(
    process: asyncio.subprocess.Process, prompt: str
) -> RequestPermissionJsonRpcRequest:
    session_id = await initialize_session(process)
    await send_json_rpc(
        process,
        PromptJsonRpcRequest(
            id=3,
            params=PromptRequest(
                sessionId=session_id,
                prompt=[TextContentBlock(type="text", text=prompt)],
            ),
        ),
    )
    text_responses = await read_multiple_responses(
        process, max_count=15, timeout_per_response=2.0
    )

    responses = parse_conversation(text_responses)
    last_response = responses[-1]

    assert isinstance(last_response, RequestPermissionJsonRpcRequest)
    assert last_response.params is not None
    assert len(last_response.params.options) == 3
    return last_response


@pytest.mark.skip(
    reason="Disabled until we have a way to properly mock the fs and acp interactions"
)
class TestToolCallStructure:
    @pytest.mark.asyncio
    async def test_tool_call_request_permission_structure(self) -> None:
        custom_results = [
            mock_llm_chunk(content="Hey"),
            mock_llm_chunk(
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="write_file",
                            arguments='{"path":"test.txt","content":"hello, world!"'
                            ',"overwrite":true}',
                        ),
                        type="function",
                        index=0,
                    )
                ],
                name="write_file",
                finish_reason="stop",
            ),
        ]
        mock_env = get_mocking_env(custom_results)
        async for process in get_acp_agent_process(mock_env=mock_env):
            session_id = await initialize_session(process)
            await send_json_rpc(
                process,
                PromptJsonRpcRequest(
                    id=3,
                    params=PromptRequest(
                        sessionId=session_id,
                        prompt=[
                            TextContentBlock(
                                type="text",
                                text="Create a new file named test.txt "
                                "with content 'hello, world!'",
                            )
                        ],
                    ),
                ),
            )
            text_responses = await read_multiple_responses(process, max_count=3)
            responses = parse_conversation(text_responses)

            # Look for tool call request permission updates
            permission_requests = [
                r for r in responses if isinstance(r, RequestPermissionJsonRpcRequest)
            ]

            assert len(permission_requests) > 0, (
                "No tool call permission requests found"
            )

            first_request = permission_requests[0]
            assert first_request.params is not None
            assert first_request.params.toolCall is not None
            assert first_request.params.toolCall.toolCallId is not None

    @pytest.mark.asyncio
    async def test_tool_call_update_approved_structure(self) -> None:
        custom_results = [
            mock_llm_chunk(content="Hey"),
            mock_llm_chunk(
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="write_file",
                            arguments='{"path":"test.txt","content":"hello, world!"'
                            ',"overwrite":true}',
                        ),
                        type="function",
                        index=0,
                    )
                ],
                name="write_file",
                finish_reason="tool_calls",
            ),
            mock_llm_chunk(
                content="The file test.txt has been created", finish_reason="stop"
            ),
        ]
        mock_env = get_mocking_env(custom_results)
        async for process in get_acp_agent_process(mock_env=mock_env):
            permission_request = await start_session_with_request_permission(
                process, "Create a file named test.txt"
            )
            assert permission_request.params is not None
            selected_option_id = ToolOption.ALLOW_ONCE
            await send_json_rpc(
                process,
                RequestPermissionJsonRpcResponse(
                    id=permission_request.id,
                    result=RequestPermissionResponse(
                        outcome=AllowedOutcome(
                            outcome="selected", optionId=selected_option_id
                        )
                    ),
                ),
            )
            text_responses = await read_multiple_responses(process, max_count=7)
            responses = parse_conversation(text_responses)

            approved_tool_call = next(
                (
                    r
                    for r in responses
                    if isinstance(r, UpdateJsonRpcNotification)
                    and r.method == "session/update"
                    and r.params is not None
                    and r.params.update is not None
                    and r.params.update.sessionUpdate == "tool_call_update"
                    and r.params.update.toolCallId
                    == (permission_request.params.toolCall.toolCallId)
                    and r.params.update.status == "completed"
                ),
                None,
            )
            assert approved_tool_call is not None

    @pytest.mark.asyncio
    async def test_tool_call_update_rejected_structure(self) -> None:
        custom_results = [
            mock_llm_chunk(content="Hey"),
            mock_llm_chunk(
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="write_file",
                            arguments='{"path":"test.txt","content":"hello, world!"'
                            ',"overwrite":false}',
                        ),
                        type="function",
                        index=0,
                    )
                ],
                name="write_file",
                finish_reason="tool_calls",
            ),
            mock_llm_chunk(
                content="The file test.txt has not been created, "
                "because you rejected the permission request",
                finish_reason="stop",
            ),
        ]
        mock_env = get_mocking_env(custom_results)
        async for process in get_acp_agent_process(mock_env=mock_env):
            permission_request = await start_session_with_request_permission(
                process, "Create a file named test.txt"
            )
            assert permission_request.params is not None
            selected_option_id = ToolOption.REJECT_ONCE

            await send_json_rpc(
                process,
                RequestPermissionJsonRpcResponse(
                    id=permission_request.id,
                    result=RequestPermissionResponse(
                        outcome=AllowedOutcome(
                            outcome="selected", optionId=selected_option_id
                        )
                    ),
                ),
            )
            text_responses = await read_multiple_responses(process, max_count=5)
            responses = parse_conversation(text_responses)

            rejected_tool_call = next(
                (
                    r
                    for r in responses
                    if isinstance(r, UpdateJsonRpcNotification)
                    and r.method == "session/update"
                    and r.params is not None
                    and r.params.update.sessionUpdate == "tool_call_update"
                    and r.params.update.toolCallId
                    == (permission_request.params.toolCall.toolCallId)
                    and r.params.update.status == "failed"
                ),
                None,
            )
            assert rejected_tool_call is not None

    @pytest.mark.skip(reason="Long running tool call updates are not implemented yet")
    @pytest.mark.asyncio
    async def test_tool_call_in_progress_update_structure(self) -> None:
        custom_results = [
            mock_llm_chunk(content="Hey"),
            mock_llm_chunk(
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="bash",
                            arguments='{"command":"sleep 3","timeout":null}',
                        ),
                        type="function",
                    )
                ],
                name="bash",
                finish_reason="tool_calls",
            ),
            mock_llm_chunk(
                content="The command sleep 3 has been run", finish_reason="stop"
            ),
        ]
        mock_env = get_mocking_env(custom_results)
        async for process in get_acp_agent_process(mock_env=mock_env):
            session_id = await initialize_session(process)
            await send_json_rpc(
                process,
                PromptJsonRpcRequest(
                    id=3,
                    params=PromptRequest(
                        sessionId=session_id,
                        prompt=[
                            TextContentBlock(
                                type="text", text="Run sleep 3 in the current directory"
                            )
                        ],
                    ),
                ),
            )
            text_responses = await read_multiple_responses(process, max_count=4)
            responses = parse_conversation(text_responses)

            # Look for tool call in progress updates
            in_progress_calls = [
                r
                for r in responses
                if isinstance(r, UpdateJsonRpcNotification)
                and r.params is not None
                and r.params.update.sessionUpdate == "tool_call_update"
                and r.params.update.status == "in_progress"
            ]

            assert len(in_progress_calls) > 0, (
                "No tool call in progress updates found for a long running command"
            )

    @pytest.mark.asyncio
    async def test_tool_call_result_update_failure_structure(self) -> None:
        custom_results = [
            mock_llm_chunk(content="Hey"),
            mock_llm_chunk(
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="write_file",
                            arguments='{"path":"/test.txt","content":"hello, world!"'
                            ',"overwrite":true}',
                        ),
                        type="function",
                        index=0,
                    )
                ],
                name="write_file",
                finish_reason="tool_calls",
            ),
            mock_llm_chunk(
                content="The file /test.txt has not been created "
                "because it's outside the project directory",
                finish_reason="stop",
            ),
        ]
        mock_env = get_mocking_env(custom_results)
        async for process in get_acp_agent_process(mock_env=mock_env):
            permission_request = await start_session_with_request_permission(
                process, "Create a file named /test.txt"
            )
            assert permission_request.params is not None
            selected_option_id = ToolOption.ALLOW_ONCE
            await send_json_rpc(
                process,
                RequestPermissionJsonRpcResponse(
                    id=permission_request.id,
                    result=RequestPermissionResponse(
                        outcome=AllowedOutcome(
                            outcome="selected", optionId=selected_option_id
                        )
                    ),
                ),
            )
            text_responses = await read_multiple_responses(process, max_count=7)
            responses = parse_conversation(text_responses)

            # Look for tool call result failure updates
            failure_result = next(
                (
                    r
                    for r in responses
                    if isinstance(r, UpdateJsonRpcNotification)
                    and r.params is not None
                    and r.params.update.sessionUpdate == "tool_call_update"
                    and r.params.update.status == "failed"
                    and r.params.update.rawOutput is not None
                    and r.params.update.toolCallId is not None
                ),
                None,
            )

            assert failure_result is not None


class TestCancellationStructure:
    @pytest.mark.skip(
        reason="Proper cancellation is not implemented yet, we still need to return "
        "the right end_turn and be able to cancel at any point in time "
        "(and not only at tool call time)"
    )
    @pytest.mark.asyncio
    async def test_tool_call_update_cancelled_structure(self) -> None:
        custom_results = [
            mock_llm_chunk(content="Hey"),
            mock_llm_chunk(
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="write_file",
                            arguments='{"path":"test.txt","content":"hello, world!"'
                            ',"overwrite":false}',
                        ),
                        type="function",
                        index=0,
                    )
                ],
                name="write_file",
                finish_reason="tool_calls",
            ),
            mock_llm_chunk(
                content="The file test.txt has not been created, "
                "because you cancelled the permission request",
                finish_reason="stop",
            ),
        ]
        mock_env = get_mocking_env(custom_results)
        async for process in get_acp_agent_process(mock_env=mock_env):
            permission_request = await start_session_with_request_permission(
                process, "Create a file named test.txt"
            )
            assert permission_request.params is not None

            await send_json_rpc(
                process,
                RequestPermissionJsonRpcResponse(
                    id=permission_request.id,
                    result=RequestPermissionResponse(
                        outcome=DeniedOutcome(outcome="cancelled")
                    ),
                ),
            )
            text_responses = await read_multiple_responses(process, max_count=5)
            responses = parse_conversation(text_responses)

            assert len(responses) == 2, (
                "There should be only 2 responses: "
                "the tool call update and the prompt end turn"
            )

            cancelled_tool_call = next(
                (
                    r
                    for r in responses
                    if isinstance(r, UpdateJsonRpcNotification)
                    and r.method == "session/update"
                    and r.params is not None
                    and r.params.update.sessionUpdate == "tool_call_update"
                    and r.params.update.toolCallId
                    == (permission_request.params.toolCall.toolCallId)
                    and r.params.update.status == "failed"
                ),
                None,
            )
            assert cancelled_tool_call is not None

            cancelled_prompt_response = next(
                (
                    r
                    for r in responses
                    if isinstance(r, PromptJsonRpcResponse)
                    and r.result is not None
                    and r.result.stopReason == "cancelled"
                ),
                None,
            )
            assert cancelled_prompt_response is not None
