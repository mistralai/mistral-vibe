use std::sync::Arc;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::Deserialize;
use serde::Serialize;

use crate::engine::HarnessEngine;
use crate::engine::SHARED_ENGINE;
use crate::engine::SessionHandle;

#[pyclass(name = "HarnessSession")]
pub(crate) struct PyHarnessSession {
    engine: Arc<HarnessEngine>,
    handle: Option<SessionHandle>,
}

#[pymethods]
impl PyHarnessSession {
    #[staticmethod]
    #[pyo3(signature = (config_json, initial_history_json, conversation_json = None))]
    fn create(
        py: Python<'_>,
        config_json: &str,
        initial_history_json: &str,
        conversation_json: Option<&str>,
    ) -> PyResult<Self> {
        let engine = Arc::clone(&SHARED_ENGINE);
        let config_value = config_json.to_string();
        let initial_history_value = initial_history_json.to_string();
        let conversation_value = conversation_json.map(str::to_owned);
        let handle = py
            .detach({
                let engine = Arc::clone(&engine);
                move || {
                    let config = decode_json(&config_value, "harness config")?;
                    let initial_history = decode_json(&initial_history_value, "initial history")?;
                    let conversation = conversation_value
                        .as_deref()
                        .map(|value| decode_json(value, "conversation seed"))
                        .transpose()?;
                    engine.create_with_conversation(config, initial_history, conversation)
                }
            })
            .map_err(PyValueError::new_err)?;
        Ok(Self {
            engine,
            handle: Some(handle),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (config_json, checkpoint_json, resume_input_id = 0, moved_from_json = None))]
    fn restore(
        py: Python<'_>,
        config_json: &str,
        checkpoint_json: &str,
        resume_input_id: u64,
        moved_from_json: Option<&str>,
    ) -> PyResult<Self> {
        let engine = Arc::clone(&SHARED_ENGINE);
        let config_value = config_json.to_string();
        let checkpoint_value = checkpoint_json.to_string();
        let moved_from_value = moved_from_json.map(str::to_string);
        let handle = py
            .detach({
                let engine = Arc::clone(&engine);
                move || {
                    let config = decode_json(&config_value, "harness config")?;
                    let checkpoint = crate::core::decode_checkpoint(&checkpoint_value)?;
                    let moved_from = moved_from_value
                        .as_deref()
                        .map(|value| decode_json(value, "harness config"))
                        .transpose()?;
                    engine.restore(config, checkpoint, resume_input_id, moved_from)
                }
            })
            .map_err(PyValueError::new_err)?;
        Ok(Self {
            engine,
            handle: Some(handle),
        })
    }

    fn apply(&self, py: Python<'_>, input_json: &str) -> PyResult<String> {
        let engine = Arc::clone(&self.engine);
        let handle = self.required_handle()?;
        let value = input_json.to_string();
        py.detach(move || {
            let input = decode_json(&value, "harness input")?;
            let result = engine.apply(handle, input)?;
            encode_json(&result)
        })
        .map_err(PyValueError::new_err)
    }

    fn inspect(&self, py: Python<'_>) -> PyResult<String> {
        let engine = Arc::clone(&self.engine);
        let handle = self.required_handle()?;
        py.detach(move || {
            let inspection = engine.inspect(handle)?;
            encode_json(&inspection)
        })
        .map_err(PyValueError::new_err)
    }

    fn checkpoint(&self, py: Python<'_>) -> PyResult<String> {
        let engine = Arc::clone(&self.engine);
        let handle = self.required_handle()?;
        py.detach(move || {
            let checkpoint = engine.checkpoint(handle)?;
            encode_json(&checkpoint)
        })
        .map_err(PyValueError::new_err)
    }

    fn export_conversation(&self, py: Python<'_>, context: &str) -> PyResult<String> {
        let engine = Arc::clone(&self.engine);
        let handle = self.required_handle()?;
        let context = context.to_owned();
        py.detach(move || {
            let conversation = engine.export_conversation(handle, &context)?;
            encode_json(&conversation)
        })
        .map_err(PyValueError::new_err)
    }

    fn close(&mut self, py: Python<'_>) -> PyResult<()> {
        let Some(handle) = self.handle.take() else {
            return Ok(());
        };
        let engine = Arc::clone(&self.engine);
        py.detach(move || engine.close(handle))
            .map_err(PyValueError::new_err)
    }
}

impl PyHarnessSession {
    fn required_handle(&self) -> PyResult<SessionHandle> {
        self.handle
            .ok_or_else(|| PyValueError::new_err("Harness Session is closed"))
    }
}

impl Drop for PyHarnessSession {
    fn drop(&mut self) {
        if let Some(handle) = self.handle.take() {
            let _ = self.engine.close(handle);
        }
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyHarnessSession>()?;
    module.add_function(wrap_pyfunction!(hook_tool_catalog, module)?)?;
    module.add_function(wrap_pyfunction!(process_tools_timeout_advice, module)?)?;
    module.add_function(wrap_pyfunction!(process_tools_shell_guidance, module)?)?;
    module.add_function(wrap_pyfunction!(restored_bash_call, module)?)?;
    module.add_function(wrap_pyfunction!(validate_permission_pattern, module)?)
}

#[pyfunction]
#[pyo3(signature = (pattern, *, arguments = false))]
fn validate_permission_pattern(pattern: &str, arguments: bool) -> PyResult<()> {
    crate::core::validate_permission_pattern(pattern, arguments).map_err(PyValueError::new_err)
}

/// A bash call a restored Session saved, held to the Host's current timeouts
/// before it runs, as Core holds a fresh call; see `restored_bash_call` in
/// Core's file system feature. `arguments_json` holds the call's arguments and
/// `bash_timeout_json` the Host's `settings.tools.bash_timeout`, or `None`
/// when it sets none. Gives the arguments with the default filled in, or `None`
/// when they stay as they are, and the failure result of a call above the
/// maximum, or `None` when the call may run.
#[pyfunction]
#[pyo3(signature = (arguments_json, *, bash_timeout_json))]
fn restored_bash_call(
    arguments_json: &str,
    bash_timeout_json: Option<&str>,
) -> PyResult<(Option<String>, Option<String>)> {
    use crate::core::{BashTimeout, BashTimeoutSettings};

    let arguments: serde_json::Value =
        decode_json(arguments_json, "bash arguments").map_err(PyValueError::new_err)?;
    let settings = bash_timeout_json
        .map(|value| decode_json::<BashTimeoutSettings>(value, "bash timeout"))
        .transpose()
        .map_err(PyValueError::new_err)?;
    let timeout = BashTimeout::from_settings(settings.as_ref());
    match crate::core::restored_bash_call(arguments.clone(), timeout) {
        Ok(held) if held == arguments => Ok((None, None)),
        Ok(held) => Ok((
            Some(encode_json(&held).map_err(PyValueError::new_err)?),
            None,
        )),
        Err(refusal) => Ok((
            None,
            Some(encode_json(&refusal).map_err(PyValueError::new_err)?),
        )),
    }
}

#[pyfunction]
fn hook_tool_catalog(config_json: &str) -> PyResult<String> {
    let config = decode_json(config_json, "harness config").map_err(PyValueError::new_err)?;
    encode_json(&crate::core::hook_tool_catalog(&config)).map_err(PyValueError::new_err)
}

/// What ends the note on a bash command stopped at its timeout, or `None`
/// where the process tools are not available; see `background_advice` on
/// `CommandEnvironment`. `command_environment` is its `mode`.
#[pyfunction]
#[pyo3(signature = (*, background_processes, command_environment))]
fn process_tools_timeout_advice(
    background_processes: bool,
    command_environment: &str,
) -> PyResult<Option<&'static str>> {
    Ok(
        background_advice(background_processes, command_environment)?
            .map(|advice| advice.timeout_note_advice),
    )
}

/// The shell guidance of a Vibe Code prompt, or `None` where the process tools
/// are not available; see `process_tools_timeout_advice`.
#[pyfunction]
#[pyo3(signature = (*, background_processes, command_environment))]
fn process_tools_shell_guidance(
    background_processes: bool,
    command_environment: &str,
) -> PyResult<Option<&'static str>> {
    Ok(
        background_advice(background_processes, command_environment)?
            .map(|advice| advice.shell_guidance),
    )
}

fn background_advice(
    background_processes: bool,
    command_environment: &str,
) -> PyResult<Option<crate::core::tools::command_environment::BackgroundAdvice>> {
    let environment: crate::core::tools::command_environment::CommandEnvironment =
        serde_json::from_value(serde_json::json!({ "mode": command_environment }))
            .map_err(|error| PyValueError::new_err(format!("command environment: {error}")))?;
    Ok(environment.background_advice(background_processes))
}

fn decode_json<'a, T: Deserialize<'a>>(value: &'a str, label: &str) -> Result<T, String> {
    serde_json::from_str(value).map_err(|error| format!("invalid {label} JSON: {error}"))
}

fn encode_json<T: Serialize>(value: &T) -> Result<String, String> {
    serde_json::to_string(value).map_err(|error| error.to_string())
}
