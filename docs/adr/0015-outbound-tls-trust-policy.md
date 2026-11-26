# 0015 Outbound TLS Trust Policy

## Decision

Every Vibe-owned connection that verifies TLS certificates must respect the
effective `enable_system_trust_store` setting.

The setting is enabled by default.

- When disabled, use the bundled Certifi roots.
- When enabled, use the operating system trust store and the bundled roots.
- In both cases, add certificates from `SSL_CERT_FILE` and `SSL_CERT_DIR`.

Resolve and apply this policy before constructing a network client, including
during bootstrap, onboarding, and browser sign-in. Vibe-owned clients must not
silently fall back to a library's default SSL context. Connections made by an
external browser or subprocess are outside this boundary, except the `uv`
subprocesses Vibe runs to check for and install updates: when the setting is
enabled, Vibe sets `UV_SYSTEM_CERTS=true` for them, and adds `--system-certs` to
the uv commands it prints, unless the user set `UV_SYSTEM_CERTS` or
`UV_NATIVE_TLS`. The install script runs before any Vibe configuration exists,
so it defaults `UV_SYSTEM_CERTS` to `true` under the same exception. Both need
uv 0.11 or later. uv receives `SSL_CERT_FILE` and `SSL_CERT_DIR` unchanged and
treats them as replacing its roots, not adding to them; the environment
variable also overrides a `system-certs` setting in the user's `uv.toml`.

## Rationale

Users behind TLS-inspecting corporate proxies depend on private certificate
authorities installed in the system trust store. Applying the setting only
after authentication makes the authentication connection fail before Vibe can
start.

## Strict chain validation

The Certifi path sets `VERIFY_X509_STRICT` explicitly, so it validates chains
against stricter RFC 5280 rules (SKID/AKID presence and matching, critical
BasicConstraints on CAs, CA KeyUsage) on every supported Python version. It also
sets `VERIFY_X509_PARTIAL_CHAIN`, which lets a trusted intermediate anchor a
chain, to match the Python 3.13+ `ssl.create_default_context()` default.
Because Python 3.12 sets neither flag, Vibe never relies on that default. The
system-trust-store path (`truststore`) does not set these flags. A private CA
chain that a lenient client accepts can therefore be rejected; affected users
should enable `enable_system_trust_store` or issue a compliant CA. Vibe keeps
the stricter validation deliberately.

## Agent Guidance

- Use Vibe's shared HTTP/TLS helpers for Vibe-owned clients.
- Apply the effective trust policy before creating or caching a client.
- Add a test with `enable_system_trust_store = true` for new bootstrap or
  connection paths.

## Flag To User When

- A dependency cannot accept Vibe's SSL context or equivalent trust policy.
- A connection must be created before effective configuration is available.
