from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    pass_count: int

    @property
    def passed(self) -> bool:
        return self.total > 0 and self.pass_count == self.total


async def run_verification(
    command: str, repeat: int, cwd: str | None = None
) -> VerificationResult:
    passes = 0
    for _ in range(max(repeat, 1)):
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        code = await proc.wait()
        if code == 0:
            passes += 1
    return VerificationResult(total=max(repeat, 1), pass_count=passes)
