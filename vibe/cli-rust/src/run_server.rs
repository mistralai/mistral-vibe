//! `vibe run-server` (experimental): many headless runs in one process; protocol in the README.

mod dispatch;
mod pool;
mod protocol;
mod run;

use std::collections::HashMap;
use std::path::PathBuf;

use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tokio::sync::mpsc;
use tracing::Instrument;

use self::pool::Pool;
use self::protocol::RunEnded;
use self::run::LiveRun;
use crate::server::lines::{Line, LineReader};
use crate::server::{signal, Launch};

/// The longest request line read; a longer one is a protocol error.
const MAX_MESSAGE: usize = 64 * 1024 * 1024;

pub struct ServerOptions {
    pub max_runs: Option<usize>,
    pub runs_per_app_server: usize,
}

/// The server's state, owned by its one loop.
struct Server {
    initialized: bool,
    max_runs: Option<usize>,
    runs: HashMap<String, LiveRun>,
    pool: Pool,
    // The runs' working directory, and the directory their relative paths name.
    cwd: Option<String>,
    invocation_cwd: PathBuf,
    server_cwd: Option<PathBuf>,
    out: mpsc::UnboundedSender<String>,
    ended: mpsc::UnboundedSender<RunEnded>,
}

/// Serve runs until stdin ends or a stop signal comes; the process's exit code.
pub async fn run(
    options: ServerOptions,
    launch: Launch,
    cwd: Option<String>,
    invocation_cwd: PathBuf,
) -> u8 {
    if cfg!(windows) {
        eprintln!("Error: vibe run-server needs Unix domain sockets; not supported on Windows");
        return 1;
    }
    let mut shutdown = match signal::install() {
        Ok(shutdown) => shutdown,
        Err(error) => {
            eprintln!("Error: {error:#}");
            return 1;
        }
    };
    let (out, writer) = stdout_writer();
    let (ended, mut ended_rx) = mpsc::unbounded_channel();
    let mut server = Server {
        initialized: false,
        max_runs: options.max_runs,
        runs: HashMap::new(),
        server_cwd: launch.cwd.clone(),
        pool: Pool::new(launch, options.runs_per_app_server),
        cwd,
        invocation_cwd,
        out,
        ended,
    };
    tracing::info!("vibe run-server started");
    let mut lines = LineReader::new(tokio::io::stdin(), MAX_MESSAGE);
    let code = loop {
        tokio::select! {
            line = lines.next() => match line {
                None => break 0,
                Some(Line::Oversized(_)) => break protocol_error("a message is too long"),
                Some(Line::Frame(line)) if line.trim_ascii().is_empty() => {}
                Some(Line::Frame(line)) => {
                    if let Err(error) = server.handle(&line) {
                        break protocol_error(&error);
                    }
                }
            },
            () = shutdown.wait() => break 0,
            Some(ended) = ended_rx.recv() => server.announce(ended),
        }
    };
    tracing::info!(runs = server.runs.len(), "vibe run-server ending its runs");
    for live in server.runs.values() {
        live.stop();
    }
    while !server.runs.is_empty() {
        let Some(ended) = ended_rx.recv().await else {
            break;
        };
        server.announce(ended);
    }
    server.pool.shutdown().await;
    drop(server);
    let _ = writer.await;
    code
}

fn protocol_error(error: &str) -> u8 {
    tracing::error!("vibe run-server protocol error, ending: {error}");
    eprintln!("Error: {error}");
    1
}

impl Server {
    fn send(&self, message: Value) {
        let _ = self.out.send(message.to_string());
    }

    /// Answer one request line; Err ends the server on a protocol error.
    fn handle(&mut self, line: &[u8]) -> Result<(), String> {
        let value: Value =
            serde_json::from_slice(line).map_err(|_| "a message is not JSON".to_owned())?;
        let request = protocol::Request::parse(value)?;
        let id = request.id.clone();
        let reply = match self.dispatch(request) {
            Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
            Err(failure) => json!({"jsonrpc": "2.0", "id": id, "error": failure.to_value()}),
        };
        self.send(reply);
        Ok(())
    }

    /// Hand a started run to its own task, announced ended through `ended`.
    fn spawn_run(
        &self,
        spec: run::RunSpec,
        channel: Result<(crate::server::Connected, crate::server::ChannelCloser), String>,
    ) -> LiveRun {
        let (live, stop, hard_stop) = run::start(&spec);
        let run_id = spec.run_id.clone();
        let span = tracing::info_span!("run", run = %run_id);
        let task = tokio::spawn(
            run::serve(spec, channel, self.cwd.clone(), stop, hard_stop).instrument(span),
        );
        let ended = self.ended.clone();
        tokio::spawn(async move {
            let report = match task.await {
                Ok(report) => report,
                Err(join) => run::crashed(run_id, &join.to_string()),
            };
            let _ = ended.send(report);
        });
        live
    }

    /// Free the run's slot, then say it ended.
    fn announce(&mut self, ended: RunEnded) {
        self.runs.remove(&ended.run_id);
        self.send(json!({"jsonrpc": "2.0", "method": "run/ended", "params": ended}));
    }
}

/// One task writes stdout, a line per message, until every sender is gone.
fn stdout_writer() -> (mpsc::UnboundedSender<String>, tokio::task::JoinHandle<()>) {
    let (out, mut messages) = mpsc::unbounded_channel::<String>();
    let writer = tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(mut message) = messages.recv().await {
            message.push('\n');
            if stdout.write_all(message.as_bytes()).await.is_err() {
                return;
            }
            if messages.is_empty() && stdout.flush().await.is_err() {
                return;
            }
        }
        let _ = stdout.flush().await;
    });
    (out, writer)
}
