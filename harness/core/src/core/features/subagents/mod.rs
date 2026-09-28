mod agent_types;
mod settings;
mod tools;

pub(crate) use agent_types::{AgentTypeDefinition, render_agent_types, validate_agent_types};
pub(crate) use settings::{Mode, validate_reconfiguration};
pub(crate) use tools::tools;

pub(crate) const NAMESPACE: &str = "agent";
pub(crate) const SEARCH_DESCRIPTION: &str = "Tools to create and coordinate stateful subagents.";

const PROMPT_SECTION: &str = r#"## Subagents

Use stateful subagents through the programmatic `tools.agent` namespace:
- `tools.agent.spawn` starts a named subagent without waiting. Each new subagent needs a name never used by this parent session.
- `tools.agent.sendMessage` steers its active turn or starts another turn.
- `tools.agent.list` reports the subagents owned by this session.
- `tools.agent.wait` waits for one subagent's current turn.
- `tools.agent.interrupt` interrupts its active turn but keeps the session.
- `tools.agent.close` stops and discards the session but keeps its name reserved.

Subagent operations are external effects. Call them from `run_typescript`. Reuse a name only when continuing work with the same live subagent; choose a never-used name for each new subagent. Wait for work whose result is needed before answering."#;

pub(crate) fn prompt_section() -> &'static str {
    PROMPT_SECTION
}
