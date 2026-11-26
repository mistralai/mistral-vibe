//! How a run reaches its app-server: a process of its own, or a channel of a shared one.

use anyhow::{Context, Result};
use tokio::sync::mpsc;

use super::{ChannelGuard, ChildHandle, Client, Launch, Notification};

/// Where a run's app-server comes from.
pub enum Connect {
    /// Spawn a dedicated app-server, as `vibe -p` does.
    Spawn(Launch),
    /// A channel already opened on a shared app-server, as `vibe run-server` runs do.
    Channel(Box<Connected>),
}

/// What keeps a run's app-server alive; dropping it ends the run's side of it.
pub enum Connection {
    Child(ChildHandle),
    Channel(ChannelGuard),
}

/// A connected client, the guard of its transport, and its notifications.
pub struct Connected {
    pub client: Client,
    pub connection: Connection,
    pub notifications: mpsc::Receiver<Notification>,
}

impl Connect {
    pub async fn connect(self) -> Result<Connected> {
        match self {
            Connect::Spawn(launch) => {
                let (client, child, notifications, _crash) =
                    Client::spawn(launch).await.context("spawn app-server")?;
                Ok(Connected {
                    client,
                    connection: Connection::Child(child),
                    notifications,
                })
            }
            Connect::Channel(connected) => Ok(*connected),
        }
    }
}
