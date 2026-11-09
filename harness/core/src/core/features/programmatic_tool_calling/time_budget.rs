//! The program time budget.
//!
//! `settings.tools.programmatic.max_elapsed_ms` bounds how long one program
//! runs, so that steering and agent messages reach the model within about that
//! time. The budget counts wall-clock time from the program's start, except
//! while one of its operations waits for an approval or a hook: a steer cannot
//! reach the model during that wait anyway. Tool execution, sleeps, and
//! process output waits count.
//!
//! When the budget is used up, the program stops before its next tool call or
//! step with a `ProgramBudgetExceeded` failure. A running tool call is never cut
//! short, but Core fits each wait to the remaining budget before it starts: a
//! shortened sleep rejects with a catchable `ToolError` with code
//! `ProgramBudgetExceeded`, and
//! a process output read only waits for the remaining time.

use serde_json::{Value, json};

use crate::core::tools::external::{ExternalTool, ExternalToolCall, RuntimeBuiltinToolName};
use crate::core::wire::content::text_content;
use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolResult};

pub(super) const BUDGET_EXCEEDED: &str = "ProgramBudgetExceeded";

/// Budget time one program has used.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct ProgramClock {
    /// When the program started. Programs restored from checkpoints written
    /// before time budgets start on their next round.
    pub(crate) started_at_unix_ms: Option<u64>,
    /// Time of finished pauses: periods in which an operation waited for an
    /// approval or a hook.
    pub(crate) paused_ms: u64,
    /// When the current pause started.
    pub(crate) paused_since_unix_ms: Option<u64>,
}

enum WaitKind {
    Sleep { seconds: f64 },
    ProcessOutput { wait_ms: u64 },
}

impl ProgramClock {
    pub(super) fn started_at(now_unix_ms: u64) -> Self {
        Self {
            started_at_unix_ms: Some(now_unix_ms),
            ..Self::default()
        }
    }

    pub(super) fn used_at(self, now_unix_ms: u64) -> u64 {
        let Some(started_at) = self.started_at_unix_ms else {
            return 0;
        };
        let current_pause = self
            .paused_since_unix_ms
            .map_or(0, |since| now_unix_ms.saturating_sub(since));
        now_unix_ms
            .saturating_sub(started_at)
            .saturating_sub(self.paused_ms.saturating_add(current_pause))
    }

    pub(super) fn remaining_at(self, now_unix_ms: u64, budget_ms: u64) -> u64 {
        budget_ms.saturating_sub(self.used_at(now_unix_ms))
    }

    /// Starts a new round at `now_unix_ms`, when no operation is outstanding:
    /// ends a pause and starts a program restored without a start time.
    pub(super) fn start_round(&mut self, now_unix_ms: u64) {
        self.started_at_unix_ms.get_or_insert(now_unix_ms);
        self.set_paused(false, now_unix_ms);
    }

    /// Pauses the clock while an operation waits for an approval or a hook,
    /// and runs it otherwise.
    pub(super) fn set_paused(&mut self, paused: bool, now_unix_ms: u64) {
        match (paused, self.paused_since_unix_ms) {
            (true, None) => self.paused_since_unix_ms = Some(now_unix_ms),
            (false, Some(since)) => {
                self.paused_ms = self
                    .paused_ms
                    .saturating_add(now_unix_ms.saturating_sub(since));
                self.paused_since_unix_ms = None;
            }
            _ => {}
        }
    }

    /// Fits `call` to the remaining budget before it starts.
    ///
    /// Returns the call to dispatch, or the result of a sleep that cannot
    /// start because the budget is used up. Calls that do not wait pass
    /// through unchanged.
    pub(super) fn fit(
        self,
        call: &ExternalToolCall,
        budget_ms: u64,
        now_unix_ms: u64,
    ) -> Result<ExternalToolCall, Box<ToolResult>> {
        let remaining_ms = self.remaining_at(now_unix_ms, budget_ms);
        match wait_kind(&call.call) {
            None => Ok(call.clone()),
            Some(WaitKind::Sleep { .. }) if remaining_ms == 0 => Err(Box::new(
                shortened_sleep_result(budget_ms, self.used_at(now_unix_ms)),
            )),
            Some(WaitKind::Sleep { seconds }) if seconds * 1000.0 > remaining_ms as f64 => Ok(
                with_argument(call, "seconds", json!(remaining_ms as f64 / 1000.0)),
            ),
            Some(WaitKind::ProcessOutput { wait_ms }) if wait_ms > remaining_ms => {
                Ok(with_argument(call, "waitMs", json!(remaining_ms)))
            }
            Some(_) => Ok(call.clone()),
        }
    }
}

/// The error a program stops with when its next tool call comes after the
/// budget is used up.
pub(super) fn budget_exceeded_error(budget_ms: u64, used_ms: u64) -> Value {
    json!({
        "name": BUDGET_EXCEEDED,
        "message": format!(
            "run_typescript used its {} time budget and stopped before its next tool call.",
            format_seconds(budget_ms)
        ),
        "details": {"budget_ms": budget_ms, "used_ms": used_ms},
    })
}

/// The length of a sleep call, in seconds. `None` for other calls.
pub(super) fn sleep_seconds(call: &ExternalToolCall) -> Option<f64> {
    match wait_kind(&call.call) {
        Some(WaitKind::Sleep { seconds }) => Some(seconds),
        _ => None,
    }
}

/// Whether Core shortened a dispatched sleep: it sleeps less than
/// `requested_seconds`, the length it had before Core fit it to the budget.
pub(super) fn is_shortened_sleep(call: &ExternalToolCall, requested_seconds: f64) -> bool {
    sleep_seconds(call).is_some_and(|seconds| seconds < requested_seconds)
}

fn wait_kind(call: &ExternalTool) -> Option<WaitKind> {
    let ExternalTool::RuntimeBuiltin {
        name, arguments, ..
    } = call
    else {
        return None;
    };
    match name {
        RuntimeBuiltinToolName::SelfSleep => Some(WaitKind::Sleep {
            seconds: arguments
                .get("seconds")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
        }),
        RuntimeBuiltinToolName::ProcessOutput => arguments
            .get("waitMs")
            .and_then(Value::as_u64)
            .filter(|wait_ms| *wait_ms > 0)
            .map(|wait_ms| WaitKind::ProcessOutput { wait_ms }),
        _ => None,
    }
}

fn with_argument(call: &ExternalToolCall, name: &str, value: Value) -> ExternalToolCall {
    let mut arguments = call.call.arguments().clone();
    if let Some(arguments) = arguments.as_object_mut() {
        arguments.insert(name.to_string(), value);
    }
    ExternalToolCall {
        call: call.call.with_arguments(arguments),
        ..call.clone()
    }
}

/// The catchable result of a sleep the budget cut short or did not start.
pub(super) fn shortened_sleep_result(budget_ms: u64, used_ms: u64) -> ToolResult {
    let message = format!(
        "This sleep ended early because run_typescript used its {} time budget. The program stops before its next tool call.",
        format_seconds(budget_ms)
    );
    ToolResult::Failure {
        content: text_content(message.clone()),
        structured_content: StructuredContent::Absent,
        meta: None,
        error: ProtocolError {
            code: BUDGET_EXCEEDED.to_string(),
            message,
            retryable: false,
            details: json!({"budget_ms": budget_ms, "used_ms": used_ms}),
        },
    }
}

pub(super) fn format_seconds(milliseconds: u64) -> String {
    format!("{:.1} s", milliseconds as f64 / 1000.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::tools::external::{ToolOrigin, effect_id_for_operation};

    fn call(name: RuntimeBuiltinToolName, arguments: Value) -> ExternalToolCall {
        ExternalToolCall {
            action_id: effect_id_for_operation(ToolOrigin::Programmatic, "operation-1"),
            call_id: "operation-1".to_string(),
            origin: ToolOrigin::Programmatic,
            call: ExternalTool::RuntimeBuiltin {
                name,
                invocation_name: String::new(),
                arguments,
            },
        }
    }

    #[test]
    fn approval_and_hook_pauses_do_not_count() {
        let mut clock = ProgramClock::started_at(0);
        clock.set_paused(true, 10_000);
        clock.set_paused(true, 20_000);
        assert_eq!(clock.used_at(100_000), 10_000);
        clock.set_paused(false, 100_000);
        assert_eq!(clock.used_at(130_000), 40_000);
        clock.start_round(130_000);
        assert_eq!(clock.used_at(130_000), 40_000);
    }

    #[test]
    fn waits_are_fit_to_the_remaining_budget() {
        let clock = ProgramClock::started_at(0);
        let sleep = call(RuntimeBuiltinToolName::SelfSleep, json!({"seconds": 10}));
        let fitted = clock.fit(&sleep, 60_000, 58_000).unwrap();
        assert_eq!(fitted.call.arguments(), &json!({"seconds": 2.0}));
        assert!(is_shortened_sleep(&fitted, 10.0));
        assert!(!is_shortened_sleep(&sleep, 10.0));

        let poll = call(
            RuntimeBuiltinToolName::ProcessOutput,
            json!({"processId": "p", "waitMs": 30_000}),
        );
        let fitted = clock.fit(&poll, 60_000, 58_000).unwrap();
        assert_eq!(
            fitted.call.arguments(),
            &json!({"processId": "p", "waitMs": 2_000})
        );

        assert!(clock.fit(&sleep, 60_000, 60_000).is_err());
        let fitted = clock.fit(&poll, 60_000, 60_000).unwrap();
        assert_eq!(
            fitted.call.arguments(),
            &json!({"processId": "p", "waitMs": 0})
        );
    }
}
