use serde_json::Value;

use crate::core::config::HarnessConfig;
use crate::core::error::CoreError;
use crate::core::features::file_system;
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, RuntimeBuiltinToolName, ToolTarget,
};
use crate::core::tools::resolved::assembly;

#[derive(Clone, Debug, PartialEq)]
pub(super) struct ToolContracts {
    runtime: Vec<RuntimeContract>,
    provided: Vec<ProvidedInputContract>,
}

#[derive(Clone, Debug, PartialEq)]
struct RuntimeContract {
    name: RuntimeBuiltinToolName,
    input_schema: Value,
    output_schema: Option<Value>,
}

#[derive(Clone, Debug, PartialEq)]
struct ProvidedInputContract {
    group_name: String,
    tool_name: String,
    input_schema: Value,
}

impl ToolContracts {
    pub(super) fn compile(config: &HarnessConfig) -> Self {
        let mut runtime = file_system::runtime_contracts()
            .into_iter()
            .map(|contract| RuntimeContract {
                name: contract.name,
                input_schema: contract.input_schema,
                output_schema: Some(contract.output_schema),
            })
            .collect::<Vec<_>>();
        runtime.extend(
            assembly::non_filesystem_runtime_builtin_tools()
                .into_iter()
                .map(|tool| {
                    let ToolTarget::RuntimeBuiltin { name } = tool.target else {
                        unreachable!("resolved Runtime built-ins contain a provided tool")
                    };
                    RuntimeContract {
                        name,
                        input_schema: tool.descriptor.input_schema,
                        output_schema: tool.descriptor.output_schema,
                    }
                }),
        );
        let provided = config
            .tool_groups()
            .flat_map(|group| {
                group.tools.iter().map(|tool| ProvidedInputContract {
                    group_name: group.name.clone(),
                    tool_name: tool.name.clone(),
                    input_schema: tool.input_schema.clone(),
                })
            })
            .collect();
        Self { runtime, provided }
    }

    pub(super) fn resolve_effective_call(
        &self,
        original: &ExternalToolCall,
        arguments: Value,
    ) -> Result<ExternalToolCall, CoreError> {
        validate_json_schema(self.input_schema(&original.call)?, &arguments).map_err(|error| {
            CoreError::invalid_command(format!(
                "pre-tool hook arguments do not satisfy the original tool schema: {error}"
            ))
        })?;
        Ok(ExternalToolCall {
            action_id: original.action_id.clone(),
            call_id: original.call_id.clone(),
            origin: original.origin,
            call: original.call.with_arguments(arguments),
        })
    }

    pub(super) fn validate_input(
        &self,
        target: &ToolTarget,
        value: &Value,
    ) -> Result<(), CoreError> {
        validate_model_input(self.input_schema_for_target(target)?, value)
    }

    pub(super) fn validate_runtime_builtin_output(
        &self,
        name: RuntimeBuiltinToolName,
        value: &Value,
    ) -> Result<(), String> {
        let schema = self
            .runtime_contract(name)
            .output_schema
            .as_ref()
            .expect("Runtime built-ins must declare an output schema");
        validate_json_schema(schema, value)
    }

    fn input_schema(&self, tool: &ExternalTool) -> Result<&Value, CoreError> {
        match tool {
            ExternalTool::RuntimeBuiltin { name, .. } => {
                Ok(&self.runtime_contract(*name).input_schema)
            }
            ExternalTool::Provided {
                group_name,
                tool_name,
                ..
            } => self.provided_input_schema(group_name, tool_name),
        }
    }

    fn input_schema_for_target(&self, target: &ToolTarget) -> Result<&Value, CoreError> {
        match target {
            ToolTarget::RuntimeBuiltin { name } => Ok(&self.runtime_contract(*name).input_schema),
            ToolTarget::Provided {
                group_name,
                tool_name,
            } => self.provided_input_schema(group_name, tool_name),
        }
    }

    fn provided_input_schema(
        &self,
        group_name: &str,
        tool_name: &str,
    ) -> Result<&Value, CoreError> {
        self.provided
            .iter()
            .find(|contract| contract.group_name == group_name && contract.tool_name == tool_name)
            .map(|contract| &contract.input_schema)
            .ok_or_else(|| {
                CoreError::invariant(format!(
                    "provided tool {group_name}.{tool_name} disappeared from resolved tools"
                ))
            })
    }

    fn runtime_contract(&self, name: RuntimeBuiltinToolName) -> &RuntimeContract {
        self.runtime
            .iter()
            .find(|contract| contract.name == name)
            .expect("resolved Runtime built-ins are complete")
    }
}

pub(super) fn validate_model_input(schema: &Value, value: &Value) -> Result<(), CoreError> {
    validate_json_schema(schema, value).map_err(|error| {
        CoreError::invalid_command(format!(
            "tool arguments do not satisfy the declared input schema: {error}"
        ))
    })
}

fn validate_json_schema(schema: &Value, value: &Value) -> Result<(), String> {
    let validator = jsonschema::draft7::options()
        .should_validate_formats(true)
        .build(schema)
        .map_err(|error| format!("invalid Core-owned JSON Schema: {error}"))?;
    validator.validate(value).map_err(|error| error.to_string())
}
