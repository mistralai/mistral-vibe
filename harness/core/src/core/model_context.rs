use crate::core::config::ImageDeliveryMode;
use crate::core::error::CoreError;
use crate::core::model_input_projection::project_model_input_messages;
use crate::core::step_protocol::ModelMessageUpdate;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{AssistantPart, Message, StoredMessage, StoredMessageSource};

/// Gives every message except the generated system prompt a Turn group, numbered from zero.
///
/// A message joins the current group unless it starts a new one: an indexed message whose
/// index differs from the group's, or an unindexed user message when the group already has
/// one. Unindexed history, from older checkpoints or imported history, therefore forms one
/// group per user message, and unindexed messages after a Turn join that Turn.
pub(crate) fn assign_turn_indexes(messages: &mut [StoredMessage]) {
    struct Group {
        source_index: Option<u64>,
        has_user: bool,
    }
    let mut group: Option<Group> = None;
    let mut next = 0;
    for message in messages
        .iter_mut()
        .filter(|message| !message.is_generated_system())
    {
        let user = message.source == StoredMessageSource::History
            && matches!(message.message, Message::User { .. });
        let starts_group = match (&group, message.turn_index) {
            (None, _) => true,
            (Some(current), Some(index)) => current
                .source_index
                .map_or(current.has_user, |source| source != index),
            (Some(current), None) => user && current.has_user,
        };
        if starts_group {
            group = Some(Group {
                source_index: None,
                has_user: false,
            });
            next += 1;
        }
        let current = group.as_mut().expect("a group was started above");
        current.source_index = current.source_index.or(message.turn_index);
        current.has_user |= user;
        message.turn_index = Some(next - 1);
    }
}

#[derive(Clone, Copy, Debug, Default)]
enum ContextChange {
    #[default]
    Clean,
    Append,
    Replace,
}

/// Everything the model sees, plus the bookkeeping `HarnessSession` needs to
/// ship it incrementally.
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
    pub(crate) fn new(mut messages: Vec<StoredMessage>) -> Self {
        assign_turn_indexes(&mut messages);
        Self {
            messages,
            revision: 0,
            replacement_revision: 0,
            change: ContextChange::Clean,
        }
    }

    /// Older checkpoints carry messages without a Turn group; restoring assigns them one.
    pub(crate) fn restored(mut messages: Vec<StoredMessage>) -> Self {
        assign_turn_indexes(&mut messages);
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

    /// Adds a message to the latest Turn's group, or to Turn 0 before any Turn exists.
    pub(crate) fn push_in_current_turn(&mut self, mut message: StoredMessage) {
        message.turn_index = Some(self.current_turn_index());
        self.push(message);
    }

    pub(crate) fn extend_current_turn(
        &mut self,
        messages: impl IntoIterator<Item = StoredMessage>,
    ) {
        let turn_index = Some(self.current_turn_index());
        self.extend(messages.into_iter().map(|mut message| {
            message.turn_index = turn_index;
            message
        }));
    }

    /// Replaces history with a compacted form that becomes Turn 0. A Turn in progress
    /// continues in that group, and later Turns count up from it.
    pub(crate) fn replace_with_compacted(&mut self, mut messages: Vec<StoredMessage>) {
        for message in messages
            .iter_mut()
            .filter(|message| !message.is_generated_system())
        {
            message.turn_index = Some(0);
        }
        self.replace_messages(messages);
    }

    /// Adds context that arrives between Turns to the group of the Turn that follows it.
    pub(crate) fn push_before_next_turn(
        &mut self,
        mut message: StoredMessage,
    ) -> Result<(), CoreError> {
        message.turn_index = Some(self.next_turn_index()?);
        self.push(message);
        Ok(())
    }

    pub(crate) fn extend_new_turn(
        &mut self,
        messages: impl IntoIterator<Item = StoredMessage>,
    ) -> Result<(), CoreError> {
        let index = self.next_turn_index()?;
        self.extend(messages.into_iter().map(|mut message| {
            message.turn_index = Some(index);
            message
        }));
        Ok(())
    }

    /// A group holding only injected context is still waiting for its Turn, which joins it.
    fn next_turn_index(&self) -> Result<u64, CoreError> {
        let Some(latest) = self.latest_turn_index() else {
            return Ok(0);
        };
        let waiting_for_turn = self
            .messages
            .iter()
            .rev()
            .filter(|message| !message.is_generated_system())
            .take_while(|message| message.turn_index == Some(latest))
            .all(StoredMessage::is_injected);
        if waiting_for_turn {
            return Ok(latest);
        }
        latest
            .checked_add(1)
            .ok_or_else(|| CoreError::invalid_command("conversation Turn index overflowed"))
    }

    fn current_turn_index(&self) -> u64 {
        self.latest_turn_index().unwrap_or(0)
    }

    fn latest_turn_index(&self) -> Option<u64> {
        self.messages
            .iter()
            .rev()
            .find_map(|message| message.turn_index)
    }

    /// The content a final answer would carry, taken from the current Turn's latest assistant
    /// message: no reasoning, tool calls, or tool results.
    pub(crate) fn latest_assistant_content_in_current_turn(&self) -> Vec<ContentBlock> {
        let turn_index = Some(self.current_turn_index());
        self.messages
            .iter()
            .rev()
            .filter(|message| !message.is_generated_system())
            .take_while(|message| message.turn_index == turn_index)
            .find_map(|message| match &message.message {
                Message::Assistant { content } => Some(content),
                _ => None,
            })
            .map(|content| {
                content
                    .iter()
                    .filter_map(|part| match part {
                        AssistantPart::Content(block) => Some(block.clone()),
                        AssistantPart::Semantic(_) => None,
                    })
                    .collect()
            })
            .unwrap_or_default()
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

    /// The revision, unless the messages or their projection changed since it
    /// was committed.
    pub(crate) fn committed_revision(&self) -> Option<u64> {
        matches!(self.change, ContextChange::Clean).then_some(self.revision)
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
