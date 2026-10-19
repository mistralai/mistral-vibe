use crate::core::action_id;
use crate::core::error::CoreError;

use super::{
    CompactionBudget, CompactionBudgets, CompactionProjection, CompactionProjectionFit,
    CompactionTrigger, SummaryAcceptance, SummaryRejection, accept_summary,
    minimum_replacement_fits,
};
use crate::core::model_context::ModelContext;
use crate::core::step_protocol::{Observation, Outcome, ToolDefinition};
use crate::core::wire::completion::CompletionCandidate;
use crate::core::wire::message::StoredMessage;
use crate::core::wire::tool::ProtocolError;

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct PendingCompaction {
    compaction_id: String,
    action_id: String,
    continuation: CompactionContinuation,
    projection: CompactionProjection,
}

#[derive(Clone, Debug, PartialEq)]
enum CompactionContinuation {
    InTurn { turn_id: String, iterations: u32 },
    BetweenTurns { trigger: CompactionTrigger },
}

impl PendingCompaction {
    pub(crate) fn in_turn(
        compaction_id: String,
        turn_id: String,
        iterations: u32,
        projection: CompactionProjection,
    ) -> Self {
        Self::from_parts(
            compaction_id,
            CompactionContinuation::InTurn {
                turn_id,
                iterations,
            },
            projection,
        )
    }

    pub(crate) fn between_turns(
        compaction_id: String,
        trigger: CompactionTrigger,
        projection: CompactionProjection,
    ) -> Self {
        Self::from_parts(
            compaction_id,
            CompactionContinuation::BetweenTurns { trigger },
            projection,
        )
    }

    fn from_parts(
        compaction_id: String,
        continuation: CompactionContinuation,
        projection: CompactionProjection,
    ) -> Self {
        let action_id = compaction_action_id(&compaction_id, projection.attempt());
        Self {
            compaction_id,
            action_id,
            continuation,
            projection,
        }
    }

    pub(crate) fn compaction_id(&self) -> &str {
        &self.compaction_id
    }

    pub(crate) fn action_id(&self) -> &str {
        &self.action_id
    }

    pub(crate) fn attempt(&self) -> u32 {
        self.projection.attempt() + 1
    }

    pub(crate) fn trigger(&self) -> CompactionTrigger {
        match &self.continuation {
            CompactionContinuation::InTurn { .. } => CompactionTrigger::Automatic,
            CompactionContinuation::BetweenTurns { trigger } => *trigger,
        }
    }

    pub(crate) fn in_turn_identity(&self) -> Option<(&str, u32)> {
        match &self.continuation {
            CompactionContinuation::InTurn {
                turn_id,
                iterations,
            } => Some((turn_id, *iterations)),
            CompactionContinuation::BetweenTurns { .. } => None,
        }
    }

    pub(crate) fn between_turns_trigger(&self) -> Option<CompactionTrigger> {
        match &self.continuation {
            CompactionContinuation::BetweenTurns { trigger } => Some(*trigger),
            CompactionContinuation::InTurn { .. } => None,
        }
    }

    pub(crate) fn projection(&self) -> &CompactionProjection {
        &self.projection
    }

    fn observed_turn_id(&self) -> Option<String> {
        self.in_turn_identity()
            .map(|(turn_id, _)| turn_id.to_string())
    }

    fn into_continuation(self) -> CompactionContinuation {
        self.continuation
    }
}

fn compaction_action_id(compaction_id: &str, projection_attempt: u32) -> String {
    if projection_attempt == 0 {
        compaction_id.to_string()
    } else {
        action_id::compaction_attempt(compaction_id, projection_attempt + 1)
    }
}

impl CompactionContinuation {
    fn into_resolution(self, outcome: Outcome) -> CompactionResolution {
        match self {
            Self::InTurn { .. } => CompactionResolution::ContinueTurn { outcome },
            Self::BetweenTurns { .. } => CompactionResolution::ReturnIdle { outcome },
        }
    }
}

pub(crate) enum CompactionResolution {
    ContinueTurn {
        outcome: Outcome,
    },
    ReturnIdle {
        outcome: Outcome,
    },
    FailTurn {
        outcome: Outcome,
        turn_id: String,
        error: ProtocolError,
    },
}

pub(crate) enum CompactionAttemptResolution {
    Retry(PendingCompaction),
    Resolve(Box<CompactionResolution>),
}

pub(crate) enum CompactionStartResolution {
    Pending(PendingCompaction),
    Failed(Box<CompactionResolution>),
}

pub(crate) fn finish_compaction(
    context: &mut ModelContext,
    last_reported_context_tokens: &mut Option<u64>,
    pending: PendingCompaction,
    candidate: CompletionCandidate,
    tools: &[ToolDefinition],
    replacement_budget: CompactionBudget,
) -> Result<CompactionAttemptResolution, CoreError> {
    let mut outcome = Outcome::quiet();
    let acceptance = accept_summary(
        context.messages(),
        pending.projection().messages(),
        &candidate,
        tools,
        replacement_budget,
    )?;
    let (messages, summary) = match acceptance {
        SummaryAcceptance::Accepted { messages, summary } => (messages, summary),
        SummaryAcceptance::Rejected(rejection) => {
            return retry_or_fail_invalid_summary(context, pending, rejection);
        }
        SummaryAcceptance::ReplacementTooLarge => {
            return Ok(CompactionAttemptResolution::Resolve(Box::new(
                fail_compaction(pending, replacement_too_large_error()),
            )));
        }
    };
    context.replace_messages(messages);
    // The previous provider-reported size describes the now-replaced context.
    // Clear it only once the live history actually changes, so a failed or
    // interrupted attempt keeps the over-budget signal for the next turn.
    *last_reported_context_tokens = None;
    outcome.observe(Observation::ContextCompacted {
        turn_id: pending.observed_turn_id(),
        action_id: pending.action_id().to_string(),
        compaction_id: pending.compaction_id().to_string(),
        attempt: pending.attempt(),
        trigger: pending.trigger(),
        summary,
        usage: candidate.usage,
    });
    Ok(CompactionAttemptResolution::Resolve(Box::new(
        pending.into_continuation().into_resolution(outcome),
    )))
}

fn retry_or_fail_invalid_summary(
    context: &mut ModelContext,
    pending: PendingCompaction,
    rejection: SummaryRejection,
) -> Result<CompactionAttemptResolution, CoreError> {
    if let Some(projection) = pending.projection.retry_after_invalid_summary()? {
        context.mark_projection_replacement();
        return Ok(CompactionAttemptResolution::Retry(
            PendingCompaction::from_parts(pending.compaction_id, pending.continuation, projection),
        ));
    }
    Ok(CompactionAttemptResolution::Resolve(Box::new(
        fail_compaction(pending, invalid_summary_error(rejection)),
    )))
}

fn invalid_summary_error(rejection: SummaryRejection) -> ProtocolError {
    let message = match rejection {
        SummaryRejection::EmptyResponse => "compaction response was empty",
        SummaryRejection::MalformedDelimiters => {
            "compaction response contained malformed <summary> delimiters"
        }
        SummaryRejection::ToolCalls => "compaction response emitted tool calls",
    };
    ProtocolError {
        code: "invalid_compaction_summary".to_string(),
        message: message.to_string(),
        retryable: false,
        details: serde_json::Value::Null,
    }
}

fn replacement_too_large_error() -> ProtocolError {
    ProtocolError {
        code: "compaction_replacement_too_large".to_string(),
        message: "compaction summary and required continuation context cannot fit the configured token threshold".to_string(),
        retryable: false,
        details: serde_json::Value::Null,
    }
}

fn request_too_large_error() -> ProtocolError {
    ProtocolError {
        code: "compaction_request_too_large".to_string(),
        message: "required compaction context cannot fit the configured token threshold"
            .to_string(),
        retryable: false,
        details: serde_json::Value::Null,
    }
}

pub(crate) fn fail_compaction(
    pending: PendingCompaction,
    error: ProtocolError,
) -> CompactionResolution {
    fail_compaction_attempt(
        pending.compaction_id().to_string(),
        pending.action_id().to_string(),
        pending.attempt(),
        pending.into_continuation(),
        error,
    )
}

fn fail_compaction_attempt(
    compaction_id: String,
    action_id: String,
    attempt: u32,
    continuation: CompactionContinuation,
    error: ProtocolError,
) -> CompactionResolution {
    let mut outcome = Outcome::quiet();
    outcome.observe(Observation::ContextCompactionFailed {
        turn_id: match &continuation {
            CompactionContinuation::InTurn { turn_id, .. } => Some(turn_id.clone()),
            CompactionContinuation::BetweenTurns { .. } => None,
        },
        action_id,
        compaction_id,
        attempt,
        trigger: match &continuation {
            CompactionContinuation::InTurn { .. } => CompactionTrigger::Automatic,
            CompactionContinuation::BetweenTurns { trigger } => *trigger,
        },
        error: error.clone(),
    });
    match continuation {
        CompactionContinuation::InTurn { turn_id, .. } => CompactionResolution::FailTurn {
            outcome,
            turn_id,
            error,
        },
        CompactionContinuation::BetweenTurns { .. } => CompactionResolution::ReturnIdle { outcome },
    }
}

pub(crate) enum CompactionRequest<'a> {
    Automatic { turn_id: &'a str, iterations: u32 },
    Manual { extra_instructions: &'a str },
}

impl<'a> CompactionRequest<'a> {
    pub(crate) fn automatic(turn_id: &'a str, iterations: u32) -> Self {
        Self::Automatic {
            turn_id,
            iterations,
        }
    }

    pub(crate) fn manual(extra_instructions: &'a str) -> Self {
        Self::Manual { extra_instructions }
    }
}

pub(crate) fn start_compaction(
    context: &mut ModelContext,
    compaction_count: &mut u64,
    generated_system: StoredMessage,
    task_id: &str,
    request: CompactionRequest<'_>,
    tools: &[ToolDefinition],
    budgets: CompactionBudgets,
) -> Result<CompactionStartResolution, CoreError> {
    let (continuation, extra_instructions) = match request {
        CompactionRequest::Automatic {
            turn_id,
            iterations,
        } => (
            CompactionContinuation::InTurn {
                turn_id: turn_id.to_string(),
                iterations,
            },
            "",
        ),
        CompactionRequest::Manual { extra_instructions } => (
            CompactionContinuation::BetweenTurns {
                trigger: CompactionTrigger::Manual,
            },
            extra_instructions,
        ),
    };
    let compaction_id = next_action_id(compaction_count, task_id)?;
    let replacement_system = generated_system.clone();
    let projection = CompactionProjection::initial(
        context.messages(),
        generated_system,
        extra_instructions,
        tools,
        budgets.request,
    )?;
    let CompactionProjectionFit::Fitted(projection) = projection else {
        return Ok(CompactionStartResolution::Failed(Box::new(
            fail_compaction_attempt(
                compaction_id.clone(),
                compaction_id,
                1,
                continuation,
                request_too_large_error(),
            ),
        )));
    };
    if !minimum_replacement_fits(
        context.messages(),
        replacement_system,
        tools,
        budgets.replacement,
    )? {
        return Ok(CompactionStartResolution::Failed(Box::new(
            fail_compaction_attempt(
                compaction_id.clone(),
                compaction_id,
                1,
                continuation,
                replacement_too_large_error(),
            ),
        )));
    }
    context.mark_projection_replacement();
    Ok(CompactionStartResolution::Pending(
        PendingCompaction::from_parts(compaction_id, continuation, projection),
    ))
}

fn next_action_id(compaction_count: &mut u64, task_id: &str) -> Result<String, CoreError> {
    *compaction_count = compaction_count
        .checked_add(1)
        .ok_or_else(|| CoreError::invariant("compaction action sequence exhausted"))?;
    Ok(action_id::compaction(task_id, *compaction_count))
}
