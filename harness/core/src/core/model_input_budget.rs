use serde::Serialize;

use crate::core::config::ImageDeliveryMode;
use crate::core::error::CoreError;
use crate::core::model_input_projection::{
    project_model_input_content, project_model_input_messages,
};
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::StoredMessage;

pub(crate) const APPROX_BYTES_PER_TOKEN: usize = 4;

pub(crate) fn estimate_model_input_tokens(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    image_delivery: ImageDeliveryMode,
) -> Result<u64, CoreError> {
    let messages = project_model_input_messages(messages, image_delivery);
    estimate_serialized_tokens(&(messages, tools), "model input")
}

pub(crate) fn estimate_model_content_tokens(
    block: &ContentBlock,
    label: &str,
    image_delivery: ImageDeliveryMode,
) -> Result<u64, CoreError> {
    let block = project_model_input_content(block, image_delivery);
    estimate_serialized_tokens(&block, label)
}

pub(crate) fn estimate_serialized_tokens(
    value: &impl Serialize,
    label: &str,
) -> Result<u64, CoreError> {
    let bytes = serde_json::to_vec(value)
        .map_err(|error| CoreError::invariant(format!("{label} cannot be measured: {error}")))?
        .len();
    Ok(u64::try_from(bytes.div_ceil(APPROX_BYTES_PER_TOKEN)).unwrap_or(u64::MAX))
}
