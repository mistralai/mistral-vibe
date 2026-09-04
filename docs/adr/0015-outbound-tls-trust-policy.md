# 0015 Outbound TLS Trust Policy

## Decision

Every Vibe-owned connection that verifies TLS certificates must respect the
effective `enable_system_trust_store` setting.

- When disabled, use the bundled Certifi roots.
- When enabled, use the operating system trust store.
- In both cases, add certificates from `SSL_CERT_FILE` and `SSL_CERT_DIR`.

Resolve and apply this policy before constructing a network client, including
during bootstrap, onboarding, and browser sign-in. Vibe-owned clients must not
silently fall back to a library's default SSL context. Connections made by an
external browser or subprocess are outside this boundary.

## Rationale

Users behind TLS-inspecting corporate proxies depend on private certificate
authorities installed in the system trust store. Applying the setting only
after authentication makes the authentication connection fail before Vibe can
start.

## Agent Guidance

- Use Vibe's shared HTTP/TLS helpers for Vibe-owned clients.
- Apply the effective trust policy before creating or caching a client.
- Add a test with `enable_system_trust_store = true` for new bootstrap or
  connection paths.

## Flag To User When

- A dependency cannot accept Vibe's SSL context or equivalent trust policy.
- A connection must be created before effective configuration is available.
