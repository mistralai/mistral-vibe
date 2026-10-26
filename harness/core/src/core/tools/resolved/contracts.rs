use serde_json::Value;

use crate::core::config::HarnessConfig;
use crate::core::error::CoreError;
use crate::core::features::file_system;
use crate::core::tools::arguments::{ArgumentsRejection, validate_arguments, validate_json_schema};
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, RuntimeBuiltinToolName, ToolTarget,
};
use crate::core::tools::resolved::assembly;

#[derive(Clone, Debug, PartialEq)]
pub(super) struct ToolContracts {
    runtime: Vec<RuntimeContract>,
    provided: Vec<ProvidedInputContract>,
    bash_timeout: file_system::BashTimeout,
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
        let bash_timeout = assembly::bash_timeout(config);
        let mut runtime = file_system::runtime_contracts(bash_timeout)
            .into_iter()
            .map(|contract| RuntimeContract {
                name: contract.name,
                input_schema: contract.input_schema,
                output_schema: Some(contract.output_schema),
            })
            .collect::<Vec<_>>();
        runtime.push(RuntimeContract {
            name: RuntimeBuiltinToolName::SelfWait,
            input_schema: crate::core::features::model_wait::runtime_input_schema(
                config.settings.tools.max_wait_ms,
            ),
            output_schema: Some(crate::core::features::model_wait::output_schema()),
        });
        runtime.extend(
            assembly::non_filesystem_runtime_builtin_tools(
                config.settings.tools.programmatic.max_sleep_ms,
                config
                    .settings
                    .tools
                    .subagents
                    .supports_conversation_forking(),
            )
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
        // These contracts only settle persisted Actions. They install no executable routes,
        // and current catalog schemas must not reinterpret a previously accepted result.
        runtime.extend(
            RuntimeBuiltinToolName::DEPRECATED.map(|name| RuntimeContract {
                name,
                input_schema: Value::Bool(true),
                output_schema: Some(Value::Bool(true)),
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
        Self {
            runtime,
            provided,
            bash_timeout,
        }
    }

    /// The call to `target`, with its arguments as the runtime takes them.
    pub(super) fn external_tool(
        &self,
        target: ToolTarget,
        invocation_name: &str,
        arguments: Value,
    ) -> ExternalTool {
        let arguments = match &target {
            ToolTarget::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::FileSystemBash,
            } => file_system::bash_runtime_arguments(arguments, self.bash_timeout),
            ToolTarget::RuntimeBuiltin { .. } | ToolTarget::Provided { .. } => arguments,
        };
        ExternalTool::from_target(target, invocation_name, arguments)
    }

    pub(super) fn resolve_effective_call(
        &self,
        original: &ExternalToolCall,
        arguments: Value,
    ) -> Result<ExternalToolCall, CoreError> {
        let tool = match &original.call {
            ExternalTool::RuntimeBuiltin { name, .. } => Some(*name),
            ExternalTool::Provided { .. } => None,
        };
        let schema = self.input_schema(&original.call)?;
        validate_arguments(tool, schema, &arguments).map_err(|rejection| {
            CoreError::invalid_command(format!(
                "pre-tool hook arguments do not satisfy the original tool schema: {}",
                rejection.detail(schema)
            ))
        })?;
        let arguments = match &original.call {
            ExternalTool::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::SelfWait,
                arguments: original_arguments,
                ..
            } => crate::core::features::model_wait::clamp_hook_arguments(
                original_arguments,
                arguments,
            )?,
            ExternalTool::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::FileSystemBash,
                ..
            } => file_system::bash_runtime_arguments(arguments, self.bash_timeout),
            _ => arguments,
        };
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
        tool_name: &str,
        value: &Value,
    ) -> Result<(), CoreError> {
        let tool = match target {
            ToolTarget::RuntimeBuiltin { name } => Some(*name),
            ToolTarget::Provided { .. } => None,
        };
        validate_model_input(
            tool,
            tool_name,
            self.input_schema_for_target(target)?,
            value,
        )
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
        validate_json_schema(schema, value).map_err(|rejection| rejection.detail(schema))
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

/// A model's direct call. A schema failure keeps the schema wording, even when
/// the tool words the error itself, so it still reads as a schema failure; a
/// rule the schema cannot state speaks for the tool, as in a program.
pub(super) fn validate_model_input(
    tool: Option<RuntimeBuiltinToolName>,
    tool_name: &str,
    schema: &Value,
    value: &Value,
) -> Result<(), CoreError> {
    validate_arguments(tool, schema, value).map_err(|rejection| {
        CoreError::invalid_command(match rejection {
            ArgumentsRejection::Domain(message) => {
                format!("Invalid arguments for {tool_name}: {message}")
            }
            rejection => format!(
                "tool arguments do not satisfy the declared input schema: {}",
                rejection.detail(schema)
            ),
        })
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn spawn_schema() -> Value {
        crate::core::features::subagents::tools(
            true,
            &crate::core::prompt::PromptSettings::default().agent_tools,
        )
        .into_iter()
        .find(|spec| spec.name.runtime_tool() == RuntimeBuiltinToolName::SubagentSpawn)
        .unwrap()
        .input_schema
    }

    #[test]
    fn a_direct_call_rejected_by_a_worded_schema_error_keeps_the_schema_prefix() {
        let error = validate_model_input(
            Some(RuntimeBuiltinToolName::SubagentSpawn),
            "agents_spawn",
            &spawn_schema(),
            &json!({"name": "a/b", "message": "hi"}),
        )
        .unwrap_err();

        assert_eq!(
            error.detail(),
            "tool arguments do not satisfy the declared input schema: at /name: \"a/b\" is not \
             a valid name: use one path segment, without `/`, other than `.` or `..`"
        );
    }

    #[test]
    fn a_direct_call_rejected_by_the_domain_check_reads_as_in_a_program() {
        let error = validate_model_input(
            Some(RuntimeBuiltinToolName::SubagentSpawn),
            "agents_spawn",
            &spawn_schema(),
            &json!({"name": "a", "message": "hi", "context": {"mode": "forked", "turns": 3.0}}),
        )
        .unwrap_err();

        assert!(
            error
                .detail()
                .starts_with("Invalid arguments for agents_spawn: at /context: "),
            "{}",
            error.detail()
        );
    }

    #[test]
    fn a_direct_call_rejected_by_the_schema_keeps_the_schema_wording() {
        let error = validate_model_input(
            Some(RuntimeBuiltinToolName::SubagentSpawn),
            "agents_spawn",
            &spawn_schema(),
            &json!({"name": "a", "message": 7}),
        )
        .unwrap_err();

        assert!(
            error.detail().starts_with(
                "tool arguments do not satisfy the declared input schema: at /message"
            ),
            "{}",
            error.detail()
        );
    }

    ///
    /// *Prepare*: A Host's bash timeouts with a maximum of 300 seconds, and a
    /// bash call asking for 301.
    /// *Do*: Check it as a model's fresh call, and hold it as a restored one.
    /// *Assert*: Both refuse it with the same reason, so a restored call reads
    /// to the model as a fresh one would.
    ///
    #[test]
    fn a_restored_bash_call_above_the_maximum_is_refused_as_a_fresh_one() {
        // Prepare
        let timeout =
            file_system::BashTimeout::from_settings(Some(&file_system::BashTimeoutSettings {
                default_seconds: 120,
                max_seconds: 300,
            }));
        let bash = file_system::runtime_contracts(timeout)
            .into_iter()
            .find(|contract| contract.name == RuntimeBuiltinToolName::FileSystemBash)
            .unwrap();
        let arguments = json!({"command": "make", "timeout_seconds": 301});

        // Do
        let fresh = validate_model_input(
            Some(RuntimeBuiltinToolName::FileSystemBash),
            "bash",
            &bash.input_schema,
            &arguments,
        )
        .unwrap_err();
        let restored = file_system::restored_bash_call(arguments, timeout).unwrap_err();

        // Assert
        assert_eq!(
            *restored,
            crate::core::tools::result::invalid_tool_call_result(fresh.detail().to_string())
        );
    }

    fn hook_spawn_call(arguments: Value) -> ExternalToolCall {
        ExternalToolCall {
            action_id: "action".to_string(),
            call_id: "call".to_string(),
            origin: crate::core::tools::external::ToolOrigin::Programmatic,
            call: ExternalTool::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::SubagentSpawn,
                invocation_name: "agents.spawn".to_string(),
                arguments,
            },
        }
    }

    #[test]
    fn a_hook_result_for_a_forked_spawn_is_checked_against_the_offered_schema() {
        let mut config = crate::core::testing::config();
        config.settings.tools.subagents =
            crate::core::features::subagents::Mode::enabled().with_conversation_forking(true);
        let contracts = ToolContracts::compile(&config);
        let arguments = json!({"name": "a", "message": "hi", "context": "fresh"});

        let resolved = contracts
            .resolve_effective_call(&hook_spawn_call(arguments.clone()), arguments.clone())
            .unwrap();

        assert_eq!(resolved.call.arguments(), &arguments);
    }

    #[test]
    fn a_hook_result_with_a_context_is_rejected_when_forking_is_off() {
        let contracts = ToolContracts::compile(&crate::core::testing::config());
        let arguments = json!({"name": "a", "message": "hi", "context": "fresh"});

        let error = contracts
            .resolve_effective_call(&hook_spawn_call(arguments.clone()), arguments)
            .unwrap_err();

        assert!(
            error
                .detail()
                .starts_with("pre-tool hook arguments do not satisfy the original tool schema: "),
            "{}",
            error.detail()
        );
    }
}
