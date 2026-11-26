use crate::core::action_id;
use crate::core::error::CoreError;
use crate::core::features::permissions::GrantKey;
use crate::core::features::programmatic_tool_calling::ProgrammaticName;
use crate::core::hooks::{
    HookCall, HookPoint, hook_action_id, invalid_pre_tool_call_arguments, post_tool_call_warning,
};
use crate::core::step_protocol::{Action, Observation};
use crate::core::tools::external::ExternalToolCall;
use crate::core::wire::tool::ToolResult;

/// An external tool call that Core has not committed yet: a direct call from
/// the model, or one call of a `run_typescript` program.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct PendingExternalCall {
    pub(crate) call: ExternalToolCall,
    pub(crate) stage: ExternalCallStage,
}

/// The hook stages always hold at least one binding ID: Core enters them only
/// for matching bindings, and checkpoint restore rejects an empty list.
#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum ExternalCallStage {
    AwaitingPreHook {
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
    },
    AwaitingApproval {
        approval_action_id: String,
        grant_key: GrantKey,
        name: ApprovalName,
    },
    Pending {
        /// Host time of the step that dispatched the tool Action. Checkpoints
        /// that did not record it restore without it.
        dispatched_at_unix_ms: Option<u64>,
    },
    AwaitingPostHook {
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
        duration_ms: u64,
    },
}

/// The name an approval shows to the user.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum ApprovalName {
    /// The model-visible name of the direct call.
    Direct,
    Programmatic(ProgrammaticName),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum HookSide {
    Pre,
    Post,
}

/// The permission decision for one external call.
#[allow(clippy::large_enum_variant)]
pub(crate) enum Admission {
    Run,
    Deny(ToolResult),
    Ask(GrantKey),
}

impl PendingExternalCall {
    pub(crate) fn awaiting_pre_hook(call: ExternalToolCall, hook_binding_ids: Vec<String>) -> Self {
        Self {
            stage: ExternalCallStage::AwaitingPreHook {
                hook_action_id: hook_action_id(&call.action_id, HookPoint::PreToolCall),
                hook_binding_ids,
            },
            call,
        }
    }

    pub(crate) fn pending(call: ExternalToolCall, dispatched_at_unix_ms: Option<u64>) -> Self {
        Self {
            call,
            stage: ExternalCallStage::Pending {
                dispatched_at_unix_ms,
            },
        }
    }

    pub(crate) fn awaiting_approval(
        call: ExternalToolCall,
        grant_key: GrantKey,
        name: ApprovalName,
    ) -> Self {
        Self {
            stage: ExternalCallStage::AwaitingApproval {
                approval_action_id: action_id::approval(&call.action_id),
                grant_key,
                name,
            },
            call,
        }
    }

    /// The Action that the current stage waits for.
    pub(crate) fn action(&self, turn_id: &str) -> Action {
        match &self.stage {
            ExternalCallStage::AwaitingPreHook {
                hook_action_id,
                hook_binding_ids,
            } => Action::Hook {
                effect_id: hook_action_id.clone(),
                turn_id: turn_id.to_string(),
                hook_binding_ids: hook_binding_ids.clone(),
                call: HookCall::PreToolCall {
                    tool_call: (&self.call).into(),
                },
            },
            ExternalCallStage::AwaitingApproval {
                approval_action_id,
                grant_key,
                name,
            } => Action::approval(
                approval_action_id.clone(),
                turn_id,
                grant_key,
                &self.call,
                match name {
                    ApprovalName::Direct => self
                        .call
                        .call
                        .direct_model_name()
                        .expect("direct approval call has a model-visible name"),
                    ApprovalName::Programmatic(name) => name.display_name(),
                },
            ),
            ExternalCallStage::Pending { .. } => Action::external_tool(&self.call, turn_id),
            ExternalCallStage::AwaitingPostHook {
                hook_action_id,
                hook_binding_ids,
                result,
                duration_ms,
            } => Action::Hook {
                effect_id: hook_action_id.clone(),
                turn_id: turn_id.to_string(),
                hook_binding_ids: hook_binding_ids.clone(),
                call: HookCall::PostToolCall {
                    tool_call: (&self.call).into(),
                    tool_result: result.clone(),
                    duration_ms: *duration_ms,
                },
            },
        }
    }

    /// The ID of the Action that the current stage waits for.
    pub(crate) fn action_id(&self) -> &str {
        match &self.stage {
            ExternalCallStage::AwaitingPreHook { hook_action_id, .. }
            | ExternalCallStage::AwaitingPostHook { hook_action_id, .. } => hook_action_id,
            ExternalCallStage::AwaitingApproval {
                approval_action_id, ..
            } => approval_action_id,
            ExternalCallStage::Pending { .. } => &self.call.action_id,
        }
    }

    pub(crate) fn awaited_hook(&self, action_id: &str) -> Option<HookSide> {
        match self.stage {
            _ if self.action_id() != action_id => None,
            ExternalCallStage::AwaitingPreHook { .. } => Some(HookSide::Pre),
            ExternalCallStage::AwaitingPostHook { .. } => Some(HookSide::Post),
            _ => None,
        }
    }

    /// The failed result and the warning for arguments from the awaited
    /// pre-tool-call hooks that do not satisfy the tool's input schema.
    pub(crate) fn invalid_pre_hook_arguments(
        &self,
        detail: &str,
        turn_id: &str,
    ) -> Result<(ToolResult, Observation), CoreError> {
        let ExternalCallStage::AwaitingPreHook {
            hook_binding_ids, ..
        } = &self.stage
        else {
            return Err(CoreError::invariant(
                "hook arguments arrived for a call that awaits no pre-tool hook",
            ));
        };
        Ok(invalid_pre_tool_call_arguments(
            turn_id,
            &self.call,
            hook_binding_ids,
            detail,
        ))
    }

    /// The warning for the result that the awaited post-tool-call hook
    /// returned, if that hook left the result's two views disagreeing.
    pub(crate) fn post_hook_warning(
        &self,
        hooked: &ToolResult,
        turn_id: &str,
    ) -> Option<Observation> {
        let ExternalCallStage::AwaitingPostHook {
            hook_binding_ids,
            result,
            ..
        } = &self.stage
        else {
            return None;
        };
        post_tool_call_warning(turn_id, &self.call, hook_binding_ids, result, hooked)
    }

    pub(crate) fn awaits_tool(&self, action_id: &str) -> bool {
        matches!(self.stage, ExternalCallStage::Pending { .. }) && self.action_id() == action_id
    }

    pub(crate) fn awaits_approval(&self, action_id: &str) -> bool {
        matches!(self.stage, ExternalCallStage::AwaitingApproval { .. })
            && self.action_id() == action_id
    }

    pub(crate) fn send(
        &mut self,
        call: ExternalToolCall,
        now_unix_ms: u64,
        turn_id: &str,
    ) -> Action {
        *self = Self::pending(call, Some(now_unix_ms));
        self.action(turn_id)
    }

    pub(crate) fn await_approval(
        &mut self,
        call: ExternalToolCall,
        grant_key: GrantKey,
        name: ApprovalName,
        turn_id: &str,
    ) -> Action {
        *self = Self::awaiting_approval(call, grant_key, name);
        self.action(turn_id)
    }

    pub(crate) fn await_post_hook(
        &mut self,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
        now_unix_ms: u64,
        turn_id: &str,
    ) -> Action {
        // A call restored without its dispatch time reports no duration.
        let duration_ms = match self.stage {
            ExternalCallStage::Pending {
                dispatched_at_unix_ms: Some(dispatched_at_unix_ms),
            } => now_unix_ms.saturating_sub(dispatched_at_unix_ms),
            _ => 0,
        };
        self.stage = ExternalCallStage::AwaitingPostHook {
            hook_action_id: hook_action_id(&self.call.action_id, HookPoint::PostToolCall),
            hook_binding_ids,
            result,
            duration_ms,
        };
        self.action(turn_id)
    }
}
