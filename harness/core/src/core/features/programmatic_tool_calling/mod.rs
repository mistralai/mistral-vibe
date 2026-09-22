mod code_mode;
mod context;
mod descriptor;
mod execution;
mod model;
mod prompt;
mod settings;
mod state;
mod tools;

use crate::core::wire::tool::ToolResult;
pub(crate) use context::{ProgramContext, ResolvedProgramOperation};
pub(crate) use descriptor::{ProgrammaticName, TypeScriptTool};
pub(crate) use execution::{
    AcceptedProgramResult, ProgramAdvance, ProgramInput, ProgramTransition,
};
pub(crate) use prompt::{
    AUTONOMY_ACTIVITY, IDENTITY_GUIDANCE, current_time_prompt, tool_group_inventory_prompt,
    tool_use_prompt,
};
pub(crate) use settings::Settings;
pub(crate) use state::{
    PendingProgramRecord, ProgramCapture, ProgramExecution, ProgramFunctionRecord,
    ProgramOperationRecord,
};
pub(crate) use tools::{
    RUN_TYPESCRIPT_NAME, direct_tools, dispatch, is_direct_name, is_private_wrapper,
};

pub(crate) enum ProgramOutcome {
    Pending(ProgramExecution),
    Completed(Box<CompletedProgramResult>),
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CompletedProgramResult {
    pub(crate) result: ToolResult,
}

#[cfg(test)]
pub(crate) use execution::typescript_execution_id;
