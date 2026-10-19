use crate::core::config::ImageDeliveryMode;
use crate::core::error::CoreError;
use crate::core::model_input_projection::project_model_input_messages;
use crate::core::step_protocol::ModelMessageUpdate;
use crate::core::wire::message::StoredMessage;

/// Everything the model sees, plus the bookkeeping `HarnessSession` needs to
/// ship it incrementally.
#[derive(Clone, Copy, Debug, Default)]
enum ContextChange {
    #[default]
    Clean,
    Append,
    Replace,
}

#[derive(Clone, Debug)]
pub(crate) struct ModelContext {
    messages: Vec<StoredMessage>,
    revision: u64,
    replacement_revision: u64,
    change: ContextChange,
}

impl PartialEq for ModelContext {
    fn eq(&self, other: &Self) -> bool {
        self.messages == other.messages
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct ModelContextCursor {
    len: usize,
    revision: u64,
}

impl ModelContext {
    pub(crate) fn new(messages: Vec<StoredMessage>) -> Self {
        Self {
            messages,
            revision: 0,
            replacement_revision: 0,
            change: ContextChange::Clean,
        }
    }

    pub(crate) fn restored(messages: Vec<StoredMessage>) -> Self {
        Self {
            messages,
            revision: 0,
            replacement_revision: 0,
            change: ContextChange::Clean,
        }
    }

    pub(crate) fn push(&mut self, message: StoredMessage) {
        self.messages.push(message);
        self.mark_appended();
    }

    pub(crate) fn extend(&mut self, messages: impl IntoIterator<Item = StoredMessage>) {
        let previous_len = self.messages.len();
        self.messages.extend(messages);
        if self.messages.len() != previous_len {
            self.mark_appended();
        }
    }

    pub(crate) fn messages(&self) -> &[StoredMessage] {
        &self.messages
    }

    pub(crate) fn message_count(&self) -> usize {
        self.messages.len()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.messages.is_empty()
    }

    pub(crate) fn revision(&self) -> u64 {
        self.revision
    }

    pub(crate) fn cursor(&self) -> ModelContextCursor {
        ModelContextCursor {
            len: self.messages.len(),
            revision: self.revision,
        }
    }

    pub(crate) fn replace_all(&self, image_delivery: ImageDeliveryMode) -> ModelMessageUpdate {
        ModelMessageUpdate::Replace {
            revision: self.revision,
            messages: project_model_input_messages(&self.messages, image_delivery),
        }
    }

    pub(crate) fn message_update_since(
        &self,
        cursor: Option<ModelContextCursor>,
        image_delivery: ImageDeliveryMode,
    ) -> ModelMessageUpdate {
        if let Some(cursor) = cursor
            && cursor.revision >= self.replacement_revision
            && cursor.len <= self.messages.len()
        {
            return ModelMessageUpdate::Append {
                base_revision: cursor.revision,
                revision: self.revision,
                messages: project_model_input_messages(
                    &self.messages[cursor.len..],
                    image_delivery,
                ),
            };
        }
        self.replace_all(image_delivery)
    }

    pub(crate) fn replace_messages(&mut self, messages: Vec<StoredMessage>) {
        self.messages = messages;
        self.mark_replaced();
    }

    pub(crate) fn mark_projection_replacement(&mut self) {
        self.mark_replaced();
    }

    fn mark_replaced(&mut self) {
        self.change = ContextChange::Replace;
    }

    fn mark_appended(&mut self) {
        if matches!(self.change, ContextChange::Clean) {
            self.change = ContextChange::Append;
        }
    }

    pub(crate) fn commit_revision(&mut self) -> Result<(), CoreError> {
        if matches!(self.change, ContextChange::Clean) {
            return Ok(());
        }
        self.revision = self
            .revision
            .checked_add(1)
            .ok_or_else(|| CoreError::invalid_command("model context revision overflowed"))?;
        if matches!(self.change, ContextChange::Replace) {
            self.replacement_revision = self.revision;
        }
        self.change = ContextChange::Clean;
        Ok(())
    }
}
