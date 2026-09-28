#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use schemars::JsonSchema;
use serde_json::Value;

use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

use super::SkillDefinition;

const DESCRIPTION: &str = concat!(
    "Load a specialized skill that provides domain-specific instructions and workflows. ",
    "When a task matches one of the available skills listed in your system prompt, ",
    "call this tool with the exact skill name to load the full skill instructions. ",
    "Only listed names are valid. Call exactly with {\"name\":\"<exact name>\"}; ",
    "no other argument keys are accepted."
);

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct SkillReadArguments {
    #[schemars(length(min = 1))]
    /// The exact name of the skill to load from the available skills.
    name: String,
}

#[derive(JsonSchema)]
#[schemars(transparent)]
struct SkillReadResult(String);

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum ToolName {
    Read,
}

impl ToolName {
    pub(crate) fn direct_name(self) -> &'static str {
        match self {
            Self::Read => "skill",
        }
    }

    pub(crate) fn runtime_tool(self) -> RuntimeBuiltinToolName {
        match self {
            Self::Read => RuntimeBuiltinToolName::SkillRead,
        }
    }

    pub(crate) fn function_name(self) -> &'static str {
        match self {
            Self::Read => "read",
        }
    }
}

pub(crate) struct ToolSpec {
    pub(crate) name: ToolName,
    pub(crate) description: &'static str,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

pub(crate) struct DirectToolSpec {
    pub(crate) name: ToolName,
    pub(crate) description: &'static str,
    pub(crate) input_schema: Value,
}

pub(crate) fn tool() -> ToolSpec {
    ToolSpec {
        name: ToolName::Read,
        description: DESCRIPTION,
        input_schema: SkillReadArguments::tool_schema(),
        output_schema: SkillReadResult::tool_schema(),
    }
}

pub(crate) fn direct_tool<'a>(
    skills: impl IntoIterator<Item = &'a SkillDefinition>,
) -> Option<DirectToolSpec> {
    skills.into_iter().next()?;
    Some(DirectToolSpec {
        name: ToolName::Read,
        description: DESCRIPTION,
        input_schema: SkillReadArguments::tool_schema(),
    })
}

pub(crate) fn is_direct_name(name: &str) -> bool {
    name == ToolName::Read.direct_name()
}
