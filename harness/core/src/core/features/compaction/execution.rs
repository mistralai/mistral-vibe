use crate::core::action_id;
use crate::core::error::CoreError;

use super::{
    CompactionInputs, CompactionProjection, CompactionProjectionFit, CompactionTrigger, Rebuilt,
    Reduction, SummaryAcceptance, SummaryCut, SummaryCutCause, SummaryRejection,
    accept_cut_off_summary, accept_summary, minimum_replacement_fits,
};

use crate::core::model_context::ModelContext;
use crate::core::step_protocol::{Observation, Outcome, WarningCode};
use crate::core::wire::completion::{CompletionCandidate, TokenUsage};
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
    /// The summary replaced the history.
    Compacted(Box<CompactionResolution>),
    Resolve(Box<CompactionResolution>),
}

pub(crate) enum CompactionStartResolution {
    Pending(PendingCompaction),
    Failed(Box<CompactionResolution>),
}

pub(crate) fn finish_compaction(
    context: &mut ModelContext,
    pending: PendingCompaction,
    candidate: CompletionCandidate,
    inputs: CompactionInputs<'_>,
) -> Result<CompactionAttemptResolution, CoreError> {
    let acceptance = accept_summary(
        context.messages(),
        pending.projection().messages(),
        &candidate,
        inputs.tools,
        inputs.budgets.replacement,
        inputs.prompts,
    )?;
    match acceptance {
        SummaryAcceptance::Accepted(rebuilt) => {
            Ok(compacted(context, pending, *rebuilt, candidate.usage))
        }
        SummaryAcceptance::Rejected(rejection) => {
            fail_compaction_or_fall_back(context, pending, inputs, invalid_summary_error(rejection))
        }
        SummaryAcceptance::CutOff { partial } => {
            retry_cut_off_compaction(context, pending, partial, candidate.usage, inputs)
        }
        SummaryAcceptance::ReplacementTooLarge => Ok(CompactionAttemptResolution::Resolve(
            Box::new(fail_compaction(pending, replacement_too_large_error())),
        )),
    }
}

/// Replaces the history with the rebuilt one, and reports what the rebuild
/// cut or reduced to fit.
fn compacted(
    context: &mut ModelContext,
    pending: PendingCompaction,
    rebuilt: Rebuilt,
    usage: Option<TokenUsage>,
) -> CompactionAttemptResolution {
    let mut outcome = Outcome::quiet();
    context.replace_with_compacted(rebuilt.messages);
    outcome.observe(Observation::ContextCompacted {
        turn_id: pending.observed_turn_id(),
        action_id: pending.action_id().to_string(),
        compaction_id: pending.compaction_id().to_string(),
        attempt: pending.attempt(),
        trigger: pending.trigger(),
        summary: rebuilt.summary,
        usage,
    });
    if let Some(cut) = rebuilt.summary_cut {
        outcome.observe(summary_cut_off_warning(pending.observed_turn_id(), &cut));
    }
    if let Some(reduction) = rebuilt.reduction {
        outcome.observe(inputs_reduced_warning(
            pending.observed_turn_id(),
            &reduction,
        ));
    }
    CompactionAttemptResolution::Compacted(Box::new(
        pending.into_continuation().into_resolution(outcome),
    ))
}

/// Moves a failed attempt on to the next phase, or ends the compaction when
/// none follows: with the cut-off text a cut-off retry saved, or failed.
///
/// The standalone request differs from the in-context one in its messages,
/// tools and reasoning, so a provider that cached or deterministically
/// repeats the failed response does not see the same request again.
pub(crate) fn fail_compaction_or_fall_back(
    context: &mut ModelContext,
    pending: PendingCompaction,
    inputs: CompactionInputs<'_>,
    error: ProtocolError,
) -> Result<CompactionAttemptResolution, CoreError> {
    let next =
        pending
            .projection
            .after_failure(inputs.tools, inputs.budgets.request, inputs.prompts)?;
    continue_or_end(context, pending, next, None, None, inputs, error)
}

/// Refits a summary request the provider refused as too long within the same
/// phase, under `refit_limit`. The caller has already grown the estimate
/// correction, so `inputs` carries the smaller budget. When even the minimal
/// request was refused, ends the compaction with the cut-off text a cut-off
/// retry saved, or fails it.
pub(crate) fn refit_refused_compaction(
    context: &mut ModelContext,
    pending: PendingCompaction,
    refit_limit: u64,
    inputs: CompactionInputs<'_>,
    error: ProtocolError,
) -> Result<CompactionAttemptResolution, CoreError> {
    let next = pending.projection.after_refusal(
        refit_limit,
        inputs.tools,
        inputs.budgets.request,
        inputs.prompts,
    )?;
    continue_or_end(
        context,
        pending,
        next,
        None,
        None,
        inputs,
        minimal_request_refused_error(error),
    )
}

/// Asks again after a cut-off summary while a retry is left, then keeps the
/// longest cut-off text (Truncate).
fn retry_cut_off_compaction(
    context: &mut ModelContext,
    pending: PendingCompaction,
    partial: Option<String>,
    usage: Option<TokenUsage>,
    inputs: CompactionInputs<'_>,
) -> Result<CompactionAttemptResolution, CoreError> {
    let next = pending.projection.after_cut_off(
        partial.clone(),
        inputs.tools,
        inputs.budgets.request,
        inputs.prompts,
    )?;
    continue_or_end(
        context,
        pending,
        next,
        partial,
        usage,
        inputs,
        cut_off_summary_error(),
    )
}

/// Sends `next` when there is one. Otherwise keeps the longest cut-off text,
/// the saved one or this attempt's `partial`, in place of a summary
/// (Truncate). Fails the compaction with `error` when there is none, or when
/// the rebuild with it does not fit.
fn continue_or_end(
    context: &mut ModelContext,
    pending: PendingCompaction,
    next: Option<CompactionProjection>,
    partial: Option<String>,
    usage: Option<TokenUsage>,
    inputs: CompactionInputs<'_>,
    error: ProtocolError,
) -> Result<CompactionAttemptResolution, CoreError> {
    if let Some(projection) = next {
        context.mark_projection_replacement();
        return Ok(CompactionAttemptResolution::Retry(
            PendingCompaction::from_parts(pending.compaction_id, pending.continuation, projection),
        ));
    }
    let Some(partial) = pending.projection.kept_partial(partial) else {
        return Ok(CompactionAttemptResolution::Resolve(Box::new(
            fail_compaction(pending, error),
        )));
    };
    let acceptance = accept_cut_off_summary(
        context.messages(),
        pending.projection().messages(),
        &partial,
        inputs.tools,
        inputs.budgets.replacement,
        inputs.prompts,
    )?;
    // When not even the fixed parts fit, the error that ended the summary
    // requests says more than the rebuild's: a refusal at the smallest size
    // has grown the estimate correction until nothing fits.
    let SummaryAcceptance::Accepted(rebuilt) = acceptance else {
        return Ok(CompactionAttemptResolution::Resolve(Box::new(
            fail_compaction(pending, error),
        )));
    };
    Ok(compacted(context, pending, *rebuilt, usage))
}

/// The summary was cut to fit, with the note after it: cut off at the
/// output limit after its retries, or too long for the rebuilt history.
fn summary_cut_off_warning(turn_id: Option<String>, cut: &SummaryCut) -> Observation {
    let message = match cut.cause {
        SummaryCutCause::CutOff => {
            "The compaction summary was cut off at the output limit; the part written is kept, \
             with a note that it was cut."
        }
        SummaryCutCause::TooLong => {
            "The compaction summary was too long for the rebuilt history and was cut, with a \
             note that it was cut."
        }
    };
    Observation::Warning {
        turn_id,
        code: WarningCode::CompactionSummaryCutOff,
        message: message.to_string(),
        details: serde_json::json!({
            "cause": cut.cause.as_str(),
            "summary_bytes": cut.summary_bytes,
            "kept_bytes": cut.kept_bytes,
        }),
    }
}

/// Items after the summary did not fit whole and come back reduced.
fn inputs_reduced_warning(turn_id: Option<String>, reduction: &Reduction) -> Observation {
    Observation::Warning {
        turn_id,
        code: WarningCode::CompactionInputsReduced,
        message: "The history rebuilt after compaction did not fit the context and was \
                  reduced."
            .to_string(),
        details: serde_json::json!({
            "reduced_messages": reduction.reduced_messages,
            "view": reduction.view,
        }),
    }
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

fn cut_off_summary_error() -> ProtocolError {
    ProtocolError {
        code: "invalid_compaction_summary".to_string(),
        message: "compaction response reached the output token limit before a complete summary"
            .to_string(),
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

/// The provider refused even the smallest summary request as too long. The
/// error keeps the provider's code and is retryable: the next request is
/// counted at the grown correction.
fn minimal_request_refused_error(error: ProtocolError) -> ProtocolError {
    ProtocolError {
        code: error.code,
        message: "The provider refused the compaction request as too long even at its smallest \
                  size. Check the model's context window."
            .to_string(),
        retryable: true,
        details: serde_json::json!({
            "reason": "minimal_summary_request",
            "provider_message": error.message,
            "provider_details": error.details,
        }),
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
    inputs: CompactionInputs<'_>,
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
    let projection = match plan_compaction(
        context.messages(),
        generated_system,
        extra_instructions,
        inputs,
    )? {
        Ok(projection) => projection,
        Err(unfit) => {
            return Ok(CompactionStartResolution::Failed(Box::new(
                fail_compaction_attempt(
                    compaction_id.clone(),
                    compaction_id,
                    1,
                    continuation,
                    unfit.error(),
                ),
            )));
        }
    };
    context.mark_projection_replacement();
    Ok(CompactionStartResolution::Pending(
        PendingCompaction::from_parts(compaction_id, continuation, projection),
    ))
}

/// Why a compaction cannot start under its budgets.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum CompactionTooLarge {
    /// The compaction request cannot fit, even with the oldest history removed.
    Request,
    /// The summary and the context a continuation needs cannot fit.
    Replacement,
}

impl CompactionTooLarge {
    pub(crate) fn error(&self) -> ProtocolError {
        match self {
            Self::Request => request_too_large_error(),
            Self::Replacement => replacement_too_large_error(),
        }
    }
}

/// Fits a compaction request over `messages` and checks that its replacement
/// fits too. Starting a compaction and checking a new model's facts share it.
pub(crate) fn plan_compaction(
    messages: &[StoredMessage],
    generated_system: StoredMessage,
    extra_instructions: &str,
    inputs: CompactionInputs<'_>,
) -> Result<Result<CompactionProjection, CompactionTooLarge>, CoreError> {
    let replacement_system = generated_system.clone();
    let projection = match CompactionProjection::initial(
        messages,
        generated_system,
        extra_instructions,
        inputs.tools,
        inputs.budgets.request,
        inputs.prompts,
    )? {
        CompactionProjectionFit::Fitted(projection) => projection,
        CompactionProjectionFit::TooLarge => return Ok(Err(CompactionTooLarge::Request)),
    };
    if !minimum_replacement_fits(
        messages,
        replacement_system,
        inputs.tools,
        inputs.budgets.replacement,
        inputs.prompts,
    )? {
        return Ok(Err(CompactionTooLarge::Replacement));
    }
    Ok(Ok(projection))
}

fn next_action_id(compaction_count: &mut u64, task_id: &str) -> Result<String, CoreError> {
    *compaction_count = compaction_count
        .checked_add(1)
        .ok_or_else(|| CoreError::invariant("compaction action sequence exhausted"))?;
    Ok(action_id::compaction(task_id, *compaction_count))
}
