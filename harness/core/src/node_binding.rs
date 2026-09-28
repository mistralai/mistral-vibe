use std::sync::Arc;

use napi::Env;
use napi::Task;
use napi::bindgen_prelude::AsyncTask;

use crate::engine::HarnessEngine;
use crate::engine::SHARED_ENGINE;
use crate::engine::SessionHandle;

#[napi_derive::napi(js_name = "HarnessEngine")]
pub(crate) struct NodeHarnessEngine {
    engine: Arc<HarnessEngine>,
}

#[napi_derive::napi]
impl NodeHarnessEngine {
    #[napi_derive::napi(constructor)]
    pub fn new() -> Self {
        Self {
            engine: Arc::clone(&SHARED_ENGINE),
        }
    }

    #[napi_derive::napi]
    pub fn create(
        &self,
        config_json: String,
        initial_history_json: String,
    ) -> AsyncTask<CreateSessionTask> {
        AsyncTask::new(CreateSessionTask {
            engine: Arc::clone(&self.engine),
            source: SessionSource::Create {
                config_json,
                initial_history_json,
            },
        })
    }

    /// `resume_input_id` is the last input ID the Runtime durably accepted for
    /// this session. Omitting it starts a fresh sequence.
    #[napi_derive::napi]
    pub fn restore(
        &self,
        config_json: String,
        checkpoint_json: String,
        resume_input_id: Option<i64>,
    ) -> AsyncTask<CreateSessionTask> {
        AsyncTask::new(CreateSessionTask {
            engine: Arc::clone(&self.engine),
            source: SessionSource::Restore {
                config_json,
                checkpoint_json,
                resume_input_id,
            },
        })
    }

    #[napi_derive::napi]
    pub fn hook_tool_catalog(&self, config_json: String) -> napi::Result<String> {
        let config = serde_json::from_str(&config_json)
            .map_err(|error| node_error(format!("invalid harness config JSON: {error}")))?;
        serde_json::to_string(&crate::core::hook_tool_catalog(&config))
            .map_err(|error| node_error(error.to_string()))
    }

    #[napi_derive::napi]
    pub fn live_session_count(&self) -> napi::Result<u32> {
        let count = self.engine.live_session_count().map_err(node_error)?;
        u32::try_from(count)
            .map_err(|_| node_error("Harness Engine session count exceeds u32".to_string()))
    }
}

impl Default for NodeHarnessEngine {
    fn default() -> Self {
        Self::new()
    }
}

#[napi_derive::napi(js_name = "HarnessSession")]
pub(crate) struct NodeHarnessSession {
    engine: Arc<HarnessEngine>,
    handle: Option<SessionHandle>,
}

#[napi_derive::napi]
impl NodeHarnessSession {
    #[napi_derive::napi]
    pub fn apply(&self, input_json: String) -> napi::Result<AsyncTask<SessionOperationTask>> {
        Ok(AsyncTask::new(SessionOperationTask {
            engine: Arc::clone(&self.engine),
            handle: self.required_handle()?,
            operation: SessionOperation::Apply(input_json),
        }))
    }

    #[napi_derive::napi]
    pub fn inspect(&self) -> napi::Result<AsyncTask<SessionOperationTask>> {
        Ok(AsyncTask::new(SessionOperationTask {
            engine: Arc::clone(&self.engine),
            handle: self.required_handle()?,
            operation: SessionOperation::Inspect,
        }))
    }

    #[napi_derive::napi]
    pub fn checkpoint(&self) -> napi::Result<AsyncTask<SessionOperationTask>> {
        Ok(AsyncTask::new(SessionOperationTask {
            engine: Arc::clone(&self.engine),
            handle: self.required_handle()?,
            operation: SessionOperation::Checkpoint,
        }))
    }

    #[napi_derive::napi]
    pub fn dispose(&mut self) -> napi::Result<AsyncTask<CloseSessionTask>> {
        let handle = self
            .handle
            .take()
            .ok_or_else(|| node_error("Harness Session is already disposed".to_string()))?;
        Ok(AsyncTask::new(CloseSessionTask {
            engine: Arc::clone(&self.engine),
            handle,
        }))
    }

    fn required_handle(&self) -> napi::Result<SessionHandle> {
        self.handle
            .ok_or_else(|| node_error("Harness Session is disposed".to_string()))
    }
}

impl Drop for NodeHarnessSession {
    fn drop(&mut self) {
        if let Some(handle) = self.handle.take() {
            let _ = self.engine.close(handle);
        }
    }
}

enum SessionSource {
    Create {
        config_json: String,
        initial_history_json: String,
    },
    Restore {
        config_json: String,
        checkpoint_json: String,
        resume_input_id: Option<i64>,
    },
}

pub(crate) struct CreateSessionTask {
    engine: Arc<HarnessEngine>,
    source: SessionSource,
}

impl Task for CreateSessionTask {
    type Output = SessionHandle;
    type JsValue = NodeHarnessSession;

    fn compute(&mut self) -> napi::Result<Self::Output> {
        match &self.source {
            SessionSource::Create {
                config_json,
                initial_history_json,
            } => {
                let config = serde_json::from_str(config_json)
                    .map_err(|error| node_error(format!("invalid harness config JSON: {error}")))?;
                let initial_history =
                    serde_json::from_str(initial_history_json).map_err(|error| {
                        node_error(format!("invalid initial history JSON: {error}"))
                    })?;
                self.engine
                    .create_with_history(config, initial_history)
                    .map_err(node_error)
            }
            SessionSource::Restore {
                config_json,
                checkpoint_json,
                resume_input_id,
            } => {
                let config = serde_json::from_str(config_json)
                    .map_err(|error| node_error(format!("invalid harness config JSON: {error}")))?;
                let checkpoint =
                    crate::core::decode_checkpoint(checkpoint_json).map_err(node_error)?;
                let resume_input_id = u64::try_from(resume_input_id.unwrap_or(0))
                    .map_err(|_| node_error("resumed input ID must not be negative".to_string()))?;
                self.engine
                    .restore(config, checkpoint, resume_input_id)
                    .map_err(node_error)
            }
        }
    }

    fn resolve(&mut self, _env: Env, handle: Self::Output) -> napi::Result<Self::JsValue> {
        Ok(NodeHarnessSession {
            engine: Arc::clone(&self.engine),
            handle: Some(handle),
        })
    }
}

enum SessionOperation {
    Apply(String),
    Inspect,
    Checkpoint,
}

pub(crate) struct SessionOperationTask {
    engine: Arc<HarnessEngine>,
    handle: SessionHandle,
    operation: SessionOperation,
}

impl Task for SessionOperationTask {
    type Output = String;
    type JsValue = String;

    fn compute(&mut self) -> napi::Result<Self::Output> {
        match &self.operation {
            SessionOperation::Apply(value) => {
                let input = serde_json::from_str(value)
                    .map_err(|error| node_error(format!("invalid harness input JSON: {error}")))?;
                let result = self.engine.apply(self.handle, input).map_err(node_error)?;
                serde_json::to_string(&result).map_err(|error| node_error(error.to_string()))
            }
            SessionOperation::Inspect => {
                let inspection = self.engine.inspect(self.handle).map_err(node_error)?;
                serde_json::to_string(&inspection).map_err(|error| node_error(error.to_string()))
            }
            SessionOperation::Checkpoint => {
                let checkpoint = self.engine.checkpoint(self.handle).map_err(node_error)?;
                serde_json::to_string(&checkpoint).map_err(|error| node_error(error.to_string()))
            }
        }
    }

    fn resolve(&mut self, _env: Env, output: Self::Output) -> napi::Result<Self::JsValue> {
        Ok(output)
    }
}

pub(crate) struct CloseSessionTask {
    engine: Arc<HarnessEngine>,
    handle: SessionHandle,
}

impl Task for CloseSessionTask {
    type Output = ();
    type JsValue = ();

    fn compute(&mut self) -> napi::Result<Self::Output> {
        self.engine.close(self.handle).map_err(node_error)
    }

    fn resolve(&mut self, _env: Env, output: Self::Output) -> napi::Result<Self::JsValue> {
        Ok(output)
    }
}

fn node_error(message: String) -> napi::Error {
    napi::Error::from_reason(message)
}
