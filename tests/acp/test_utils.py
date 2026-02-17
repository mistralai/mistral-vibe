from __future__ import annotations

from vibe.acp.utils import get_proxy_help_text
from vibe.core.paths.global_paths import GLOBAL_ENV_FILE
from vibe.core.proxy_setup import SUPPORTED_PROXY_VARS


def _write_env_file(content: str) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_ENV_FILE.path.write_text(content, encoding="utf-8")


class TestGetProxyHelpText:
    def test_returns_string(self) -> None:
        result = get_proxy_help_text()

        assert isinstance(result, str)

    def test_includes_proxy_configuration_header(self) -> None:
        result = get_proxy_help_text()

        assert "## Proxy Configuration" in result

    def test_includes_usage_section(self) -> None:
        result = get_proxy_help_text()

        assert "### Usage:" in result
        assert "/proxy-setup" in result

    def test_includes_all_supported_variables(self) -> None:
        result = get_proxy_help_text()

        for key in SUPPORTED_PROXY_VARS:
            assert f"`{key}`" in result

    def test_shows_none_configured_when_no_settings(self) -> None:
        result = get_proxy_help_text()

        assert "(none configured)" in result

    def test_shows_current_settings_when_configured(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        result = get_proxy_help_text()

        assert "HTTP_PROXY=http://proxy:8080" in result
        assert "(none configured)" not in result

    def test_shows_only_set_values(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        result = get_proxy_help_text()

        assert "HTTP_PROXY=http://proxy:8080" in result
        assert "HTTPS_PROXY=" not in result
