//! The estimate correction: how Core learns that its token estimate is short.
//!
//! Core estimates a request's size from its serialized bytes (about four bytes
//! per token, images at the model's image cost). A tokenizer that packs more
//! tokens per byte, or content such as base64, makes the real size larger than
//! the estimate. Core keeps one correction factor `r` per session and
//! multiplies every estimate by it:
//!
//! - After each accepted agent reply, `r` is re-measured as the reported input
//!   divided by the estimate of the request that produced it, never below 1.
//! - When the provider refuses a request as too long (a `context_overflow`
//!   failure), the refused request was at least the context window in size.
//!   `r` grows to `max(r × REFUSAL_GROWTH, window ÷ refused estimate)`, and the
//!   check that sent the request runs again. The growth floor makes repeated
//!   refusals converge even when the window itself is a guess.
//! - When the model facts or the effective image delivery change, `r` goes
//!   back to 1, and the last reported size and any exact count are cleared:
//!   they measured requests under the old facts.
//!
//! The provider adapter in each Runtime recognises its provider's refusal and
//! reports it under [`CONTEXT_OVERFLOW`]. Core reads only that code: it never
//! matches provider messages.
//!
//! Estimate × `r` stands in for the request's real input token count, which
//! Core cannot count itself. [`ExactRequestTokens`] is the room for that
//! count: when it holds a count of the request the check is sizing, the check
//! uses it in place of estimate × `r`.

use crate::core::wire::tool::ProtocolError;

/// The completion failure code for a request the provider refused because its
/// input does not fit the model's context window.
pub(crate) const CONTEXT_OVERFLOW: &str = "context_overflow";

/// The least `r` grows by per refusal.
pub(crate) const REFUSAL_GROWTH: f64 = 4.0 / 3.0;

/// Whether `error`, the failure of a completion, is a refusal as too long.
pub(crate) fn is_context_overflow(error: &ProtocolError) -> bool {
    error.code == CONTEXT_OVERFLOW
}

/// An exact count of one agent request's input tokens, taken before it was
/// sent. It holds only for the request it counted, which it names by the
/// committed revisions of the context and the tool catalog (see
/// [`RequestRevision`]); any change to either makes it stale, and the check
/// falls back to the estimate.
// TODO: no Runtime fills this yet. A Runtime that can count a request's input
// tokens (a tokenizer, or a provider count endpoint) fills it by counting the
// agent request before it sends it. The likely shape is a `count_tokens`
// action Core asks for before each agent request, behind a model fact that is
// off by default; the count it returns goes through
// `request_size::record_exact_request_tokens`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ExactRequestTokens {
    /// The input tokens the request holds.
    pub tokens: u64,
    revision: Option<RequestRevision>,
}

impl ExactRequestTokens {
    /// The count `tokens` of the request at `revision`. A count taken with
    /// changes not yet committed has no revision and describes no request.
    pub(crate) fn new(tokens: u64, revision: Option<RequestRevision>) -> Self {
        Self { tokens, revision }
    }

    /// Whether this is the count of the request at `revision`.
    pub(crate) fn describes(self, revision: Option<RequestRevision>) -> bool {
        self.revision.is_some() && self.revision == revision
    }
}

/// Which agent request the context and tool catalog make: their committed
/// revisions. Each moves on any change to the messages, their projection or
/// the tools, whatever the size of the change.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct RequestRevision {
    pub context: u64,
    pub tool_catalog: u64,
}

/// The session's estimate correction factor `r`, always finite and at least 1.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct EstimateCorrection(f64);

impl Default for EstimateCorrection {
    fn default() -> Self {
        Self::NONE
    }
}

impl EstimateCorrection {
    /// No correction: estimates are taken as they are.
    pub(crate) const NONE: Self = Self(1.0);

    /// Restores a stored factor. A value below 1 or not finite is invalid.
    pub(crate) fn restore(factor: f64) -> Result<Self, String> {
        if !factor.is_finite() || factor < 1.0 {
            return Err(format!(
                "estimate correction must be a finite number of at least 1, got {factor}"
            ));
        }
        Ok(Self(factor))
    }

    pub(crate) fn factor(self) -> f64 {
        self.0
    }

    pub(crate) fn is_none(&self) -> bool {
        *self == Self::NONE
    }

    /// The factor measured from an accepted reply: the input the provider
    /// reported divided by Core's estimate of that request, never below 1.
    pub(crate) fn measured(reported_input_tokens: u64, estimated_input_tokens: u64) -> Self {
        if estimated_input_tokens == 0 {
            return Self::NONE;
        }
        let ratio = reported_input_tokens as f64 / estimated_input_tokens as f64;
        Self(ratio.max(1.0))
    }

    /// The factor after a refusal as too long of a request whose estimate was
    /// `refused_estimate`: the refused request was at least the window, and the
    /// factor grows by at least [`REFUSAL_GROWTH`].
    pub(crate) fn after_refusal(self, context_window: u64, refused_estimate: u64) -> Self {
        let grown = self.0 * REFUSAL_GROWTH;
        let measured = if refused_estimate > 0 {
            context_window as f64 / refused_estimate as f64
        } else {
            1.0
        };
        Self(grown.max(measured))
    }

    /// The largest estimate a request refitted after a refusal may have: the
    /// refused request's estimate shrunk by [`REFUSAL_GROWTH`]. The grown
    /// factor bounds the refit through the corrected budget too; when the
    /// factor jumps to window ÷ refused estimate, that bound is the tighter
    /// one, and this one still makes the request smaller when it does not.
    pub(crate) fn refit_limit(refused_estimate: u64) -> u64 {
        (refused_estimate as f64 / REFUSAL_GROWTH).floor() as u64
    }

    /// `estimate` multiplied by the factor, rounded up.
    pub(crate) fn apply(self, estimate: u64) -> u64 {
        if self.is_none() {
            return estimate;
        }
        let corrected = (estimate as f64 * self.0).ceil();
        if corrected >= u64::MAX as f64 {
            u64::MAX
        } else {
            corrected as u64
        }
    }

    /// The largest estimate whose corrected size stays at or below `limit`.
    pub(crate) fn uncorrected_limit(self, limit: u64) -> u64 {
        if self.is_none() {
            return limit;
        }
        let mut estimate = (limit as f64 / self.0).floor() as u64;
        while estimate > 0 && self.apply(estimate) > limit {
            estimate -= 1;
        }
        estimate
    }
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    use super::*;

    fn error(code: &str) -> ProtocolError {
        ProtocolError {
            code: code.to_string(),
            message: "Prompt 270000 > 262144 maximum context length".to_string(),
            retryable: false,
            details: Value::Null,
        }
    }

    #[test]
    fn a_refit_is_smaller_than_the_refused_request_by_the_growth_floor() {
        assert_eq!(EstimateCorrection::refit_limit(40_000), 30_000);
        assert_eq!(EstimateCorrection::refit_limit(4), 3);
        assert_eq!(EstimateCorrection::refit_limit(1), 0);
    }

    #[test]
    fn only_the_context_overflow_code_is_a_refusal_as_too_long() {
        assert!(is_context_overflow(&error(CONTEXT_OVERFLOW)));
        // Classifying the provider's rejection is the provider adapter's job.
        for code in [
            "model_stream_failed",
            "completion_provider",
            "context_window_exceeded",
        ] {
            assert!(!is_context_overflow(&error(code)), "{code}");
        }
    }

    #[test]
    fn a_refusal_from_one_grows_to_the_window_over_the_refused_estimate() {
        // 262k window, the refused request was 150k by estimate.
        let r = EstimateCorrection::NONE.after_refusal(262_144, 150_000);
        assert!((r.factor() - 262_144.0 / 150_000.0).abs() < 1e-9);
        // The corrected refused estimate is at least the window.
        assert!(r.apply(150_000) >= 262_144);
    }

    #[test]
    fn a_refusal_grows_by_at_least_four_thirds() {
        // The window says less than the growth floor.
        let r = EstimateCorrection::NONE.after_refusal(100_000, 99_000);
        assert!((r.factor() - REFUSAL_GROWTH).abs() < 1e-9);
        let r = EstimateCorrection::restore(1.5)
            .expect("valid factor")
            .after_refusal(100_000, 99_000);
        assert!((r.factor() - 2.0).abs() < 1e-9);
    }

    #[test]
    fn the_measured_factor_never_goes_below_one() {
        assert_eq!(
            EstimateCorrection::measured(90_000, 100_000),
            EstimateCorrection::NONE
        );
        assert_eq!(EstimateCorrection::measured(5, 0), EstimateCorrection::NONE);
        let r = EstimateCorrection::measured(133_000, 100_000);
        assert!((r.factor() - 1.33).abs() < 1e-9);
    }

    #[test]
    fn repeated_refusals_converge_with_a_guessed_window() {
        // The host guessed a 262k window; the real one is 131k. Core believes
        // the guess, so each refusal teaches it something, and the 4/3 floor
        // makes the refitted request shrink until the provider accepts it.
        let guessed_window = 262_144;
        let real_window = 131_072;
        let summary_budget = 238_000;
        // For this content the real size is the estimate × 1.2.
        let real_size = |estimate: u64| estimate * 6 / 5;
        let mut r = EstimateCorrection::NONE;
        let mut refusals = 0;
        let mut estimate = r.uncorrected_limit(summary_budget);
        while real_size(estimate) > real_window {
            r = r.after_refusal(guessed_window, estimate);
            refusals += 1;
            estimate = r.uncorrected_limit(summary_budget);
        }
        assert_eq!(refusals, 3);
        // The next accepted reply re-measures r from the real size.
        let measured = EstimateCorrection::measured(real_size(estimate), estimate);
        assert!((measured.factor() - 1.2).abs() < 1e-4);
    }

    #[test]
    fn apply_rounds_up_and_uncorrected_limit_inverts_it() {
        let r = EstimateCorrection::restore(4.0 / 3.0).expect("valid factor");
        assert_eq!(r.apply(3), 4);
        assert_eq!(r.apply(4), 6);
        assert_eq!(EstimateCorrection::NONE.apply(7), 7);
        for limit in [0, 1, 100, 202_000, 238_000] {
            let estimate = r.uncorrected_limit(limit);
            assert!(r.apply(estimate) <= limit);
            assert!(r.apply(estimate + 1) > limit);
        }
    }

    #[test]
    fn restore_rejects_a_factor_below_one_or_not_finite() {
        assert!(EstimateCorrection::restore(0.9).is_err());
        assert!(EstimateCorrection::restore(f64::NAN).is_err());
        assert!(EstimateCorrection::restore(f64::INFINITY).is_err());
        assert_eq!(
            EstimateCorrection::restore(1.0).expect("valid factor"),
            EstimateCorrection::NONE
        );
    }
}
