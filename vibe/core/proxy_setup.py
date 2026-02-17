from __future__ import annotations

from dotenv import dotenv_values, set_key, unset_key

from vibe.core.paths.global_paths import GLOBAL_ENV_FILE

SUPPORTED_PROXY_VARS: dict[str, str] = {
    "HTTP_PROXY": "Proxy URL for HTTP requests",
    "HTTPS_PROXY": "Proxy URL for HTTPS requests",
    "ALL_PROXY": "Proxy URL for all requests (fallback)",
    "NO_PROXY": "Comma-separated list of hosts to bypass proxy",
    "SSL_CERT_FILE": "Path to custom SSL certificate file",
    "SSL_CERT_DIR": "Path to directory containing SSL certificates",
}


class ProxySetupError(Exception):
    pass


def get_current_proxy_settings() -> dict[str, str | None]:
    if not GLOBAL_ENV_FILE.path.exists():
        return {key: None for key in SUPPORTED_PROXY_VARS}

    try:
        env_vars = dotenv_values(GLOBAL_ENV_FILE.path)
        return {key: env_vars.get(key) for key in SUPPORTED_PROXY_VARS}
    except Exception:
        return {key: None for key in SUPPORTED_PROXY_VARS}


def set_proxy_var(key: str, value: str) -> None:
    key = key.upper()
    if key not in SUPPORTED_PROXY_VARS:
        raise ProxySetupError(
            f"Unknown key '{key}'. Supported: {', '.join(SUPPORTED_PROXY_VARS.keys())}"
        )

    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, key, value)


def unset_proxy_var(key: str) -> None:
    key = key.upper()
    if key not in SUPPORTED_PROXY_VARS:
        raise ProxySetupError(
            f"Unknown key '{key}'. Supported: {', '.join(SUPPORTED_PROXY_VARS.keys())}"
        )

    if not GLOBAL_ENV_FILE.path.exists():
        return

    unset_key(GLOBAL_ENV_FILE.path, key)


def parse_proxy_command(args: str) -> tuple[str, str | None]:
    args = args.strip()
    if not args:
        raise ProxySetupError("No key provided")

    parts = args.split(maxsplit=1)
    key = parts[0].upper()
    value = parts[1] if len(parts) > 1 else None

    return key, value
