from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.tools.builtins.experimental_bash import TerminalSessionManager


class TerminalRuntime:
    def __init__(self) -> None:
        self._managers: dict[tuple[str, str], TerminalSessionManager] = {}

    def get(
        self, *, shell_family: str = "posix", session_prefix: str = "bash"
    ) -> TerminalSessionManager:
        key = (shell_family, session_prefix)
        if key not in self._managers:
            from vibe.core.tools.builtins.experimental_bash import (
                TerminalSessionManager,
            )

            self._managers[key] = TerminalSessionManager(
                shell_family=shell_family, session_prefix=session_prefix
            )
        return self._managers[key]

    def close(self) -> None:
        managers = list(self._managers.values())
        self._managers.clear()
        errors: list[BaseException] = []
        for manager in managers:
            try:
                manager.reset(clear_logs=False)
            except BaseException as exc:
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close terminal managers", errors)
