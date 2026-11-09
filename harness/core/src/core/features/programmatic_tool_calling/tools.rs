#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use schemars::JsonSchema;

use crate::core::error::CoreError;
use crate::core::step_protocol::Action;
use crate::core::step_protocol::DeterminismContext;
use crate::core::step_protocol::ToolDefinition;
use crate::core::tools::result::invalid_tool_call_result;
use crate::core::tools::schema::ToolSchema;
use crate::core::wire::tool::ToolCall;

use super::context::ProgramContext;
use super::execution::start_program_execution;
use super::{AcceptedProgramResult, CompletedProgramResult, ProgramOutcome};

pub(crate) const RUN_TYPESCRIPT_NAME: &str = "run_typescript";

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct RunTypeScriptArguments {
    code: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ToolName {
    RunTypeScript,
}

impl ToolName {
    const ALL: [Self; 1] = [Self::RunTypeScript];

    pub(crate) fn direct_name(self) -> &'static str {
        match self {
            Self::RunTypeScript => RUN_TYPESCRIPT_NAME,
        }
    }

    fn definition(self, description: &str) -> ToolDefinition {
        match self {
            Self::RunTypeScript => ToolDefinition {
                name: self.direct_name().to_string(),
                description: description.to_string(),
                parameters: RunTypeScriptArguments::tool_schema(),
            },
        }
    }
}

pub(crate) fn direct_tools(description: &str) -> Vec<ToolDefinition> {
    ToolName::ALL
        .into_iter()
        .map(|name| name.definition(description))
        .collect()
}

fn tool_name(name: &str) -> Option<ToolName> {
    ToolName::ALL
        .into_iter()
        .find(|tool_name| tool_name.direct_name() == name)
}

pub(crate) fn is_direct_name(name: &str) -> bool {
    tool_name(name).is_some()
}

pub(crate) fn is_private_wrapper(name: &str) -> bool {
    tool_name(name) == Some(ToolName::RunTypeScript)
}

pub(crate) fn dispatch(
    context: ProgramContext<'_>,
    call: &ToolCall,
    determinism: DeterminismContext,
    accepted_results: &mut Vec<AcceptedProgramResult>,
) -> Option<Result<(ProgramOutcome, Vec<Action>), CoreError>> {
    let name = tool_name(&call.name)?;
    let result = match name {
        ToolName::RunTypeScript => {
            start_program_execution(context, call, determinism, accepted_results)
        }
    };
    Some(match result {
        Ok(outcome) => Ok(outcome),
        Err(error) => invalid_tool_call_outcome(error.detail().to_string()),
    })
}

fn invalid_tool_call_outcome(reason: String) -> Result<(ProgramOutcome, Vec<Action>), CoreError> {
    Ok((
        ProgramOutcome::Completed(Box::new(CompletedProgramResult {
            result: invalid_tool_call_result(reason),
        })),
        Vec::new(),
    ))
}
