use crate::core::action_id;
use crate::core::error::CoreError;

use super::{
    CompactionBudget, CompactionBudgets, CompactionFallback, CompactionProjection,
    CompactionProjectionFit, CompactionTrigger, NewestToolExchange, SummaryAcceptance,
    SummaryRejection, accept_summary, minimum_replacement_fits,
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
    fallback: CompactionFallback,
) -> Result<CompactionAttemptResolution, CoreError> {
    let mut outcome = Outcome::quiet();
    let acceptance = accept_summary(
        context.messages(),
        pending.projection().messages(),
        &candidate,
        tools,
        replacement_budget,
        fallback,
    )?;
    let (messages, summary) = match acceptance {
        SummaryAcceptance::Accepted { messages, summary } => (messages, summary),
        SummaryAcceptance::Rejected(rejection) => {
            return fail_compaction_or_fall_back(
                context,
                pending,
                fallback,
                replacement_budget.token_threshold,
                invalid_summary_error(rejection),
            );
        }
        SummaryAcceptance::ReplacementTooLarge => {
            return Ok(CompactionAttemptResolution::Resolve(Box::new(
                fail_compaction(pending, replacement_too_large_error()),
            )));
        }
    };
    context.replace_with_compacted(messages);
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

/// Fails an attempt over to the next request `fallback` allows, or fails the
/// compaction when the failed attempt was the last one.
///
/// The standalone request differs from the in-context one in its messages,
/// tools and reasoning, so a provider that cached or deterministically
/// repeats the failed response does not see the same request again.
pub(crate) fn fail_compaction_or_fall_back(
    context: &mut ModelContext,
    pending: PendingCompaction,
    fallback: CompactionFallback,
    token_threshold: Option<u64>,
    error: ProtocolError,
) -> Result<CompactionAttemptResolution, CoreError> {
    if let Some(projection) = pending.projection.fall_back(fallback, token_threshold)? {
        context.mark_projection_replacement();
        return Ok(CompactionAttemptResolution::Retry(
            PendingCompaction::from_parts(pending.compaction_id, pending.continuation, projection),
        ));
    }
    Ok(CompactionAttemptResolution::Resolve(Box::new(
        fail_compaction(pending, error),
    )))
}

fn invalid_summary_error(rejection: SummaryRejection) -> ProtocolError {
    let message = match rejection {
        SummaryRejection::EmptyResponse => "compaction response was empty",
        SummaryRejection::MalformedDelimiters => {
            "compaction response contained malformed <summary> delimiters"
        }
        SummaryRejection::ToolCalls => "compaction response emitted tool calls",
        SummaryRejection::Truncated => {
            "compaction response reached the output token limit before a complete summary"
        }
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

fn tool_result_too_large_error(tool_names: &[String]) -> ProtocolError {
    ProtocolError {
        code: "compaction_tool_result_too_large".to_string(),
        message: format!(
            "the newest result of tool {} cannot fit the configured compaction token threshold; raise the threshold or make the tool return less",
            tool_names.join(", ")
        ),
        retryable: false,
        details: serde_json::json!({ "tool_names": tool_names }),
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
    let (continuation, extra_instructions, newest_tool_exchange) = match request {
        CompactionRequest::Automatic {
            turn_id,
            iterations,
        } => (
            CompactionContinuation::InTurn {
                turn_id: turn_id.to_string(),
                iterations,
            },
            "",
            NewestToolExchange::Keep,
        ),
        CompactionRequest::Manual { extra_instructions } => (
            CompactionContinuation::BetweenTurns {
                trigger: CompactionTrigger::Manual,
            },
            extra_instructions,
            NewestToolExchange::MayRemove,
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
        newest_tool_exchange,
    )?;
    let projection = match projection {
        CompactionProjectionFit::Fitted(projection) => projection,
        CompactionProjectionFit::TooLarge => {
            return Ok(CompactionStartResolution::Failed(Box::new(
                fail_compaction_attempt(
                    compaction_id.clone(),
                    compaction_id,
                    1,
                    continuation,
                    request_too_large_error(),
                ),
            )));
        }
        CompactionProjectionFit::ToolResultTooLarge { tool_names } => {
            return Ok(CompactionStartResolution::Failed(Box::new(
                fail_compaction_attempt(
                    compaction_id.clone(),
                    compaction_id,
                    1,
                    continuation,
                    tool_result_too_large_error(&tool_names),
                ),
            )));
        }
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
