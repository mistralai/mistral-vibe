from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Rust renders inline-code backticks in the /connectors description; Python
# strips them. Rust is the better rendering, so skip Py-vs-Rust parity but
# keep the golden non-regression test.
skip_terminal_parity = (
    "rust keeps backticks in the /connectors menu description; python strips them"
)

timeline: Timeline = ["/"]
