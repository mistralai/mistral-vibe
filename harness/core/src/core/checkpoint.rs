use serde::{Deserialize, Serialize};

use crate::core::config::{ContextSettings, HarnessConfig};
use crate::core::state::HarnessState;

mod v1;

#[cfg(test)]
mod fixture_tests;

#[cfg(test)]
mod tests;

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(transparent)]
pub(crate) struct Checkpoint(CheckpointVersion);

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(untagged)]
enum CheckpointVersion {
    V1(v1::CheckpointV1),
}

impl Checkpoint {
    pub(crate) fn capture(state: &HarnessState) -> Result<Self, String> {
        v1::CheckpointV1::capture(state).map(|checkpoint| Self(CheckpointVersion::V1(checkpoint)))
    }

    /// Restores the checkpoint under `config` without a move.
    #[cfg(test)]
    pub(crate) fn restore(self, config: HarnessConfig) -> Result<HarnessState, String> {
        self.restore_moved_from(config, None)
    }

    /// Restores the checkpoint under `config`. With `moved_from`, the context
    /// settings the checkpoint was taken under, the Session first moves to
    /// `config` by the rule a reconfiguration applies.
    pub(crate) fn restore_moved_from(
        self,
        config: HarnessConfig,
        moved_from: Option<&ContextSettings>,
    ) -> Result<HarnessState, String> {
        match self.0 {
            CheckpointVersion::V1(checkpoint) => checkpoint.restore(config, moved_from),
        }
    }
}

#[derive(Deserialize)]
struct CheckpointVersionProbe {
    checkpoint_version: u64,
}

pub(crate) fn decode_checkpoint(value: &str) -> Result<Checkpoint, String> {
    let version: CheckpointVersionProbe = serde_json::from_str(value)
        .map_err(|error| format!("invalid session checkpoint JSON: {error}"))?;
    match version.checkpoint_version {
        version if version == u64::from(v1::CHECKPOINT_VERSION) => serde_json::from_str(value)
            .map(|checkpoint| Checkpoint(CheckpointVersion::V1(checkpoint)))
            .map_err(|error| format!("invalid session checkpoint JSON: {error}")),
        version => Err(format!("unsupported session checkpoint version {version}")),
    }
}
