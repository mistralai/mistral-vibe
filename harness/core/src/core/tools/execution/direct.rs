use crate::core::error::CoreError;

use crate::core::hooks::HookPoint;
use crate::core::tools::context::ToolContext;
use crate::core::tools::execution::external_call::PendingExternalCall;
use crate::core::tools::external::{ExternalToolCall, ToolOrigin, effect_id_for_operation};
use crate::core::wire::tool::ToolCall;

pub(crate) fn direct_tool_execution_from_resolved_tools(
    tools: ToolContext<'_>,
    call: &ToolCall,
    determinism: crate::core::step_protocol::DeterminismContext,
) -> Result<PendingExternalCall, CoreError> {
    let mut tool = tools.resolve_direct_call(call)?;
    if let crate::core::tools::external::ExternalTool::RuntimeBuiltin {
        name: crate::core::tools::external::RuntimeBuiltinToolName::SelfWait,
        arguments,
        ..
    } = &mut tool
    {
        *arguments = crate::core::features::model_wait::runtime_arguments(
            arguments.take(),
            determinism.time_unix_ms,
        )?;
    }
    let external_call = ExternalToolCall {
        action_id: effect_id_for_operation(ToolOrigin::TopLevel, &call.id),
        call_id: call.id.clone(),
        origin: ToolOrigin::TopLevel,
        call: tool,
    };
    let hook_binding_ids =
        tools.hook_binding_ids(HookPoint::PreToolCall, Some(&external_call.hook_tool_key()));
    Ok(if hook_binding_ids.is_empty() {
        PendingExternalCall::pending(external_call)
    } else {
        PendingExternalCall::awaiting_pre_hook(external_call, hook_binding_ids)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::features::skills;
    use crate::core::testing::*;

    #[test]
    fn pre_tool_hooks_match_the_exact_canonical_tool_key_in_binding_order() {
        let mut config = config();
        add_tool_hook(
            &mut config,
            "write-only",
            HookPoint::PreToolCall,
            0,
            HookToolTarget::Filesystem,
            "file_system.write_file",
        );
        add_tool_hook(
            &mut config,
            "read-second",
            HookPoint::PreToolCall,
            20,
            HookToolTarget::Filesystem,
            "file_system.read_file",
        );
        add_tool_hook(
            &mut config,
            "read-first",
            HookPoint::PreToolCall,
            10,
            HookToolTarget::Filesystem,
            "file_system.read_file",
        );

        let step = advance_llm(
            started_with(config),
            assistant_tool("read_file", json!({"path": "a.txt"})),
        );

        assert!(matches!(
            step.effect,
            Some(Action::Hook {
                hook_binding_ids,
                call: HookCall::PreToolCall { tool_call },
                ..
            }) if hook_binding_ids == ["read-first", "read-second"]
                && tool_call.call.hook_tool_key().qualified_name == "file_system.read_file"
        ));
    }

    #[test]
    fn unmatched_post_tool_hook_does_not_add_a_hook_action() {
        let mut config = config();
        add_tool_hook(
            &mut config,
            "write-post",
            HookPoint::PostToolCall,
            0,
            HookToolTarget::Filesystem,
            "file_system.write_file",
        );
        let first = advance_llm(
            started_with(config),
            assistant_tool("read_file", json!({"path": "a.txt"})),
        );
        let Some(Action::RuntimeBuiltinTool { effect_id, .. }) = first.effect else {
            panic!("expected a direct tool action");
        };

        let completed = advance_tool(first.state, effect_id, json!({"content": "done"}), None);

        assert!(matches!(completed.effect, Some(Action::Completion { .. })));
        assert!(
            completed
                .effects
                .iter()
                .all(|action| !matches!(action, Action::Hook { .. }))
        );
    }

    // `skill.read` used to return no `HookToolKey`, and every dispatch site read that as
    // "no bindings" rather than "match against nothing" -- so no pre_tool hook fired for
    // it at all, not even an `always` one. A `match = "skill"` guard silently let the
    // call through.
    #[test]
    fn pre_tool_hooks_fire_for_the_skill_tool() {
        let mut config = config();
        config.capabilities.skills.push(SkillDefinition {
            name: "review".to_string(),
            description: "Review code.".to_string(),
            path: "/skills/review/SKILL.md".to_string(),
        });
        add_always_hook(&mut config, HookPoint::PreToolCall);
        add_tool_hook(
            &mut config,
            "skill-guard",
            HookPoint::PreToolCall,
            1,
            HookToolTarget::Skill,
            "skill.read",
        );

        let step = advance_llm(
            started_with(config),
            assistant_tool("skill", json!({"name": "review"})),
        );

        let Some(Action::Hook {
            hook_binding_ids,
            call: HookCall::PreToolCall { tool_call },
            ..
        }) = step.effect
        else {
            panic!("expected a pre_tool_call hook action");
        };
        assert_eq!(hook_binding_ids, ["test-hook-0-PreToolCall", "skill-guard"]);
        assert_eq!(tool_call.call.hook_tool_key().qualified_name, "skill.read");
    }

    #[test]
    fn skill_tool_is_a_runtime_effect_and_resumes_the_model() {
        let mut config = config();
        config.capabilities.skills.push(SkillDefinition {
            name: "review".to_string(),
            description: "Review code.".to_string(),
            path: "/skills/review/SKILL.md".to_string(),
        });
        let state = started_with(config);

        let step = advance_llm(state, assistant_tool("skill", json!({"name": "review"})));
        let Some(Action::RuntimeBuiltinTool {
            effect_id,
            operation_id,
            call,
            ..
        }) = step.effect
        else {
            panic!("expected a skill effect");
        };
        assert_eq!(operation_id, "call-1");
        assert_eq!(call.name, skills::ToolName::Read.runtime_tool());

        let resumed = advance_tool(step.state, effect_id.clone(), json!("loaded skill"), None);

        assert!(matches!(resumed.effect, Some(Action::Completion { .. })));
        assert_eq!(
            resumed.accepted_tool_result,
            Some(AcceptedToolResult {
                turn_id: "turn-1".to_string(),
                action_id: effect_id,
                operation_id: "call-1".to_string(),
                result: ToolResult::Success {
                    content: Vec::new(),
                    structured_content: StructuredContent::present(json!("loaded skill")),
                    meta: None,
                },
            })
        );
        assert!(
            resumed
                .state
                .context
                .messages()
                .last()
                .is_some_and(|message| {
                    message_role(&message.message) == Role::Tool
                        && message_text(message) == "loaded skill"
                })
        );
    }

    #[test]
    fn invalid_search_limits_return_an_error_to_the_model() {
        let step = advance_llm(
            started(),
            assistant_tool(
                "search_tool_functions",
                json!({"query": "search", "connectors": vec!["connector"; 11]}),
            ),
        );

        assert!(matches!(step.effect, Some(Action::Completion { .. })));
        let message = step.state.context.messages().last().unwrap();
        assert_eq!(message_role(&message.message), Role::Tool);
        assert!(message_text(message).contains("InvalidToolCall"));
        assert!(message_text(message).contains("at /connectors:"));
        assert!(message_text(message).contains("has more than 10 items"));
    }

    #[test]
    fn provided_tools_use_the_generic_runtime_route() {
        let mut config = config();
        config.capabilities.tool_groups.push(ToolGroupDefinition {
            name: "client".to_string(),
            description: "Client-side tools".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "select_customer".to_string(),
                description: String::new(),
                input_schema: json!({"type": "object"}),
                output_schema: None,
                exposure: ProvidedToolExposure::Programmatic,
            }],
        });
        let state = advance(
            initial_state(config).unwrap(),
            user_message("work", UserMessageMode::Queue),
        )
        .unwrap()
        .state;
        let step = advance_llm(
            state,
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.client.select_customer({}); }"}),
            ),
        );

        assert!(matches!(step.effect, Some(Action::ProvidedTool { .. })));
    }
}
