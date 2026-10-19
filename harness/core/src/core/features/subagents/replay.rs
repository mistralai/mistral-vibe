//! Retired program names are replay-only; they never install executable routes.

use std::collections::BTreeSet;

use crate::core::features::programmatic_tool_calling::{ProgrammaticName, ReplayOnlyTool};

/// Namespaces that hold only replay-only functions. They stay reserved so a
/// provided tool group can never share a namespace with them, and they are
/// derived from [`rejected_tools`] so the two lists cannot drift apart. The
/// live namespace is reserved separately and is not retired.
pub(crate) fn retired_namespaces() -> BTreeSet<String> {
    rejected_tools()
        .into_iter()
        .map(|tool| tool.programmatic_name.namespace)
        .filter(|namespace| namespace != super::NAMESPACE)
        .collect()
}

/// Deprecated: every function returned here is a retired spelling of a
/// `tools.agents.*` function (or has no replacement). They exist only so
/// recorded results of older Sessions replay; new calls are rejected with the
/// replacement named in `rejection_message`.
pub(crate) fn rejected_tools() -> Vec<ReplayOnlyTool> {
    let mut tools = [
        "agent_list",
        "agent_spawn",
        "agent_message",
        "agent_interrupt",
        "agent_wait",
        "agent_stop",
    ]
    .into_iter()
    .filter_map(replay_only_tool)
    .collect::<Vec<_>>();
    // The retired `tools.subagent.*` spelling dispatched the same runtime names
    // as `tools.agent.*`, so its recorded results replay before new calls are rejected.
    for (function, runtime_name, replacement) in [
        ("list", "agent_list", "tools.agents.list"),
        ("spawn", "agent_spawn", "tools.agents.spawn"),
        ("sendMessage", "agent_message", "tools.agents.send_message"),
        ("interrupt", "agent_interrupt", "tools.agents.interrupt"),
        ("wait", "agent_wait", "wait outside run_typescript"),
        ("close", "agent_stop", "no replacement is available"),
    ] {
        tools.push(ReplayOnlyTool {
            name: runtime_name.to_string(),
            programmatic_name: ProgrammaticName {
                namespace: "subagent".to_string(),
                name: function.to_string(),
            },
            rejection_message: format!("tools.subagent.{function} is deprecated; {replacement}."),
        });
    }
    for (namespace, function, replacement) in [
        ("subagent", "send_message", "tools.agents.send_message"),
        ("subagent", "stop", "no replacement is available"),
        ("agent", "stop", "no replacement is available"),
        ("agents", "stop", "no replacement is available"),
        ("agents", "close", "no replacement is available"),
        ("collaboration", "spawn_agent", "tools.agents.spawn"),
        ("collaboration", "send_message", "tools.agents.send_message"),
        (
            "collaboration",
            "followup_task",
            "tools.agents.send_message",
        ),
        ("collaboration", "interrupt_agent", "tools.agents.interrupt"),
        ("collaboration", "list_agents", "tools.agents.list"),
        ("collaboration", "wait_agent", "wait outside run_typescript"),
    ] {
        tools.push(ReplayOnlyTool {
            name: format!("deprecated_{namespace}_{function}"),
            programmatic_name: ProgrammaticName {
                namespace: namespace.to_string(),
                name: function.to_string(),
            },
            rejection_message: format!(
                "tools.{namespace}.{function} is deprecated; {replacement}."
            ),
        });
    }
    tools
}

/// The program name to show for a recorded call to a retired runtime name.
/// `tools.agent.*` and `tools.subagent.*` record the same runtime names, so the
/// spelling the program source uses wins; otherwise `tools.agent.*` is shown.
pub(crate) fn recorded_program_name(runtime_name: &str, code: &str) -> Option<ProgrammaticName> {
    let names = rejected_tools()
        .into_iter()
        .filter(|tool| tool.name == runtime_name)
        .map(|tool| tool.programmatic_name)
        .collect::<Vec<_>>();
    names
        .iter()
        .find(|name| code.contains(&name.display_name()))
        .or_else(|| names.first())
        .cloned()
}

/// Deprecated: maps a retired `tools.agent.*` runtime name to its replay-only
/// stub; use `tools.agents.*` instead.
pub(crate) fn replay_only_tool(name: &str) -> Option<ReplayOnlyTool> {
    let (function, replacement) = match name {
        "agent_list" => ("list", "tools.agents.list"),
        "agent_spawn" => ("spawn", "tools.agents.spawn"),
        "agent_message" => ("sendMessage", "tools.agents.send_message"),
        "agent_interrupt" => ("interrupt", "tools.agents.interrupt"),
        "agent_wait" => ("wait", "wait"),
        "agent_stop" => ("close", "no replacement is available"),
        _ => return None,
    };
    Some(ReplayOnlyTool {
        name: name.to_string(),
        programmatic_name: ProgrammaticName {
            namespace: "agent".to_string(),
            name: function.to_string(),
        },
        rejection_message: if name == "agent_stop" {
            format!("tools.agent.{function} is deprecated; {replacement}.")
        } else {
            format!("tools.agent.{function} is deprecated; use {replacement}.")
        },
    })
}
