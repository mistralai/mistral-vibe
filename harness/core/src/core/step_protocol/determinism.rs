use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct DeterminismContext {
    pub time_unix_ms: u64,
    pub random_seed: u32,
}
