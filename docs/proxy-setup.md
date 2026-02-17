# Proxy Setup

Mistral Vibe supports proxy configuration for environments that require network traffic to go through a proxy server. Proxy settings are shared between the CLI and ACP — configuring them in one will apply to both.

## Using Mistral Vibe CLI

Configure proxy settings through the interactive form:

1. Type `/proxy-setup` and press Enter
2. Fill in the variables you need, then press **Enter** to save or **Escape** to cancel
3. **Restart the CLI** for changes to take effect

## Through an ACP Client

In ACP, variables must be set one at a time using the `/proxy-setup` command:

```bash
/proxy-setup HTTP_PROXY http://proxy.example.com:8080
```

Once all variables are configured, **restart the conversation** for changes to take effect.

## Supported Environment Variables

Mistral Vibe uses [httpx](https://www.python-httpx.org/environment_variables/) for HTTP requests and supports the same environment variables:

| Variable | Description |
|----------|-------------|
| `HTTP_PROXY` | Proxy URL for HTTP requests |
| `HTTPS_PROXY` | Proxy URL for HTTPS requests |
| `ALL_PROXY` | Proxy URL for all requests (fallback when HTTP_PROXY/HTTPS_PROXY are not set) |
| `NO_PROXY` | Comma-separated list of hosts that should bypass the proxy |
| `SSL_CERT_FILE` | Path to a custom SSL certificate file |
| `SSL_CERT_DIR` | Path to a directory containing SSL certificates |

These variables can also be set directly in your shell environment before launching the CLI (but will be overridden if set through the interactive form).
