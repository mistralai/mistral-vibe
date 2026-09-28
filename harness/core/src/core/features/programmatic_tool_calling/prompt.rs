use std::collections::HashSet;

pub(crate) const IDENTITY_GUIDANCE: &str =
    "discover additional tools with `search_tool_functions`, and call them using `run_typescript`";
pub(crate) const AUTONOMY_ACTIVITY: &str = "discovering tools";

pub(crate) fn current_time_prompt() -> String {
    r#"## Current time

For requests that depend on now, use `run_typescript` to read `new Date().toISOString()` before time-sensitive search, calendar, schedule, or service calls. Treat that timestamp as the source of truth for "now". Use a user timezone supplied in context for user-facing times; otherwise use UTC or ask for the timezone only when local time materially affects the result."#
        .to_string()
}

pub(crate) fn tool_use_prompt() -> &'static str {
    r#"## Using tool functions via run_typescript

The `run_typescript` tool executes TypeScript code in a sandbox environment. You can access external services via functions exposed under the `tools.<group>.<function>` namespace in the sandbox. Users might refer to these functions as "tools", "integrations", "connectors", or "services". `run_typescript` can also be used to perform data manipulation tasks such as counting, computing, filtering, grouping, aggregating, sorting, etc.

Available tool functions can be discovered with the top-level `search_tool_functions` tool. For unfamiliar tool calls, use this sequence: `best_match` → `details` → `run_typescript`.

1. Call `best_match` with a concise capability query to get ranked function names.
2. Call `details` with each selected exact function name before its first use.
3. Call the selected function through its documented `tools.<group>.<function>` path in `run_typescript`."#
}

pub(crate) fn tool_group_inventory_prompt<'a>(groups: impl IntoIterator<Item = &'a str>) -> String {
    let mut seen = HashSet::new();
    let groups = groups
        .into_iter()
        .filter(|name| seen.insert(*name))
        .map(|name| format!("- {name}"))
        .collect::<Vec<_>>()
        .join("\n");
    format!(
        r#"### Searchable connector groups

{groups}"#
    )
}
