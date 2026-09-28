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
    fn create(py: Python<'_>, config_json: &str, initial_history_json: &str) -> PyResult<Self> {
        let engine = Arc::clone(&SHARED_ENGINE);
        let config_value = config_json.to_string();
        let initial_history_value = initial_history_json.to_string();
        let handle = py
            .detach({
                let engine = Arc::clone(&engine);
                move || {
                    let config = decode_json(&config_value, "harness config")?;
                    let initial_history = decode_json(&initial_history_value, "initial history")?;
                    engine.create_with_history(config, initial_history)
                }
            })
            .map_err(PyValueError::new_err)?;
        Ok(Self {
            engine,
            handle: Some(handle),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (config_json, checkpoint_json, resume_input_id = 0))]
    fn restore(
        py: Python<'_>,
        config_json: &str,
        checkpoint_json: &str,
        resume_input_id: u64,
    ) -> PyResult<Self> {
        let engine = Arc::clone(&SHARED_ENGINE);
        let config_value = config_json.to_string();
        let checkpoint_value = checkpoint_json.to_string();
        let handle = py
            .detach({
                let engine = Arc::clone(&engine);
                move || {
                    let config = decode_json(&config_value, "harness config")?;
                    let checkpoint = crate::core::decode_checkpoint(&checkpoint_value)?;
                    engine.restore(config, checkpoint, resume_input_id)
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
    module.add_function(wrap_pyfunction!(hook_tool_catalog, module)?)
}

#[pyfunction]
fn hook_tool_catalog(config_json: &str) -> PyResult<String> {
    let config = decode_json(config_json, "harness config").map_err(PyValueError::new_err)?;
    encode_json(&crate::core::hook_tool_catalog(&config)).map_err(PyValueError::new_err)
}

fn decode_json<'a, T: Deserialize<'a>>(value: &'a str, label: &str) -> Result<T, String> {
    serde_json::from_str(value).map_err(|error| format!("invalid {label} JSON: {error}"))
}

fn encode_json<T: Serialize>(value: &T) -> Result<String, String> {
    serde_json::to_string(value).map_err(|error| error.to_string())
}
