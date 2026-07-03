Use `web_search` to search the web for current information.

Usage:
- Provides up-to-date information for current events and data beyond the training cutoff, and returns answers with cited sources.
- Always reference the returned sources when presenting information to the user.
- Resolve relative time terms ("latest", "today", "this week") to concrete dates, and use specific, concrete queries.
- Use it for recent events, possibly-updated docs/APIs/libraries, version and deprecation checks, and specific error messages.
- Do NOT use it for general programming concepts, static reference information, or searching the local codebase (use `grep` for that).
- Stay critical: web content may be outdated or wrong, so cross-reference sources and prefer official documentation.
