//! `--setup`'s overlapped boot handoff: the wizard opens before the boot,
//! which answers through the flow's boot receiver.

use std::sync::Arc;

use tokio::sync::oneshot;

use crate::server::Client;

/// What the wizard flow submits its writes on: the booted client with the
/// status the wizard seeds from (a pending boot seeds behind the flow's
/// welcome gate instead), or `--setup`'s background boot, still pending
/// behind the welcome screen.
pub enum Boot {
    Ready {
        client: Arc<Client>,
        status: Box<crate::setup::auth::rpc::SetupStatus>,
    },
    Pending(oneshot::Receiver<SetupBoot>),
}

/// The `--setup` background boot's answer through the flow's boot
/// receiver: the booted client with the status the wizard seeds from, or
/// the failure the exit prints once the wizard closed.
pub enum SetupBoot {
    Ready {
        client: Arc<Client>,
        status: Box<crate::setup::auth::rpc::SetupStatus>,
    },
    /// `Client::spawn` failed: the round fails the run (main's error path).
    Spawn(anyhow::Error),
    /// `initialize` failed: "the app-server did not start".
    Init,
    /// The server predates the `setup/*` surface.
    Unavailable,
    /// `setup/status` failed on the wire (the detail is the message).
    Status(String),
}

/// The booted client has no debug shape; the status and failures do.
impl std::fmt::Debug for SetupBoot {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Ready { status, .. } => f.debug_struct("Ready").field("status", status).finish(),
            Self::Spawn(error) => f.debug_tuple("Spawn").field(error).finish(),
            Self::Init => f.write_str("Init"),
            Self::Unavailable => f.write_str("Unavailable"),
            Self::Status(error) => f.debug_tuple("Status").field(error).finish(),
        }
    }
}

impl SetupBoot {
    /// Print the failed boot's message after the wizard closed (the print
    /// restores the alternate screen first, like every close print).
    pub fn print_failure(&self) {
        use crate::setup::exit;
        match self {
            Self::Init => exit::print_setup_failed("the app-server did not start"),
            Self::Unavailable => {
                exit::print_setup_unavailable("this app-server does not support the setup methods")
            }
            Self::Status(error) => exit::print_setup_failed(error),
            _ => {}
        }
    }
}
