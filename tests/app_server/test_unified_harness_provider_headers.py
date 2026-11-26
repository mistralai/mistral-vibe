from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from vibe import __version__
from vibe.app_server import _runtime as runtime_module
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
from vibe.app_server.protocol import SessionOptions


def _use_litellm(config_dir: Path, extra_headers: dict[str, str]) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["providers"].append({
        "name": "litellm",
        "api_base": "https://litellm.example.com/v1",
        "api_key_env_var": "LITELLM_API_KEY",
        "backend": "generic",
        "extra_headers": extra_headers,
    })
    config["models"].append({
        "name": "claude-opus",
        "provider": "litellm",
        "alias": "opus",
    })
    config["active_model"] = "opus"
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


async def _session_request_headers(
    tmp_path: Path, process_services: ProcessServices
) -> dict[str, str]:
    process = runtime_module.HarnessProcess(services=process_services)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()
    return derivation.adapter_config.request_headers()


@pytest.mark.asyncio
async def test_session_requests_identify_vibe_to_the_provider(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    _use_litellm(config_dir, {"X-Team": "vibe"})
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    headers = await _session_request_headers(tmp_path, process_services)

    assert headers["user-agent"] == f"Mistral-Vibe/{__version__}"
    assert headers["X-Team"] == "vibe"


@pytest.mark.asyncio
async def test_session_requests_keep_a_user_agent_set_in_extra_headers(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    _use_litellm(config_dir, {"User-Agent": "my-gateway-client"})
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    headers = await _session_request_headers(tmp_path, process_services)

    assert [
        value for name, value in headers.items() if name.lower() == "user-agent"
    ] == ["my-gateway-client"]
