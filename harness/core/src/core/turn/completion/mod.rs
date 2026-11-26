pub(super) mod candidate;

use super::lifecycle_hooks::PendingLifecycleHook;
use crate::core::action_id;
use crate::core::error::CoreError;

pub(in crate::core::turn) enum AgentCompletionPlan {
    AwaitCompletion { action_id: String },
    AwaitPreLlmHook { pending: Box<PendingLifecycleHook> },
}

pub(in crate::core::turn) struct AgentCompletionRequest<'a> {
    task_id: &'a str,
    message_count: usize,
    compaction_count: u64,
    pre_llm_hook_binding_ids: Vec<String>,
}

impl<'a> AgentCompletionRequest<'a> {
    pub(in crate::core::turn) fn new(
        task_id: &'a str,
        message_count: usize,
        compaction_count: u64,
        pre_llm_hook_binding_ids: Vec<String>,
    ) -> Self {
        Self {
            task_id,
            message_count,
            compaction_count,
            pre_llm_hook_binding_ids,
        }
    }
}

pub(in crate::core::turn) fn plan_agent_completion(
    request: AgentCompletionRequest<'_>,
) -> Result<AgentCompletionPlan, CoreError> {
    let completion_action_id = action_id::completion(
        request.task_id,
        request.compaction_count,
        request.message_count,
    );
    let hook_binding_ids = request.pre_llm_hook_binding_ids;
    if !hook_binding_ids.is_empty() {
        return Ok(AgentCompletionPlan::AwaitPreLlmHook {
            pending: Box::new(PendingLifecycleHook::pre_llm_call(
                completion_action_id,
                hook_binding_ids,
            )?),
        });
    }
    Ok(agent_completion_after_pre_llm_hook(completion_action_id))
}

pub(in crate::core::turn) fn agent_completion_after_pre_llm_hook(
    action_id: String,
) -> AgentCompletionPlan {
    AgentCompletionPlan::AwaitCompletion { action_id }
}
