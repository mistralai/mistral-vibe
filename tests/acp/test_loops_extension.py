"""ACP-layer loops tests: a `loops/*` method ACP does not support fails loudly instead of acting as another."""

from __future__ import annotations

import pytest

from tests.acp.conftest import _create_acp_agent
from vibe.acp.exceptions import NotImplementedMethodError


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["loops/update", "loops/run", "loops/nope"])
async def test_an_unsupported_loops_method_is_refused_without_clearing(
    method: str,
) -> None:
    """*Prepare*: An ACP agent.
    *Do*: Call a `loops/*` method that ACP does not support.
    *Assert*: It fails as not implemented, before any session or `loops/clear` is reached.
    """
    # Prepare
    agent = _create_acp_agent()

    # Do / Assert
    with pytest.raises(NotImplementedMethodError):
        await agent.ext_method(method, {"sessionId": "s", "loopId": "l"})
