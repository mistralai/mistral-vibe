"""History pages, Turn lists and plugin information on the Session API path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    SessionReadParams as ApiSessionReadParams,
    SessionReadResponse as ApiSessionReadResponse,
    SessionTurnsListResponse as ApiTurnsListResponse,
)
from tests.app_server._session_api_harness import (
    api_adapter,
    api_transport,
    forwarding,
    until,
    use_answering_model,
    use_model_calling_one_tool,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import TurnCompleted
from vibe.app_server.models import PublicEffectEntry, TextContentBlock
from vibe.app_server.protocol import (
    PageRequest,
    PluginInfoParams,
    PluginInfoResponse,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionReadParams,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    TurnStartParams,
)


@pytest.mark.asyncio
async def test_pages_turns_and_plugin_info_are_read_through_the_session_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A session on the Session API path with two Turns, the first
    calling a tool, and an adapter whose direct page reads fail.
    *Do*: Page the history back from a two-entry read, list the Turns, and read
    the plugin information.
    *Assert*: Each read goes through its Session API procedure; the pages hold
    the whole history once, the tool as one effect; both Turns are listed; the
    plugin information is the Runtime's.
    """
    # Prepare
    use_model_calling_one_tool(monkeypatch)
    async with (
        api_adapter(tmp_path, process_services=process_services) as harness,
        forwarding(harness) as forwarded,
    ):
        adapter = harness.adapter
        session_id = adapter.session_id
        for index in range(2):
            await adapter.start_turn(
                TurnStartParams(
                    session_id=session_id,
                    message=[TextContentBlock(text=f"ask {index}")],
                    client_user_message_id=f"ask-{index}",
                )
            )
            await until(
                lambda count=index + 1: (
                    sum(isinstance(e.event, TurnCompleted) for e in forwarded) == count
                )
            )
        await adapter.flush_events()
        full = await adapter.read(
            SessionReadParams(session_id=session_id, history=PageRequest(limit=500))
        )
        tail = await adapter.read(
            SessionReadParams(session_id=session_id, history=PageRequest(limit=2))
        )
        runtime_info = await adapter._session.read_plugin_info()
        transport = api_transport(adapter)
        requested: list[str] = []
        request = transport.request

        async def recording(method: str, *args: Any, **kwargs: Any) -> Any:
            requested.append(method)
            return await request(method, *args, **kwargs)

        async def no_runtime_read(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("read a page of the Runtime's own state")

        monkeypatch.setattr(transport, "request", recording)
        monkeypatch.setattr(adapter, "_read_page_state", no_runtime_read)

        # Do
        paged = [entry.id for entry in tail.state.history or []]
        cursor = tail.state.history_before_cursor
        while cursor is not None:
            page = SessionHistoryListResponse.model_validate(
                (
                    await adapter.dispatch_extension(
                        "session/history/list",
                        SessionHistoryListParams(
                            session_id=session_id,
                            page=PageRequest(cursor=cursor, limit=2),
                        ).model_dump(mode="json", by_alias=True),
                    )
                ).response.model_dump(mode="json", by_alias=True)
            )
            paged = [*(entry.id for entry in page.items), *paged]
            cursor = page.next_cursor
        turns = SessionTurnsListResponse.model_validate(
            (
                await adapter.dispatch_extension(
                    "session/turns/list",
                    SessionTurnsListParams(session_id=session_id).model_dump(
                        mode="json", by_alias=True
                    ),
                )
            ).response.model_dump(mode="json", by_alias=True)
        )
        info = PluginInfoResponse.model_validate(
            (
                await adapter.dispatch_extension(
                    "plugin/info",
                    PluginInfoParams(session_id=session_id).model_dump(
                        mode="json", by_alias=True
                    ),
                )
            ).response.model_dump(mode="json", by_alias=True)
        )

    # Assert
    assert {
        "app_server/session/history/list",
        "app_server/session/turns/list",
        "app_server/session/plugin/info",
    } <= set(requested)
    history = full.state.history or []
    assert tail.state.history_before_cursor is not None
    assert paged == [entry.id for entry in history]
    assert sum(isinstance(entry, PublicEffectEntry) for entry in history) == 1
    assert [turn.input_entry_id for turn in turns.items] == ["ask-0", "ask-1"]
    assert all(turn.status == "completed" for turn in turns.items)
    assert info.info.model_dump(mode="json", by_alias=True) == runtime_info.model_dump(
        mode="json", by_alias=True
    )


@pytest.mark.asyncio
async def test_a_listed_turn_without_a_start_starts_when_the_session_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A session on the Session API path with one Turn, and a
    ``turns/list`` that also lists a Turn with no start or end time that the
    latest history window does not hold.
    *Do*: List the Turns.
    *Assert*: That Turn starts when the Session was created, as the latest Turn
    does, not at the Unix epoch; the real Turn keeps its own start.
    """
    # Prepare
    use_answering_model(monkeypatch)
    async with (
        api_adapter(tmp_path, process_services=process_services) as harness,
        forwarding(harness) as forwarded,
    ):
        adapter = harness.adapter
        session_id = adapter.session_id
        await adapter.start_turn(
            TurnStartParams(
                session_id=session_id,
                message=[TextContentBlock(text="ask")],
                client_user_message_id="ask",
            )
        )
        await until(lambda: any(isinstance(e.event, TurnCompleted) for e in forwarded))
        await adapter.flush_events()
        transport = api_transport(adapter)
        read = await transport.request(
            "app_server/session/read",
            ApiSessionReadParams(session_id=session_id),
            ApiSessionReadResponse,
        )
        request = transport.request

        async def with_unseen_turn(method: str, *args: Any, **kwargs: Any) -> Any:
            response = await request(method, *args, **kwargs)
            if method != "app_server/session/turns/list":
                return response
            assert isinstance(response, ApiTurnsListResponse)
            real = response.page.items[0]
            unseen = real.model_copy(
                update={"id": "turn-unseen", "started_at": None, "completed_at": None}
            )
            page = response.page.model_copy(update={"items": [unseen, real]})
            return response.model_copy(update={"page": page})

        monkeypatch.setattr(transport, "request", with_unseen_turn)

        # Do
        turns = SessionTurnsListResponse.model_validate(
            (
                await adapter.dispatch_extension(
                    "session/turns/list",
                    SessionTurnsListParams(session_id=session_id).model_dump(
                        mode="json", by_alias=True
                    ),
                )
            ).response.model_dump(mode="json", by_alias=True)
        )

    # Assert
    started = {turn.id: turn.started_at for turn in turns.items}
    assert started["turn-unseen"] == read.state.session.created_at
    assert started.pop("turn-unseen") != 0
    assert all(value >= read.state.session.created_at for value in started.values())
