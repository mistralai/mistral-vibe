mod system;

pub(crate) use system::{build_system_prompt, configuration_update_changes_system_prompt};

#[cfg(test)]
mod context_snapshot;
