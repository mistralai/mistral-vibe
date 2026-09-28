use serde::{Deserialize, Serialize};

use crate::core::config::HarnessConfig;
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

    pub(crate) fn restore(self, config: HarnessConfig) -> Result<HarnessState, String> {
        match self.0 {
            CheckpointVersion::V1(checkpoint) => checkpoint.restore(config),
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
