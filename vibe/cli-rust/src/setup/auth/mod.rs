//! The wizard's headless auth half (Python `vibe/setup/auth`): browser
//! sign-in, tenant resolution, and the `setup/*` RPC calls. The client
//! never persists anything itself — the app-server owns every write.

pub mod browser_sign_in;
pub mod rpc;
pub mod sign_in_flow;
pub mod sign_in_gateway;
pub mod sign_in_url;
pub mod whoami;
