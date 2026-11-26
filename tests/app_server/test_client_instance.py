from __future__ import annotations

import pytest

from vibe.app_server._client_instance import is_dev_desktop_copy


@pytest.mark.parametrize(
    ("instance", "copy"),
    [
        ("/appData/le-chat-desktop-dev-port-5182", True),
        ("/appData/le-chat-desktop-dev-port-5182/", True),
        ("C:\\Users\\me\\AppData\\Roaming\\le-chat-desktop-dev-port-5182", True),
        ("/appData/le-chat-desktop-dev", False),
        ("/appData/le-chat-desktop", False),
        ("/appData/le-chat-desktop-dev-subagents-menu-scroll", False),
        ("/tmp/lecd-e2e-profile", False),
    ],
)
def test_only_a_dev_desktops_per_port_copy_is_one(instance: str, copy: bool) -> None:
    """Desktop deletes it on quit (dev-instance-profile.ts): nothing that lasts
    should be keyed on it.
    """
    assert is_dev_desktop_copy(instance) is copy
