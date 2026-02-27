Use `web_search` to find current information from the web.
Returns answers with cited sources. Always reference sources when presenting information to the user.

**Query Best Practices:**
- Avoid relative time terms ("latest", "today", "this week") - resolve to actual dates when possible
- Be specific and use concrete terms rather than vague queries

**When to use:**
- User asks about recent events or explicitly asks to search the web
- Documentation, APIs, or libraries may have been updated since training cutoff
- Verifying facts that could be outdated (versions, deprecations, breaking changes)
- Looking up specific error messages or issues that may have known solutions
- User mentions a library, framework, or version you're not familiar with

**When NOT to use:**
- General programming concepts and patterns (use training knowledge)
- Searching the local codebase (use `grep` or file search instead)
- Static reference information unlikely to change (math, algorithms, language syntax)
- Information you're already confident about and is unlikely to have changed

**Using results:**
- Stay critical - web content may be outdated, wrong, or misleading
- Cross-reference multiple sources when possible
- Prefer official documentation over third-party sources
- Always cite your sources so the user can verify
