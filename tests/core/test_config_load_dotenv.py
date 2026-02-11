from __future__ import annotations

import os
from pathlib import Path
import threading

from vibe.core.config import load_dotenv_values


def _write_env_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_skips_missing_file(tmp_path: Path) -> None:
    environ = {"EXISTING": "1"}
    missing_path = tmp_path / "missing.env"

    load_dotenv_values(env_path=missing_path, environ=environ)

    assert environ == {"EXISTING": "1"}


def test_sets_and_overrides_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(
        env_path,
        "\n".join([
            "MISTRAL_API_KEY=new-key",
            "HTTPS_PROXY=https://local-proxy:8080",
            "OTHER=from-env",
            "NEW_KEY=added",
            "FOO=replace",
        ])
        + "\n",
    )
    environ = {
        "MISTRAL_API_KEY": "old-key",
        "HTTPS_PROXY": "old-https",
        "OTHER": "keep",
        "FOO": "keep",
    }

    load_dotenv_values(env_path=env_path, environ=environ)

    assert environ["MISTRAL_API_KEY"] == "new-key"
    assert environ["HTTPS_PROXY"] == "https://local-proxy:8080"
    assert environ["OTHER"] == "from-env"
    assert environ["NEW_KEY"] == "added"
    assert environ["FOO"] == "replace"


def test_ignores_empty_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(
        env_path, "\n".join(["EMPTY=", "MISTRAL_API_KEY=", "NO_VALUE"]) + "\n"
    )
    environ: dict[str, str] = {}

    load_dotenv_values(env_path=env_path, environ=environ)

    assert "EMPTY" not in environ
    assert "MISTRAL_API_KEY" not in environ
    assert "NO_VALUE" not in environ


def test_reads_from_fifo(tmp_path: Path) -> None:
    fifo_path = tmp_path / ".env.fifo"
    os.mkfifo(fifo_path)
    environ: dict[str, str] = {}

    def write_to_fifo() -> None:
        with open(fifo_path, "w") as f:
            f.write("FIFO_KEY=fifo-value\n")

    writer = threading.Thread(target=write_to_fifo)
    writer.start()

    load_dotenv_values(env_path=fifo_path, environ=environ)

    writer.join()
    assert environ["FIFO_KEY"] == "fifo-value"
