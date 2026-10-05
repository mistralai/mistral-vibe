//! The update notifier (Python `cli/update_notifier`): update cache, gateway,
//! and the update use-case.

pub mod brew_oracle;
pub mod cache;
pub mod gateway;
pub mod pep440;
pub mod update;
pub mod uv_oracle;
pub mod uv_pin;
pub mod version;
