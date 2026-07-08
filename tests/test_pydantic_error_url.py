from __future__ import annotations

import subprocess
import sys


def test_importing_vibe_strips_pydantic_error_url() -> None:
    script = (
        "import vibe\n"
        "from pydantic import BaseModel\n"
        "class M(BaseModel):\n"
        "    x: int\n"
        "try:\n"
        "    M(x='a')\n"
        "except Exception as e:\n"
        "    print(str(e))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert "errors.pydantic.dev" not in result.stdout


def test_explicit_env_override_is_respected() -> None:
    script = (
        "import os\n"
        "os.environ['PYDANTIC_ERRORS_INCLUDE_URL'] = '1'\n"
        "import vibe\n"
        "from pydantic import BaseModel\n"
        "class M(BaseModel):\n"
        "    x: int\n"
        "try:\n"
        "    M(x='a')\n"
        "except Exception as e:\n"
        "    print(str(e))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert "errors.pydantic.dev" in result.stdout
