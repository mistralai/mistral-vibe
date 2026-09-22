use crate::core::error::CoreError;
use crate::core::hooks::{
    CompletionHookOutput, HookCall, HookPoint, HookResult, PreAgentTurnOutput, PreLlmCallOutput,
    hook_action_id,
};
use crate::core::require_action_id;
use crate::core::step_protocol::Action;
use crate::core::turn::TurnOutcome;
use crate::core::wire::completion::CompletionCandidate;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::content::validate_non_empty_content;
use crate::core::wire::message::{Message, StoredMessage};
use crate::core::wire::tool::ProtocolError;

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum SuspendedTurn {
    Idle,
    Terminal {
        turn_id: String,
        outcome: TurnOutcome,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum PendingLifecycleHook {
    PreAgentTurn {
        action_id: String,
        hook_binding_ids: Vec<String>,
        turn_id: String,
        user_content: Vec<ContentBlock>,
        suspended: SuspendedTurn,
    },
    PreLlmCall {
        action_id: String,
        hook_binding_ids: Vec<String>,
        completion_action_id: String,
    },
    PostLlmCall {
        action_id: String,
        hook_binding_ids: Vec<String>,
        completion_action_id: String,
        candidate: CompletionCandidate,
    },
    PostAgentTurn {
        action_id: String,
        hook_binding_ids: Vec<String>,
        completion_action_id: String,
        candidate: CompletionCandidate,
    },
}

impl PendingLifecycleHook {
    pub(in crate::core::turn) fn pre_agent_turn(
        turn_id: String,
        user_content: Vec<ContentBlock>,
        suspended: SuspendedTurn,
        hook_binding_ids: Vec<String>,
    ) -> Result<Self, CoreError> {
        if hook_binding_ids.is_empty() {
            return Err(CoreError::invariant(
                "cannot start PreAgentTurn without a matching hook binding",
            ));
        }
        Ok(Self::PreAgentTurn {
            action_id: hook_action_id(&turn_id, HookPoint::PreAgentTurn),
            hook_binding_ids,
            turn_id,
            user_content,
            suspended,
        })
    }

    pub(in crate::core::turn) fn pre_llm_call(
        completion_action_id: String,
        hook_binding_ids: Vec<String>,
    ) -> Result<Self, CoreError> {
        if hook_binding_ids.is_empty() {
            return Err(CoreError::invariant(
                "cannot start PreLlmCall without a matching hook binding",
            ));
        }
        Ok(Self::PreLlmCall {
            action_id: hook_action_id(&completion_action_id, HookPoint::PreLlmCall),
            hook_binding_ids,
            completion_action_id,
        })
    }

    pub(in crate::core::turn) fn completion_candidate(
        completion_action_id: String,
        candidate: CompletionCandidate,
        hook_binding_ids: Vec<String>,
        point: HookPoint,
    ) -> Result<Self, CoreError> {
        if hook_binding_ids.is_empty() {
            return Err(CoreError::invariant(format!(
                "cannot start {point:?} without a matching hook binding"
            )));
        }
        let action_id = hook_action_id(&completion_action_id, point);
        match point {
            HookPoint::PostLlmCall => Ok(Self::PostLlmCall {
                action_id,
                hook_binding_ids,
                completion_action_id,
                candidate,
            }),
            HookPoint::PostAgentTurn => Ok(Self::PostAgentTurn {
                action_id,
                hook_binding_ids,
                completion_action_id,
                candidate,
            }),
            _ => Err(CoreError::invalid_command(format!(
                "invalid completion hook point {point:?}"
            ))),
        }
    }

    pub(crate) fn action_id(&self) -> &str {
        match self {
            Self::PreAgentTurn { action_id, .. }
            | Self::PreLlmCall { action_id, .. }
            | Self::PostLlmCall { action_id, .. }
            | Self::PostAgentTurn { action_id, .. } => action_id,
        }
    }

    pub(crate) fn hook_binding_ids(&self) -> &[String] {
        match self {
            Self::PreAgentTurn {
                hook_binding_ids, ..
            }
            | Self::PreLlmCall {
                hook_binding_ids, ..
            }
            | Self::PostLlmCall {
                hook_binding_ids, ..
            }
            | Self::PostAgentTurn {
                hook_binding_ids, ..
            } => hook_binding_ids,
        }
    }

    fn point(&self) -> HookPoint {
        match self {
            Self::PreAgentTurn { .. } => HookPoint::PreAgentTurn,
            Self::PreLlmCall { .. } => HookPoint::PreLlmCall,
            Self::PostLlmCall { .. } => HookPoint::PostLlmCall,
            Self::PostAgentTurn { .. } => HookPoint::PostAgentTurn,
        }
    }

    pub(crate) fn action(&self, active_turn_id: &str) -> Action {
        let (effect_id, turn_id, hook_binding_ids, call) = match self {
            Self::PreAgentTurn {
                action_id,
                hook_binding_ids,
                turn_id,
                user_content,
                ..
            } => (
                action_id.clone(),
                turn_id.clone(),
                hook_binding_ids.clone(),
                HookCall::PreAgentTurn {
                    user_content: user_content.clone(),
                },
            ),
            Self::PreLlmCall {
                action_id,
                hook_binding_ids,
                ..
            } => (
                action_id.clone(),
                active_turn_id.to_string(),
                hook_binding_ids.clone(),
                HookCall::PreLlmCall,
            ),
            Self::PostLlmCall {
                action_id,
                hook_binding_ids,
                candidate,
                ..
            } => (
                action_id.clone(),
                active_turn_id.to_string(),
                hook_binding_ids.clone(),
                HookCall::PostLlmCall {
                    candidate: candidate.clone(),
                },
            ),
            Self::PostAgentTurn {
                action_id,
                hook_binding_ids,
                candidate,
                ..
            } => (
                action_id.clone(),
                active_turn_id.to_string(),
                hook_binding_ids.clone(),
                HookCall::PostAgentTurn {
                    candidate: candidate.clone(),
                },
            ),
        };
        Action::Hook {
            effect_id,
            turn_id,
            hook_binding_ids,
            call,
        }
    }

    pub(crate) fn interrupted_completion_action_id(&self) -> Option<&str> {
        match self {
            Self::PostLlmCall {
                completion_action_id,
                ..
            }
            | Self::PostAgentTurn {
                completion_action_id,
                ..
            } => Some(completion_action_id),
            Self::PreAgentTurn { .. } | Self::PreLlmCall { .. } => None,
        }
    }
}

pub(in crate::core::turn) enum HookResolution {
    RestoreTurn {
        suspended: SuspendedTurn,
    },
    CommitPreAgentTurn {
        suspended: SuspendedTurn,
        turn_id: String,
        message: StoredMessage,
    },
    ResolveCompletion(CompletionHookResolution),
}

pub(in crate::core::turn) enum CompletionHookResolution {
    PreLlm {
        completion_action_id: String,
        output: PreLlmCallOutput,
    },
    Candidate {
        stage: CompletionHookStage,
        completion_action_id: String,
        candidate: CompletionCandidate,
        output: CompletionHookOutput,
    },
    Failed {
        completion_action_id: String,
        error: ProtocolError,
    },
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(in crate::core::turn) enum CompletionHookStage {
    PostLlm,
    PostAgent,
}

pub(in crate::core::turn) fn resolve_lifecycle_hook(
    pending: PendingLifecycleHook,
    action_id: String,
    result: HookResult,
) -> Result<HookResolution, CoreError> {
    require_action_id(pending.action_id(), &action_id)?;
    if pending.point() != result.point() {
        return Err(CoreError::invalid_command(format!(
            "hook result {:?} does not match pending hook {:?}",
            result.point(),
            pending.point()
        )));
    }
    match (pending, result) {
        (
            PendingLifecycleHook::PreAgentTurn {
                turn_id, suspended, ..
            },
            HookResult::PreAgentTurn(output),
        ) => match output {
            PreAgentTurnOutput::Continue { user_content } => {
                validate_non_empty_content(&user_content, "pre-agent hook user content")?;
                Ok(HookResolution::CommitPreAgentTurn {
                    suspended,
                    turn_id,
                    message: StoredMessage::visible(Message::user(user_content)),
                })
            }
            PreAgentTurnOutput::Skip { reason } => {
                validate_non_empty_content(&reason, "pre-agent hook skip reason")?;
                Ok(HookResolution::RestoreTurn { suspended })
            }
        },
        (
            PendingLifecycleHook::PreLlmCall {
                completion_action_id,
                ..
            },
            HookResult::PreLlmCall(output),
        ) => Ok(HookResolution::ResolveCompletion(
            CompletionHookResolution::PreLlm {
                completion_action_id,
                output,
            },
        )),
        (
            PendingLifecycleHook::PostLlmCall {
                completion_action_id,
                candidate,
                ..
            },
            HookResult::PostLlmCall(output),
        ) => Ok(HookResolution::ResolveCompletion(
            CompletionHookResolution::Candidate {
                stage: CompletionHookStage::PostLlm,
                completion_action_id,
                candidate,
                output,
            },
        )),
        (
            PendingLifecycleHook::PostAgentTurn {
                completion_action_id,
                candidate,
                ..
            },
            HookResult::PostAgentTurn(output),
        ) => Ok(HookResolution::ResolveCompletion(
            CompletionHookResolution::Candidate {
                stage: CompletionHookStage::PostAgent,
                completion_action_id,
                candidate,
                output,
            },
        )),
        _ => unreachable!("hook point was validated above"),
    }
}

pub(in crate::core::turn) fn resolve_failed_lifecycle_hook(
    pending: PendingLifecycleHook,
    action_id: String,
    error: ProtocolError,
) -> Result<HookResolution, CoreError> {
    require_action_id(pending.action_id(), &action_id)?;
    match pending {
        PendingLifecycleHook::PreAgentTurn { suspended, .. } => {
            Ok(HookResolution::RestoreTurn { suspended })
        }
        PendingLifecycleHook::PreLlmCall {
            completion_action_id,
            ..
        }
        | PendingLifecycleHook::PostLlmCall {
            completion_action_id,
            ..
        }
        | PendingLifecycleHook::PostAgentTurn {
            completion_action_id,
            ..
        } => Ok(HookResolution::ResolveCompletion(
            CompletionHookResolution::Failed {
                completion_action_id,
                error,
            },
        )),
    }
}
