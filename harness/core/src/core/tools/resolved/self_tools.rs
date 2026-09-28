#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use schemars::JsonSchema;

use crate::core::features::programmatic_tool_calling::{ProgrammaticName, TypeScriptTool};
use crate::core::tools::external::{RuntimeBuiltinToolName, ToolTarget};
use crate::core::tools::resolved::assembly::{SELF_NAMESPACE, ToolBinding};
use crate::core::tools::schema::ToolSchema;

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct SleepArguments {
    #[schemars(extend("exclusiveMinimum" = 0, "exclusiveMaximum" = 60))]
    seconds: f64,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct SleepResult {
    seconds: f64,
}

pub(crate) fn self_tools() -> Vec<ToolBinding> {
    vec![ToolBinding {
        descriptor: TypeScriptTool {
            name: "sleep".to_string(),
            programmatic_name: ProgrammaticName {
                namespace: SELF_NAMESPACE.to_string(),
                name: "sleep".to_string(),
            },
            description: "Pause this agent execution for a bounded number of seconds while Runtime-owned background work continues.".to_string(),
            input_schema: SleepArguments::tool_schema(),
            output_schema: Some(SleepResult::tool_schema()),
        },
        target: ToolTarget::RuntimeBuiltin {
            name: RuntimeBuiltinToolName::SelfSleep,
        },
    }]
}
