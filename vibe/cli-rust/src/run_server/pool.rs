//! The app-servers runs share: started as runs need them, at most K runs each, exited once idle.

use std::sync::Arc;

use serde_json::Value;
use tokio::sync::watch;
use tokio::task::JoinSet;

use crate::server::{ChannelCloser, Connected, Connection, Launch, MuxClient};

pub struct Pool {
    launch: Launch,
    per_server: usize,
    servers: Vec<Arc<MuxClient>>,
    // Each app-server's task, which shuts it down once it serves no run.
    retirements: JoinSet<()>,
    // Dropped when the pool shuts down, ending the tasks still waiting.
    closing: Option<watch::Sender<()>>,
}

impl Pool {
    pub fn new(launch: Launch, per_server: usize) -> Self {
        Self {
            launch,
            per_server: per_server.max(1),
            servers: Vec::new(),
            retirements: JoinSet::new(),
            closing: Some(watch::channel(()).0),
        }
    }

    /// Open a channel for a run on the busiest app-server with room, starting
    /// one when none has: packing runs lets a spare app-server drain and exit.
    /// Only live runs count: a run's channel awaiting its close
    /// acknowledgement takes no room, or runs ending as others start would
    /// start an app-server that then never goes idle.
    pub fn place(&mut self, open: &Value) -> Result<(Connected, ChannelCloser), String> {
        let connected = loop {
            self.servers
                .retain(|server| server.accepts_runs() || server.load() > 0);
            let server = self
                .servers
                .iter()
                .filter(|server| server.accepts_runs() && server.live_runs() < self.per_server)
                .min_by_key(|server| self.per_server - server.live_runs())
                .cloned();
            let server = match server {
                Some(server) => server,
                None => self.start()?,
            };
            if let Some(connected) = server.try_open_channel(open) {
                break connected;
            }
        };
        let Connection::Channel(guard) = &connected.connection else {
            unreachable!("a multiplexed app-server opens channels");
        };
        let closer = guard.closer();
        Ok((connected, closer))
    }

    fn start(&mut self) -> Result<Arc<MuxClient>, String> {
        let server = MuxClient::spawn(&self.launch)
            .map(Arc::new)
            .map_err(|error| format!("start an app-server: {error:#}"))?;
        tracing::info!(
            pid = server.pid(),
            servers = self.servers.len() + 1,
            "Started an app-server for runs"
        );
        self.servers.push(server.clone());
        while self.retirements.try_join_next().is_some() {}
        let closing = self.closing.as_ref().map(watch::Sender::subscribe);
        self.retirements
            .spawn(retire_when_idle(server.clone(), closing));
        Ok(server)
    }

    /// Shut every app-server down, together.
    pub async fn shutdown(&mut self) {
        self.closing = None;
        let servers = std::mem::take(&mut self.servers);
        let shutdowns = servers.iter().map(|server| server.shutdown());
        futures::future::join_all(shutdowns).await;
        while self.retirements.join_next().await.is_some() {}
    }
}

/// Shut the app-server down once its last run's channel closes.
async fn retire_when_idle(server: Arc<MuxClient>, closing: Option<watch::Receiver<()>>) {
    let Some(mut closing) = closing else {
        return;
    };
    tokio::select! {
        biased;
        () = server.idle() => {
            tracing::info!(pid = server.pid(), "Shutting down an idle app-server");
            server.shutdown().await;
        }
        _ = closing.changed() => {}
    }
}
