//! The run server's requests: `initialize`, `ping`, `run/start` and `run/stop`.

use std::path::Path;

use serde_json::{json, Value};

use super::protocol::{
    invalid_params, parse, EmptyParams, Failure, InitializeParams, Request, RunStartParams,
    RunStopParams, PROTOCOL_VERSION, SERVER_NAME,
};
use super::run::RunSpec;
use super::Server;

impl Server {
    pub(super) fn dispatch(&mut self, request: Request) -> Result<Value, Failure> {
        match request.method.as_str() {
            "ping" => parse::<EmptyParams>(&request.params).map(|_| json!({})),
            "initialize" => self.initialize(&request.params),
            _ if !self.initialized => Err(Failure::new("not_initialized", "Send initialize first")),
            "run/start" => self.start(&request.params),
            "run/stop" => self.stop(&request.params),
            _ => Err(Failure::new("method_not_found", "Method not found")
                .with_data(json!({"method": request.method}))),
        }
    }

    fn initialize(&mut self, params: &Value) -> Result<Value, Failure> {
        if self.initialized {
            return Err(Failure::new("invalid_request", "Already initialized"));
        }
        let params: InitializeParams = parse(params)?;
        let mut missing = Vec::new();
        if params.protocol_version.is_none() {
            missing.push(("protocolVersion".to_owned(), "required".to_owned()));
        }
        if params.client_info.is_none() {
            missing.push(("clientInfo".to_owned(), "required".to_owned()));
        }
        if !missing.is_empty() {
            return Err(invalid_params(missing));
        }
        if params.protocol_version != Some(PROTOCOL_VERSION) {
            return Err(
                Failure::new("invalid_request", "Unsupported protocol version")
                    .with_data(json!({"protocolVersion": PROTOCOL_VERSION})),
            );
        }
        if let Some(client) = params.client_info {
            tracing::info!(
                client = client.name,
                version = client.version,
                "Initialized"
            );
        }
        self.initialized = true;
        Ok(json!({
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": env!("CARGO_PKG_VERSION")},
            "maxRuns": self.max_runs,
        }))
    }

    fn start(&mut self, params: &Value) -> Result<Value, Failure> {
        let params: RunStartParams = parse(params)?;
        let spec = RunSpec::from_params(params, &self.invocation_cwd)?;
        if self.runs.contains_key(&spec.run_id) {
            return Err(Failure::new("conflict", "A live run has this runId")
                .with_data(json!({"reason": "duplicate_run"})));
        }
        if let Some(max_runs) = self.max_runs.filter(|max| self.runs.len() >= *max) {
            return Err(Failure::new("conflict", "As many runs as maxRuns are live")
                .with_data(json!({"reason": "at_capacity", "maxRuns": max_runs})));
        }
        let open = self.open_options(&spec);
        let channel = self.pool.place(&open);
        if let Err(error) = &channel {
            tracing::error!(run = spec.run_id, "Could not place the run: {error}");
        }
        let run_id = spec.run_id.clone();
        let live = self.spawn_run(spec, channel);
        self.runs.insert(run_id, live);
        Ok(json!({}))
    }

    fn stop(&mut self, params: &Value) -> Result<Value, Failure> {
        let params: RunStopParams = parse(params)?;
        let Some(run_id) = params.run_id.filter(|id| !id.is_empty()) else {
            return Err(invalid_params(vec![(
                "runId".to_owned(),
                "required, and not empty".to_owned(),
            )]));
        };
        let Some(live) = self.runs.get(&run_id) else {
            return Err(Failure::new("not_found", "No live run has this runId"));
        };
        live.stop();
        Ok(json!({}))
    }

    /// The run's channel options: its socket as its app-server must open it.
    fn open_options(&self, spec: &RunSpec) -> Value {
        let socket = spec
            .options
            .agent_socket
            .as_deref()
            .unwrap_or(Path::new(""));
        let socket = crate::agent_socket::server_path(
            socket,
            Some(&self.invocation_cwd),
            self.server_cwd.as_deref(),
        );
        json!({
            "runId": spec.run_id,
            "agentSocket": socket.display().to_string(),
            "config": spec.config,
            "credentials": spec.credentials,
        })
    }
}
