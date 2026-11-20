from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"clientInfo": {"name": "test", "version": "0"}, "capabilities": {}},
}


@pytest.mark.timeout(60)
def test_app_server_serves_when_its_vibe_home_cannot_be_created(tmp_path: Path) -> None:
    """LE-CHAT-DESKTOP-727: a Vibe home whose folders cannot be created
    (here, a file where a folder must be) made the process exit 1 before it
    answered, and Desktop started another one every 100 ms.
    """
    home = tmp_path / "home"
    home.mkdir()
    blocker = tmp_path / "not-a-folder"
    blocker.touch()
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "VIBE_HOME": str(blocker / ".vibe"),
        "VIBE_TEST_DISABLE_KEYRING": "1",
    }

    result = subprocess.run(
        [sys.executable, "-c", "from vibe.app_server.stdio import main; main()"],
        input=json.dumps(_INITIALIZE) + "\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=home,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout.splitlines()[0])
    assert response["result"]["serverInfo"]["name"] == "vibe-app-server"
