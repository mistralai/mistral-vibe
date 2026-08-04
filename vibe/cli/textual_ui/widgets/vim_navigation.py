from __future__ import annotations

from typing import Protocol, cast

from textual import events


class _VimNavigationHost(Protocol):
    def action_move_up(self) -> None: ...

    def action_move_down(self) -> None: ...


class VimNavigationMixin:
    # For custom container widgets that render their own option list and expose
    # action_move_up/action_move_down. OptionList-based pickers should use
    # NavigableOptionList instead.
    def _handle_vim_navigation_key(self, event: events.Key) -> bool:
        host = cast(_VimNavigationHost, self)

        match event.key:
            case "j":
                host.action_move_down()
            case "k":
                host.action_move_up()
            case _:
                return False

        event.stop()
        event.prevent_default()
        return True
