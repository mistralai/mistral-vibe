from __future__ import annotations

import pytest

from vibe.core.paths.global_paths import GLOBAL_ENV_FILE
from vibe.core.proxy_setup import (
    SUPPORTED_PROXY_VARS,
    ProxySetupError,
    get_current_proxy_settings,
    parse_proxy_command,
    set_proxy_var,
    unset_proxy_var,
)


def _write_env_file(content: str) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_ENV_FILE.path.write_text(content, encoding="utf-8")


class TestProxySetupError:
    def test_inherits_from_exception(self) -> None:
        assert issubclass(ProxySetupError, Exception)

    def test_preserves_message(self) -> None:
        error = ProxySetupError("test message")
        assert str(error) == "test message"


class TestSupportedProxyVars:
    def test_contains_all_expected_keys(self) -> None:
        expected_keys = {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        }
        assert set(SUPPORTED_PROXY_VARS.keys()) == expected_keys

    def test_all_keys_are_uppercase(self) -> None:
        for key in SUPPORTED_PROXY_VARS:
            assert key == key.upper()

    def test_all_values_are_non_empty_strings(self) -> None:
        for value in SUPPORTED_PROXY_VARS.values():
            assert isinstance(value, str)
            assert len(value) > 0


class TestGetCurrentProxySettings:
    def test_returns_all_none_when_env_file_does_not_exist(self) -> None:
        result = get_current_proxy_settings()

        assert all(value is None for value in result.values())

    def test_returns_dict_with_all_supported_keys(self) -> None:
        result = get_current_proxy_settings()

        assert set(result.keys()) == set(SUPPORTED_PROXY_VARS.keys())

    def test_returns_values_from_env_file(self) -> None:
        _write_env_file(
            "HTTP_PROXY=http://proxy:8080\nHTTPS_PROXY=https://proxy:8443\n"
        )

        result = get_current_proxy_settings()

        assert result["HTTP_PROXY"] == "http://proxy:8080"
        assert result["HTTPS_PROXY"] == "https://proxy:8443"

    def test_returns_none_for_unset_keys(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        result = get_current_proxy_settings()

        assert result["HTTP_PROXY"] == "http://proxy:8080"
        assert result["HTTPS_PROXY"] is None
        assert result["ALL_PROXY"] is None

    def test_ignores_non_proxy_vars_in_env_file(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\nOTHER_VAR=ignored\n")

        result = get_current_proxy_settings()

        assert "OTHER_VAR" not in result
        assert result["HTTP_PROXY"] == "http://proxy:8080"

    def test_handles_values_with_special_characters(self) -> None:
        _write_env_file("HTTP_PROXY=http://user:p@ss@proxy:8080\n")

        result = get_current_proxy_settings()

        assert result["HTTP_PROXY"] == "http://user:p@ss@proxy:8080"

    def test_returns_all_none_when_env_file_read_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        def raise_error(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr("vibe.core.proxy_setup.dotenv_values", raise_error)

        result = get_current_proxy_settings()

        assert all(value is None for value in result.values())


class TestSetProxyVar:
    def test_sets_valid_proxy_var(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://proxy:8080")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] == "http://proxy:8080"

    @pytest.mark.parametrize("key", SUPPORTED_PROXY_VARS.keys())
    def test_sets_all_supported_vars(self, key: str) -> None:
        set_proxy_var(key, "test-value")

        result = get_current_proxy_settings()
        assert result[key] == "test-value"

    def test_uppercases_key_before_validation(self) -> None:
        set_proxy_var("http_proxy", "http://proxy:8080")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] == "http://proxy:8080"

    def test_raises_error_for_unknown_key(self) -> None:
        with pytest.raises(ProxySetupError) as exc_info:
            set_proxy_var("UNKNOWN_KEY", "value")

        assert "Unknown key 'UNKNOWN_KEY'" in str(exc_info.value)

    def test_error_message_contains_supported_keys(self) -> None:
        with pytest.raises(ProxySetupError) as exc_info:
            set_proxy_var("UNKNOWN_KEY", "value")

        error_msg = str(exc_info.value)
        assert "HTTP_PROXY" in error_msg
        assert "HTTPS_PROXY" in error_msg

    def test_creates_env_file_if_missing(self) -> None:
        assert not GLOBAL_ENV_FILE.path.exists()

        set_proxy_var("HTTP_PROXY", "http://proxy:8080")

        assert GLOBAL_ENV_FILE.path.exists()

    def test_overwrites_existing_value(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://old:8080")
        set_proxy_var("HTTP_PROXY", "http://new:8080")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] == "http://new:8080"

    def test_preserves_other_values(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://proxy:8080")
        set_proxy_var("HTTPS_PROXY", "https://proxy:8443")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] == "http://proxy:8080"
        assert result["HTTPS_PROXY"] == "https://proxy:8443"

    def test_handles_value_with_spaces(self) -> None:
        set_proxy_var("NO_PROXY", "localhost, 127.0.0.1, .local")

        result = get_current_proxy_settings()
        assert result["NO_PROXY"] == "localhost, 127.0.0.1, .local"

    def test_handles_url_with_credentials(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://user:password@proxy:8080")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] == "http://user:password@proxy:8080"


class TestUnsetProxyVar:
    def test_removes_existing_var(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://proxy:8080")
        unset_proxy_var("HTTP_PROXY")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] is None

    def test_uppercases_key_before_validation(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://proxy:8080")
        unset_proxy_var("http_proxy")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] is None

    def test_raises_error_for_unknown_key(self) -> None:
        with pytest.raises(ProxySetupError) as exc_info:
            unset_proxy_var("UNKNOWN_KEY")

        assert "Unknown key 'UNKNOWN_KEY'" in str(exc_info.value)

    def test_error_message_contains_supported_keys(self) -> None:
        with pytest.raises(ProxySetupError) as exc_info:
            unset_proxy_var("UNKNOWN_KEY")

        error_msg = str(exc_info.value)
        assert "HTTP_PROXY" in error_msg

    def test_no_op_when_env_file_does_not_exist(self) -> None:
        assert not GLOBAL_ENV_FILE.path.exists()

        unset_proxy_var("HTTP_PROXY")

        assert not GLOBAL_ENV_FILE.path.exists()

    def test_no_op_when_key_not_in_file(self) -> None:
        set_proxy_var("HTTPS_PROXY", "https://proxy:8443")
        unset_proxy_var("HTTP_PROXY")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] is None
        assert result["HTTPS_PROXY"] == "https://proxy:8443"

    def test_preserves_other_values(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://proxy:8080")
        set_proxy_var("HTTPS_PROXY", "https://proxy:8443")
        unset_proxy_var("HTTP_PROXY")

        result = get_current_proxy_settings()
        assert result["HTTP_PROXY"] is None
        assert result["HTTPS_PROXY"] == "https://proxy:8443"

    @pytest.mark.parametrize("key", SUPPORTED_PROXY_VARS.keys())
    def test_all_supported_vars_can_be_unset(self, key: str) -> None:
        set_proxy_var(key, "test-value")
        unset_proxy_var(key)

        result = get_current_proxy_settings()
        assert result[key] is None


class TestParseProxyCommand:
    def test_parses_key_only(self) -> None:
        key, value = parse_proxy_command("HTTP_PROXY")

        assert key == "HTTP_PROXY"
        assert value is None

    def test_parses_key_and_value(self) -> None:
        key, value = parse_proxy_command("HTTP_PROXY http://proxy:8080")

        assert key == "HTTP_PROXY"
        assert value == "http://proxy:8080"

    def test_uppercases_key(self) -> None:
        key, value = parse_proxy_command("http_proxy")

        assert key == "HTTP_PROXY"

    def test_preserves_value_case(self) -> None:
        key, value = parse_proxy_command("HTTP_PROXY http://Proxy:8080")

        assert value == "http://Proxy:8080"

    def test_strips_leading_whitespace(self) -> None:
        key, value = parse_proxy_command("  HTTP_PROXY")

        assert key == "HTTP_PROXY"

    def test_strips_trailing_whitespace(self) -> None:
        key, value = parse_proxy_command("HTTP_PROXY  ")

        assert key == "HTTP_PROXY"
        assert value is None

    def test_splits_on_first_space_only(self) -> None:
        key, value = parse_proxy_command("NO_PROXY localhost, 127.0.0.1, .local")

        assert key == "NO_PROXY"
        assert value == "localhost, 127.0.0.1, .local"

    def test_raises_error_for_empty_string(self) -> None:
        with pytest.raises(ProxySetupError) as exc_info:
            parse_proxy_command("")

        assert "No key provided" in str(exc_info.value)

    def test_raises_error_for_whitespace_only(self) -> None:
        with pytest.raises(ProxySetupError) as exc_info:
            parse_proxy_command("   ")

        assert "No key provided" in str(exc_info.value)

    def test_handles_tab_as_separator(self) -> None:
        key, value = parse_proxy_command("HTTP_PROXY\thttp://proxy:8080")

        assert key == "HTTP_PROXY"
        assert value == "http://proxy:8080"

    def test_handles_multiple_spaces_as_separator(self) -> None:
        key, value = parse_proxy_command("HTTP_PROXY   http://proxy:8080")

        assert key == "HTTP_PROXY"
        assert value == "http://proxy:8080"
