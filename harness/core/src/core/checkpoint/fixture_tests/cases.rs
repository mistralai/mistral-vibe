use std::collections::BTreeSet;

use serde_json::{Value, json};

use super::{CONFIG_SECRET, FixtureCase};
use crate::core::features::compaction::{
    CompactionBudget, CompactionProjection, CompactionProjectionFit, CompactionTrigger,
    PendingCompaction,
};
use crate::core::features::notifications::NotificationState;
use crate::core::features::programmatic_tool_calling::{
    PendingProgramRecord, ProgramExecution, ProgramFunctionRecord, ProgramOperationRecord,
};
use crate::core::features::skills;
use crate::core::hooks::hook_action_id;
use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::turn::SuspendedTurn;
use crate::core::{
    ActivePhase, ActiveTurn, AssistantPart, AssistantRole, AssistantSemanticPart, CandidateMessage,
    CompletionCandidate, CompletionFinishReason, CompletionPurpose, ContentBlock, ExternalTool,
    ExternalToolCall, HarnessConfig, HarnessState, HookPoint, Message, Notification,
    NotificationLevel, NotificationSource, PendingLifecycleHook, ProcessTerminalStatus,
    ProtocolError, QueuedTurn, SkillDefinition, StoredMessage, StructuredContent, TokenUsage,
    ToolArguments, ToolBatch, ToolCall, ToolExecution, ToolExecutionState, ToolOrigin, ToolResult,
    TurnOutcome, TurnState,
};

pub(super) fn fixture_cases() -> Vec<FixtureCase> {
    vec![
        FixtureCase {
            file_name: "idle-empty.json",
            state: empty_state(),
        },
        FixtureCase {
            file_name: "idle-history-notifications.json",
            state: idle_history_notifications_state(),
        },
        FixtureCase {
            file_name: "active-awaiting-agent-completion.json",
            state: awaiting_completion_state(CompletionPurpose::Agent),
        },
        FixtureCase {
            file_name: "active-awaiting-compaction-completion.json",
            state: awaiting_completion_state(CompletionPurpose::Compaction),
        },
        FixtureCase {
            file_name: "between-turn-manual-compaction.json",
            state: between_turn_manual_compaction_state(),
        },
        FixtureCase {
            file_name: "active-hook-pre-agent.json",
            state: pre_agent_hook_state(),
        },
        FixtureCase {
            file_name: "active-hook-pre-llm.json",
            state: completion_hook_state(HookPoint::PreLlmCall),
        },
        FixtureCase {
            file_name: "active-hook-post-llm.json",
            state: completion_hook_state(HookPoint::PostLlmCall),
        },
        FixtureCase {
            file_name: "active-hook-post-agent.json",
            state: completion_hook_state(HookPoint::PostAgentTurn),
        },
        FixtureCase {
            file_name: "active-tool-batch.json",
            state: tool_batch_state(),
        },
        FixtureCase {
            file_name: "active-skill-read.json",
            state: skill_read_state(),
        },
        terminal_case(
            "terminal-completed.json",
            TurnOutcome::Completed {
                output: vec![ContentBlock::image("aW1hZ2U=", "image/png")],
            },
        ),
        terminal_case(
            "terminal-rejected.json",
            TurnOutcome::Rejected {
                reason: "policy rejected the completion".to_string(),
            },
        ),
        terminal_case(
            "terminal-failed.json",
            TurnOutcome::Failed {
                error: protocol_error("provider_failed", "provider request failed"),
            },
        ),
        terminal_case(
            "terminal-interrupted.json",
            TurnOutcome::Interrupted {
                reason: Some("user stopped the turn".to_string()),
            },
        ),
        terminal_case(
            "terminal-interrupted-without-reason.json",
            TurnOutcome::Interrupted { reason: None },
        ),
    ]
}

fn fixture_config() -> HarnessConfig {
    let mut config = crate::core::testing::config();
    config.task_id = "checkpoint-fixture-task".to_string();
    config.system_instructions = CONFIG_SECRET.to_string();
    config
}

fn empty_state() -> HarnessState {
    crate::core::initial_state(fixture_config()).unwrap()
}

fn historical_state() -> HarnessState {
    let mut state = crate::core::initial_state_with_history(
        fixture_config(),
        vec![
            Message::system_text("historical system message"),
            Message::user_text("historical user message"),
        ],
    )
    .unwrap();
    state
        .context
        .push(StoredMessage::injected(Message::user_text(
            "runtime-injected context",
        )));
    state.compaction_count = 2;
    state
}

fn idle_history_notifications_state() -> HarnessState {
    let mut state = historical_state();
    let pending = Notification {
        id: "notification-pending".to_string(),
        source: NotificationSource::BackgroundProcess {
            process_id: "process-1".to_string(),
            status: ProcessTerminalStatus::Completed,
            exit_code: Some(0),
        },
        level: NotificationLevel::Info,
        message: "background process completed".to_string(),
        content: vec![text_block("notification attachment")],
    };
    state.notifications = NotificationState::try_from_parts_with_delivery(
        vec![pending],
        true,
        BTreeSet::from([
            "notification-pending".to_string(),
            "notification-received".to_string(),
        ]),
    )
    .unwrap();
    state
}

fn awaiting_completion_state(purpose: CompletionPurpose) -> HarnessState {
    let mut state = historical_state();
    let turn_id = "turn-active".to_string();
    let iterations = 3;
    let compaction_projection = if purpose == CompletionPurpose::Compaction {
        state
            .context
            .push(StoredMessage::visible(Message::assistant(vec![
                AssistantPart::Content(text_block("completed projected round")),
            ])));
        state
            .context
            .push(StoredMessage::visible(Message::user_text(
                "current raw request",
            )));
        Some(
            fixture_projection(&state, "")
                .retry_after_invalid_summary()
                .expect("fixture summary retry is valid")
                .expect("fixture projection has one summary retry"),
        )
    } else {
        None
    };
    let phase = match purpose {
        CompletionPurpose::Agent => ActivePhase::AwaitingCompletion {
            action_id: crate::core::action_id::completion("checkpoint-fixture-task", 2, 3),
        },
        CompletionPurpose::Compaction => ActivePhase::AwaitingCompaction {
            pending: PendingCompaction::in_turn(
                crate::core::action_id::compaction("checkpoint-fixture-task", 3),
                turn_id.clone(),
                iterations,
                compaction_projection.expect("compaction fixture has a projection"),
            ),
        },
    };
    state.turn = TurnState::Active(ActiveTurn {
        turn_id,
        iterations,
        phase,
        pending_steer: Vec::new(),
        model_input_ready: false,
        queued: QueuedTurn::Empty,
    });
    state
}

fn between_turn_manual_compaction_state() -> HarnessState {
    let mut state = historical_state();
    let projection = fixture_projection(&state, "preserve fixture details");
    state.turn = TurnState::Compacting {
        pending: PendingCompaction::between_turns(
            crate::core::action_id::compaction("checkpoint-fixture-task", 3),
            CompactionTrigger::Manual,
            projection,
        ),
    };
    state
}

fn pre_agent_hook_state() -> HarnessState {
    let mut state = empty_state();
    let turn_id = "turn-pre-agent".to_string();
    state.turn = TurnState::Active(ActiveTurn {
        turn_id: turn_id.clone(),
        iterations: 0,
        phase: ActivePhase::AwaitingHook {
            pending: PendingLifecycleHook::PreAgentTurn {
                action_id: hook_action_id(&turn_id, HookPoint::PreAgentTurn),
                hook_binding_ids: vec!["hook-pre-agent".to_string()],
                turn_id,
                user_content: vec![text_block("start work")],
                suspended: SuspendedTurn::Idle,
            },
        },
        pending_steer: Vec::new(),
        model_input_ready: false,
        queued: QueuedTurn::Empty,
    });
    state
}

fn completion_hook_state(point: HookPoint) -> HarnessState {
    let mut state = historical_state();
    let completion_action_id = crate::core::action_id::completion("checkpoint-fixture-task", 2, 3);
    let action_id = hook_action_id(&completion_action_id, point);
    let hook_binding_ids = vec![format!("hook-{point:?}")];
    let pending = match point {
        HookPoint::PreLlmCall => PendingLifecycleHook::PreLlmCall {
            action_id,
            hook_binding_ids,
            completion_action_id,
        },
        HookPoint::PostLlmCall => PendingLifecycleHook::PostLlmCall {
            action_id,
            hook_binding_ids,
            completion_action_id,
            candidate: completion_candidate(),
        },
        HookPoint::PostAgentTurn => PendingLifecycleHook::PostAgentTurn {
            action_id,
            hook_binding_ids,
            completion_action_id,
            candidate: completion_candidate(),
        },
        point => panic!("unsupported completion-hook fixture point: {point:?}"),
    };
    state.turn = TurnState::Active(ActiveTurn {
        turn_id: "turn-hook".to_string(),
        iterations: 2,
        phase: ActivePhase::AwaitingHook { pending },
        pending_steer: Vec::new(),
        model_input_ready: false,
        queued: QueuedTurn::Empty,
    });
    state
}

fn completion_candidate() -> CompletionCandidate {
    CompletionCandidate {
        message: CandidateMessage {
            role: AssistantRole::Assistant,
            content: vec![AssistantPart::Content(text_block("candidate answer"))],
        },
        finish_reason: CompletionFinishReason::Stop,
        usage: Some(TokenUsage {
            input_tokens: 10,
            output_tokens: 4,
            total_tokens: 14,
            cached_input_tokens: 3,
        }),
    }
}

fn tool_batch_state() -> HarnessState {
    let mut state = historical_state();
    let assistant_message_count = state.context.message_count() + 1;
    let executions = vec![
        direct_pre_hook_execution(),
        direct_pending_execution(),
        direct_post_hook_execution(),
        completed_execution(),
        completed_failure_execution(),
        program_execution(assistant_message_count),
    ];
    state
        .context
        .push(StoredMessage::visible(tool_batch_assistant_message(
            &executions,
        )));
    state.turn = TurnState::Active(ActiveTurn {
        turn_id: "turn-tools".to_string(),
        iterations: 4,
        phase: ActivePhase::AwaitingToolBatch {
            batch: ToolBatch { executions },
        },
        pending_steer: vec![StoredMessage::visible(Message::user_text(
            "steer after the current batch",
        ))],
        model_input_ready: false,
        queued: QueuedTurn::Pending {
            turn_id: "turn-queued".to_string(),
            messages: vec![StoredMessage::visible(Message::user_text(
                "queued follow-up",
            ))],
        },
    });
    state
}

fn skill_read_state() -> HarnessState {
    let mut config = fixture_config();
    config.capabilities.skills.push(SkillDefinition {
        name: "zeta-review".to_string(),
        description: "Review fixture state.".to_string(),
        path: "/skills/zeta/SKILL.md".to_string(),
    });
    let mut state = crate::core::initial_state_with_history(
        config,
        vec![Message::user_text("load the review skill")],
    )
    .unwrap();
    let execution = direct_skill_pending_execution();
    state
        .context
        .push(StoredMessage::visible(tool_batch_assistant_message(
            std::slice::from_ref(&execution),
        )));
    state.turn = TurnState::Active(ActiveTurn {
        turn_id: "turn-skill-read".to_string(),
        iterations: 1,
        phase: ActivePhase::AwaitingToolBatch {
            batch: ToolBatch {
                executions: vec![execution],
            },
        },
        pending_steer: Vec::new(),
        model_input_ready: false,
        queued: QueuedTurn::Empty,
    });
    state
}

fn fixture_projection(state: &HarnessState, extra_instructions: &str) -> CompactionProjection {
    let projection = CompactionProjection::initial(
        state.context.messages(),
        state.generated_system_message(),
        extra_instructions,
        state.resolved_tools.top_level_tools(),
        CompactionBudget {
            token_threshold: state.config.settings.context.compaction.token_threshold(),
            image_delivery: state.config.settings.context.image_delivery.compaction,
        },
    )
    .expect("fixture compaction projection can be estimated");
    let CompactionProjectionFit::Fitted(projection) = projection else {
        panic!("fixture compaction projection is valid");
    };
    projection
}

fn tool_batch_assistant_message(executions: &[ToolExecution]) -> Message {
    Message::assistant(
        executions
            .iter()
            .map(|execution| {
                AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                    id: execution.call.id.clone(),
                    name: execution.call.name.clone(),
                    arguments: ToolArguments::Json {
                        raw: serde_json::to_string(&execution.call.arguments).unwrap(),
                        value: execution.call.arguments.clone(),
                    },
                    meta: None,
                })
            })
            .collect(),
    )
}

fn direct_pre_hook_execution() -> ToolExecution {
    let call_id = "direct-pre";
    let call = external_read_call(call_id, ToolOrigin::TopLevel, "pre.txt");
    ToolExecution {
        call: model_tool_call(call_id, "read_file", json!({ "path": "pre.txt" })),
        state: ToolExecutionState::DirectAwaitingPreHook {
            hook_action_id: hook_action_id(&call.action_id, HookPoint::PreToolCall),
            hook_binding_ids: vec!["hook-direct-pre".to_string()],
            call,
        },
    }
}

fn direct_pending_execution() -> ToolExecution {
    let call_id = "direct-pending";
    ToolExecution {
        call: model_tool_call(call_id, "read_file", json!({ "path": "pending.txt" })),
        state: ToolExecutionState::DirectPending {
            call: external_read_call(call_id, ToolOrigin::TopLevel, "pending.txt"),
        },
    }
}

fn direct_skill_pending_execution() -> ToolExecution {
    let call_id = "direct-skill";
    let arguments = json!({ "name": "zeta-review" });
    ToolExecution {
        call: model_tool_call(call_id, "skill", arguments.clone()),
        state: ToolExecutionState::DirectPending {
            call: ExternalToolCall {
                action_id: crate::core::tools::external::effect_id_for_operation(
                    ToolOrigin::TopLevel,
                    call_id,
                ),
                call_id: call_id.to_string(),
                origin: ToolOrigin::TopLevel,
                call: ExternalTool::RuntimeBuiltin {
                    name: skills::ToolName::Read.runtime_tool(),
                    invocation_name: "skill".to_string(),
                    arguments,
                },
            },
        },
    }
}

fn direct_post_hook_execution() -> ToolExecution {
    let call_id = "direct-post";
    let call = external_read_call(call_id, ToolOrigin::TopLevel, "post.txt");
    ToolExecution {
        call: model_tool_call(call_id, "read_file", json!({ "path": "post.txt" })),
        state: ToolExecutionState::DirectAwaitingPostHook {
            hook_action_id: hook_action_id(&call.action_id, HookPoint::PostToolCall),
            hook_binding_ids: vec!["hook-direct-post".to_string()],
            call,
            result: successful_tool_result("raw direct result"),
        },
    }
}

fn completed_execution() -> ToolExecution {
    let call_id = "direct-completed";
    ToolExecution {
        call: model_tool_call(
            call_id,
            "read_file",
            json!({
                "path": "completed.txt",
                "opaque_payload": {
                    "action_id": "user data",
                    "configuration": "user data",
                    "receipt": "user data"
                }
            }),
        ),
        state: ToolExecutionState::Completed {
            message: Message::tool_success_text(
                call_id.to_string(),
                "read_file".to_string(),
                "completed direct result",
            ),
        },
    }
}

fn completed_failure_execution() -> ToolExecution {
    let call_id = "direct-completed-failure";
    ToolExecution {
        call: model_tool_call(
            call_id,
            "read_file",
            json!({ "path": "missing-completed.txt" }),
        ),
        state: ToolExecutionState::Completed {
            message: Message::tool_failure_text(
                call_id.to_string(),
                "read_file".to_string(),
                "completed direct failure",
            ),
        },
    }
}

fn program_execution(message_count: usize) -> ToolExecution {
    let execution_id = crate::core::features::programmatic_tool_calling::typescript_execution_id(
        message_count,
        "program-call",
        0,
    );
    let operation_id = |kind: &str, index: u8| format!("{execution_id}:{kind}:{index}");
    let pre_id = operation_id("tool", 2);
    let pending_id = operation_id("tool", 3);
    let post_id = operation_id("tool", 4);
    let resolved_id = operation_id("tool", 5);
    let rejected_id = operation_id("tool", 6);
    let pre_call = external_read_call(&pre_id, ToolOrigin::Programmatic, "pre.txt");
    let pending_call = external_read_call(&pending_id, ToolOrigin::Programmatic, "pending.txt");
    let post_call = external_read_call(&post_id, ToolOrigin::Programmatic, "effective-post.txt");
    let operations = vec![
        ProgramOperationRecord::InternalResolved {
            id: operation_id("step", 0),
            value: json!(1_700_000_000_000_u64),
        },
        ProgramOperationRecord::InternalRejected {
            id: operation_id("step", 1),
            error: json!({ "name": "SyntaxError", "message": "unexpected end of JSON" }),
        },
        ProgramOperationRecord::ExternalPending {
            id: pre_call.call_id.clone(),
            function: program_function("read_file", json!({ "path": "pre.txt" })),
            pending: Box::new(PendingProgramRecord::AwaitingPreHook {
                hook_action_id: hook_action_id(&pre_call.action_id, HookPoint::PreToolCall),
                hook_binding_ids: vec!["hook-program-pre".to_string()],
                call: pre_call,
            }),
        },
        ProgramOperationRecord::ExternalPending {
            id: pending_call.call_id.clone(),
            function: program_function("read_file", json!({ "path": "pending.txt" })),
            pending: Box::new(PendingProgramRecord::Pending { call: pending_call }),
        },
        ProgramOperationRecord::ExternalPending {
            id: post_call.call_id.clone(),
            function: program_function("read_file", json!({ "path": "original-post.txt" })),
            pending: Box::new(PendingProgramRecord::AwaitingPostHook {
                hook_action_id: hook_action_id(&post_call.action_id, HookPoint::PostToolCall),
                hook_binding_ids: vec!["hook-program-post".to_string()],
                result: successful_tool_result("raw program result"),
                call: post_call,
            }),
        },
        ProgramOperationRecord::ExternalResolved {
            id: resolved_id,
            function: program_function("read_file", json!({ "path": "resolved.txt" })),
            value: json!({ "path": "resolved.txt", "content": "resolved" }),
            content: vec![ContentBlock::image("cHJldmlldw==", "image/png")],
        },
        ProgramOperationRecord::ExternalRejected {
            id: rejected_id,
            function: program_function("read_file", json!({ "path": "missing.txt" })),
            error: json!({ "code": "not_found", "message": "missing.txt was not found" }),
            content: vec![text_block("external failure context")],
        },
    ];
    ToolExecution {
        call: model_tool_call(
            "program-call",
            "run_typescript",
            json!({ "code": fixture_program_code() }),
        ),
        state: ToolExecutionState::ProgramPending {
            execution: ProgramExecution::restore(fixture_program_code().to_string(), operations, 0)
                .unwrap(),
        },
    }
}

fn fixture_program_code() -> &'static str {
    r#"async function main() {
  await step(() => 1700000000000);
  try {
    await step(() => { throw new SyntaxError("unexpected end of JSON"); });
  } catch (_) {}
  return Promise.allSettled([
    tools.file_system.read_file({ path: "pre.txt" }),
    tools.file_system.read_file({ path: "pending.txt" }),
    tools.file_system.read_file({ path: "original-post.txt" }),
    tools.file_system.read_file({ path: "resolved.txt" }),
    tools.file_system.read_file({ path: "missing.txt" })
  ]);
}"#
}

fn program_function(name: &str, arguments: Value) -> ProgramFunctionRecord {
    ProgramFunctionRecord {
        name: name.to_string(),
        arguments,
    }
}

fn external_read_call(call_id: &str, origin: ToolOrigin, path: &str) -> ExternalToolCall {
    ExternalToolCall {
        action_id: crate::core::tools::external::effect_id_for_operation(origin, call_id),
        call_id: call_id.to_string(),
        origin,
        call: ExternalTool::RuntimeBuiltin {
            name: RuntimeBuiltinToolName::FileSystemReadFile,
            invocation_name: "read_file".to_string(),
            arguments: json!({ "path": path }),
        },
    }
}

fn model_tool_call(id: &str, name: &str, arguments: Value) -> ToolCall {
    ToolCall {
        id: id.to_string(),
        name: name.to_string(),
        arguments,
        argument_error: None,
    }
}

fn successful_tool_result(text: &str) -> ToolResult {
    ToolResult::Success {
        content: vec![text_block(text)],
        structured_content: StructuredContent::present(json!({ "text": text })),
        meta: Some(serde_json::Map::from_iter([(
            "fixture".to_string(),
            json!({ "opaque": true }),
        )])),
    }
}

fn terminal_case(file_name: &'static str, outcome: TurnOutcome) -> FixtureCase {
    let mut state = historical_state();
    let turn_id = file_name.trim_end_matches(".json").to_string();
    state.last_finished_turn_id = Some(turn_id.clone());
    state.turn = TurnState::Terminal { turn_id, outcome };
    FixtureCase { file_name, state }
}

fn text_block(text: &str) -> ContentBlock {
    ContentBlock::text(text.to_string())
}

fn protocol_error(code: &str, message: &str) -> ProtocolError {
    ProtocolError {
        code: code.to_string(),
        message: message.to_string(),
        retryable: false,
        details: json!({ "fixture": true }),
    }
}
