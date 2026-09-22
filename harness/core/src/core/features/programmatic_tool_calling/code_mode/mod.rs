//! Private code-mode evaluator.
//!
//! The rest of Core depends on this evaluator-neutral interface. Deno and V8
//! types, isolate setup, source generation, watchdogs, and transpilation stay
//! inside `v8`. Durable replay records live with the program state machine.

use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

use crate::core::features::programmatic_tool_calling::TypeScriptTool;
use crate::core::features::programmatic_tool_calling::model::PartialEvaluation;
use crate::core::features::programmatic_tool_calling::model::ToolState;
use crate::core::step_protocol::DeterminismContext;

mod v8;

pub(crate) struct EvaluationRequest<'a> {
    pub(crate) partial_evaluation: &'a PartialEvaluation,
    pub(crate) tools: &'a [TypeScriptTool],
    pub(crate) execution_id: &'a str,
    pub(crate) determinism: DeterminismContext,
    pub(crate) max_program_effects: u32,
    pub(crate) max_program_operations: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum CodeResult {
    Success { value: Value },
    Error { error: Value },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum EvaluationOutcome {
    PartialEvaluation {
        partial_evaluation: PartialEvaluation,
    },
    CodeResult {
        #[serde(skip_serializing_if = "Option::is_none")]
        stdout: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        stderr: Option<String>,
        tool_state: Vec<ToolState>,
        result: CodeResult,
    },
    Error {
        error: Value,
    },
}

pub(crate) fn evaluate(request: EvaluationRequest<'_>) -> Result<EvaluationOutcome, String> {
    v8::evaluate(request)
}
