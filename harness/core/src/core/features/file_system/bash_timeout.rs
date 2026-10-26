use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::error::CoreError;
use crate::core::step_protocol::MAX_SAFE_JSON_INTEGER;
use crate::core::tools::result::invalid_tool_call_result;
use crate::core::wire::tool::ToolResult;

/// How long a bash command may run, as `settings.tools.bash_timeout` gives it.
///
/// The Host owns both values: they are what its Runtime runs. Core states them to
/// the model, rejects a call above the maximum, and gives a call without a timeout
/// the default, so the model is told what runs.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct BashTimeoutSettings {
    /// Seconds a command runs when the call does not say.
    pub default_seconds: u64,
    /// The longest timeout a call may ask for.
    pub max_seconds: u64,
}

impl BashTimeoutSettings {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if self.default_seconds == 0 || self.max_seconds == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.tools.bash_timeout",
                "bash timeouts must be positive",
            ));
        }
        if self.default_seconds > MAX_SAFE_JSON_INTEGER || self.max_seconds > MAX_SAFE_JSON_INTEGER
        {
            return Err(CoreError::invalid_configuration(
                "settings.tools.bash_timeout",
                "bash timeouts must be at most 9007199254740991 seconds, a JSON safe integer",
            ));
        }
        if self.default_seconds > self.max_seconds {
            return Err(CoreError::invalid_configuration(
                "settings.tools.bash_timeout",
                "the default bash timeout must not exceed the maximum",
            ));
        }
        Ok(())
    }
}

/// The bash timeouts the Host set, if any. Core has none of its own: without
/// them, the model is told no numbers and a call keeps the timeout it gave.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct BashTimeout(Option<BashTimeoutSettings>);

impl BashTimeout {
    /// From settings that passed validation.
    pub(crate) fn from_settings(settings: Option<&BashTimeoutSettings>) -> Self {
        Self(settings.copied())
    }

    /// Writes these timeouts into the bash input schema the model and Runtime see.
    pub(super) fn apply_to_input_schema(self, schema: &mut Value) {
        const STOPPED: &str = "A command that runs longer is stopped, and the result keeps the output it wrote so far with `timed_out` set.";
        let timeout = &mut schema["properties"]["timeout_seconds"];
        let Some(settings) = self.0 else {
            // Schemars writes the field's own default, 0, which no Host chose.
            if let Value::Object(fields) = timeout {
                fields.remove("default");
            }
            timeout["description"] = Value::from(format!("Seconds the command may run. {STOPPED}"));
            return;
        };
        timeout["default"] = Value::from(settings.default_seconds);
        timeout["maximum"] = Value::from(settings.max_seconds);
        timeout["description"] = Value::from(format!(
            "Seconds the command may run: {} by default, at most {}. {STOPPED}",
            settings.default_seconds, settings.max_seconds
        ));
    }
}

/// Gives a bash call without `timeout_seconds` the Host's default, so the
/// Runtime runs it for the time the model was told. Without Host settings the
/// call is left as it is.
pub(crate) fn bash_runtime_arguments(mut arguments: Value, timeout: BashTimeout) -> Value {
    if let (Value::Object(fields), Some(settings)) = (&mut arguments, timeout.0) {
        fields
            .entry("timeout_seconds")
            .or_insert_with(|| Value::from(settings.default_seconds));
    }
    arguments
}

/// A bash call a Runtime saved under other timeouts, or none, held to `timeout`
/// before it runs, as Core holds a fresh call: given the default when it sets no
/// timeout, and refused with the result a fresh call gets when its timeout is
/// above the maximum. Without Host settings the call is left as it is.
///
/// A restored Session saved such calls before the Host's current timeouts
/// applied. Every Runtime holds each one to them here; none keeps its own copy.
pub(crate) fn restored_bash_call(
    arguments: Value,
    timeout: BashTimeout,
) -> Result<Value, Box<ToolResult>> {
    let Some(settings) = timeout.0 else {
        return Ok(arguments);
    };
    let arguments = bash_runtime_arguments(arguments, timeout);
    match arguments.get("timeout_seconds").and_then(Value::as_u64) {
        Some(seconds) if seconds > settings.max_seconds => {
            Err(Box::new(invalid_tool_call_result(format!(
                "tool arguments do not satisfy the declared input schema: at /timeout_seconds: \
                 {seconds} is greater than the maximum of {}",
                settings.max_seconds
            ))))
        }
        _ => Ok(arguments),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::features::file_system::ToolName;
    use crate::core::tools::command_environment::CommandEnvironment;
    use serde_json::json;

    const HOST_TIMEOUT: BashTimeoutSettings = BashTimeoutSettings {
        default_seconds: 120,
        max_seconds: 300,
    };

    ///
    /// *Prepare*: The bash tool with a Host's timeouts of 120 and 300 seconds.
    /// *Do*: Read the model-visible timeout property.
    /// *Assert*: It states the Host's default and maximum. With background
    /// processes, the Unix descriptions point longer work to the background;
    /// without them, or in the in-memory shell, they do not.
    ///
    #[test]
    fn bash_timeout_schema_states_the_hosts_default_and_maximum() {
        // Prepare
        let timeout = BashTimeout::from_settings(Some(&HOST_TIMEOUT));
        let bash = ToolName::Bash
            .spec(CommandEnvironment::Unix, timeout, true)
            .expect("Unix exposes Bash");
        let foreground = ToolName::Bash
            .spec(CommandEnvironment::Unix, timeout, false)
            .expect("Unix exposes Bash");

        // Do
        let timeout_schema = &bash.input_schema["properties"]["timeout_seconds"];

        // Assert
        assert_eq!(timeout_schema["default"], 120);
        assert_eq!(timeout_schema["maximum"], 300);
        assert_eq!(timeout_schema["minimum"], 1);
        assert!(
            timeout_schema["description"]
                .as_str()
                .expect("the timeout is described")
                .starts_with("Seconds the command may run: 120 by default, at most 300.")
        );
        assert!(
            timeout_schema["description"]
                .as_str()
                .expect("the timeout is described")
                .ends_with("`timed_out` set. For longer work, use `tools.process.start` instead.")
        );
        assert!(bash.description.contains("tools.process.start"));
        assert_eq!(
            foreground.description,
            "Run a shell command and capture stdout, stderr, and its return code."
        );
        assert!(
            foreground.input_schema["properties"]["timeout_seconds"]["description"]
                .as_str()
                .expect("the timeout is described")
                .ends_with("`timed_out` set.")
        );
        let in_memory = ToolName::Bash
            .spec(CommandEnvironment::InMemoryBash, timeout, true)
            .expect("the in-memory shell exposes Bash");
        assert!(
            in_memory.input_schema["properties"]["timeout_seconds"]["description"]
                .as_str()
                .expect("the timeout is described")
                .ends_with("`timed_out` set.")
        );
        assert!(!in_memory.description.contains("tools.process"));
    }

    ///
    /// *Prepare*: Each shell, with background processes off and on.
    /// *Do*: Read the bash description and its `timeout_seconds`, and the
    /// shell's advice for a timed-out command and a Vibe Code prompt.
    /// *Assert*: All of them point long work to `tools.process.start` exactly
    /// when background processes are on in a shell that runs them, so the prompt
    /// never contradicts the tool. Windows shells keep their own description
    /// and only swap the line on `timeout_seconds`.
    ///
    #[test]
    fn bash_tool_and_shell_advice_agree_on_the_process_tools() {
        let timeout = BashTimeout::from_settings(Some(&HOST_TIMEOUT));
        for environment in [
            CommandEnvironment::Unix,
            CommandEnvironment::GitBash,
            CommandEnvironment::PowerShell,
            CommandEnvironment::InMemoryBash,
        ] {
            for background in [false, true] {
                // Prepare
                let case = format!("{environment:?}, background {background}");

                // Do
                let bash = ToolName::Bash
                    .spec(environment, timeout, background)
                    .expect("the shell exposes Bash");
                let timeout_description =
                    bash.input_schema["properties"]["timeout_seconds"]["description"]
                        .as_str()
                        .expect("the timeout is described")
                        .to_owned();
                let advice = environment.background_advice(background);

                // Assert
                let advised = background && environment != CommandEnvironment::InMemoryBash;
                assert_eq!(
                    bash.description.contains("tools.process.start"),
                    advised,
                    "{case}"
                );
                assert_eq!(
                    timeout_description.contains("tools.process.start"),
                    advised,
                    "{case}"
                );
                assert_eq!(
                    advice.is_some_and(|advice| {
                        advice.shell_guidance.contains("tools.process.start")
                            && advice.timeout_note_advice.contains("tools.process.start")
                    }),
                    advised,
                    "{case}"
                );
                if matches!(
                    environment,
                    CommandEnvironment::GitBash | CommandEnvironment::PowerShell
                ) {
                    assert!(bash.description.contains("on native Windows"), "{case}");
                    assert_eq!(
                        bash.description
                            .contains("- Use `timeout_seconds` to control how long"),
                        !background,
                        "{case}"
                    );
                }
            }
        }
    }

    ///
    /// *Prepare*: A Host's bash timeouts of 300 and 1,200 seconds, and no timeouts.
    /// *Do*: Build the bash schema and the Runtime arguments of a call with and
    /// without `timeout_seconds` from each.
    /// *Assert*: The Host's values reach the schema and an omitted
    /// `timeout_seconds`. Without them, Core states no numbers and fills none in.
    ///
    #[test]
    fn bash_timeouts_come_from_the_host_only() {
        // Prepare
        let host = BashTimeout::from_settings(Some(&BashTimeoutSettings {
            default_seconds: 300,
            max_seconds: 1_200,
        }));
        let none = BashTimeout::from_settings(None);

        // Do
        let schema = |timeout| {
            ToolName::Bash
                .spec(CommandEnvironment::Unix, timeout, false)
                .expect("Unix exposes Bash")
                .input_schema["properties"]["timeout_seconds"]
                .clone()
        };
        let (host_schema, bare_schema) = (schema(host), schema(none));
        let untimed = json!({"command": "make"});
        let timed = json!({"command": "make", "timeout_seconds": 5});

        // Assert
        assert_eq!(host_schema["default"], 300);
        assert_eq!(host_schema["maximum"], 1_200);
        assert_eq!(
            bash_runtime_arguments(untimed.clone(), host),
            json!({"command": "make", "timeout_seconds": 300})
        );
        assert_eq!(bash_runtime_arguments(timed.clone(), host), timed);
        assert!(bare_schema.get("default").is_none());
        assert!(bare_schema.get("maximum").is_none());
        assert_eq!(
            bare_schema["description"],
            "Seconds the command may run. A command that runs longer is stopped, and the result keeps the output it wrote so far with `timed_out` set."
        );
        assert_eq!(bash_runtime_arguments(untimed.clone(), none), untimed);
    }

    ///
    /// *Prepare*: Saved bash calls: one without a timeout, one within the
    /// Host's maximum of 300 seconds, one above it, and one whose timeout is
    /// not a whole number.
    /// *Do*: Hold each to the Host's timeouts, and to none.
    /// *Assert*: The untimed call gets the default and the one within the
    /// maximum runs as saved; the one above it is refused as an invalid tool
    /// call naming the maximum. A timeout that is not a whole number, or a
    /// Host without timeouts, leaves the call as it is.
    ///
    #[test]
    fn a_restored_bash_call_is_held_to_the_hosts_current_timeouts() {
        // Prepare
        let host = BashTimeout::from_settings(Some(&HOST_TIMEOUT));
        let none = BashTimeout::from_settings(None);
        let untimed = json!({"command": "make"});
        let within = json!({"command": "make", "timeout_seconds": 300});
        let above = json!({"command": "make", "timeout_seconds": 301});
        let fractional = json!({"command": "make", "timeout_seconds": 301.5});

        // Do
        let refusal = restored_bash_call(above.clone(), host).unwrap_err();

        // Assert
        assert_eq!(
            restored_bash_call(untimed.clone(), host),
            Ok(json!({"command": "make", "timeout_seconds": 120}))
        );
        assert_eq!(restored_bash_call(within.clone(), host), Ok(within));
        assert_eq!(
            *refusal,
            invalid_tool_call_result(
                "tool arguments do not satisfy the declared input schema: at /timeout_seconds: \
                 301 is greater than the maximum of 300"
                    .to_string()
            )
        );
        assert_eq!(restored_bash_call(fractional.clone(), host), Ok(fractional));
        assert_eq!(restored_bash_call(untimed.clone(), none), Ok(untimed));
        assert_eq!(restored_bash_call(above.clone(), none), Ok(above));
    }

    ///
    /// *Prepare*: Bash timeout settings with a zero timeout, a default above the
    /// maximum, or a timeout above the largest JSON safe integer.
    /// *Do*: Validate them.
    /// *Assert*: Each is rejected as invalid configuration.
    ///
    #[test]
    fn bash_timeout_settings_reject_zero_inverted_or_unsafe_timeouts() {
        let above = MAX_SAFE_JSON_INTEGER + 1;
        for (default_seconds, max_seconds) in
            [(0, 300), (120, 0), (601, 600), (60, above), (above, above)]
        {
            let settings = BashTimeoutSettings {
                default_seconds,
                max_seconds,
            };
            assert!(settings.validate().is_err());
        }
        assert!(HOST_TIMEOUT.validate().is_ok());
        let longest = BashTimeoutSettings {
            default_seconds: MAX_SAFE_JSON_INTEGER,
            max_seconds: MAX_SAFE_JSON_INTEGER,
        };
        assert!(longest.validate().is_ok());
    }
}
