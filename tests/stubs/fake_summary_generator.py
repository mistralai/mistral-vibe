from __future__ import annotations

import asyncio
from typing import Any


class FakeSummaryGenerator:
    def __init__(self, summary: str | None = "summary") -> None:
        self.summary = summary
        self.calls: list[dict[str, Any]] = []
        self.gate: asyncio.Event | None = None

    async def summarize(self, **kwargs: Any) -> str | None:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        return self.summary
