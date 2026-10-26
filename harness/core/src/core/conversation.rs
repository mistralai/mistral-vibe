use std::collections::BTreeSet;
use std::num::NonZeroU32;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::core::model_context::assign_turn_indexes;
use crate::core::wire::content::text_content;
use crate::core::wire::message::{
    AssistantPart, AssistantSemanticPart, Message, StoredMessage, StoredMessageSource, ToolOutcome,
};

/// Portable conversation only: group indexes carry no execution identity.
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ConversationSeed {
    pub messages: Vec<ConversationMessage>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ConversationMessage {
    pub message: Message,
    pub turn_index: Option<u64>,
    pub source: ConversationSource,
}

/// What a spawned child starts from: `"fresh"`, or the parent's latest whole
/// Turns with `{"mode": "forked", "turns": N}` or `{"mode": "forked", "turns": "all"}`.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(untagged)]
#[schemars(extend("x-harness-one-of" = true))]
pub(crate) enum SpawnContext {
    /// A new conversation with only the child's task.
    Fresh(FreshMode),
    /// A copy of the parent's conversation before the child's task.
    Forked(ForkedContext),
}

impl Default for SpawnContext {
    fn default() -> Self {
        Self::Fresh(FreshMode::Fresh)
    }
}

impl SpawnContext {
    /// The accepted values, in words for error messages.
    pub(crate) const ACCEPTED: &str =
        r#""fresh" or {"mode": "forked", "turns": <positive integer> or "all"}"#;

    /// Reads a context sent across a language binding.
    pub(crate) fn from_json(value: &str) -> Result<Self, String> {
        serde_json::from_str(value)
            .map_err(|_| format!("context must be {}, not {value}", Self::ACCEPTED))
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(crate) enum FreshMode {
    #[default]
    Fresh,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ForkedContext {
    pub mode: ForkedMode,
    /// How many of the parent's latest whole Turns to copy, the current one included: a positive integer, or `"all"` (the default), which also lets the child reuse the parent's cached prompt prefix.
    #[serde(default)]
    pub turns: ForkedTurns,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(crate) enum ForkedMode {
    Forked,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(untagged)]
#[schemars(extend("x-harness-one-of" = true))]
pub(crate) enum ForkedTurns {
    Latest(#[schemars(range(max = 4_294_967_295u32))] NonZeroU32),
    All(AllTurns),
}

impl Default for ForkedTurns {
    fn default() -> Self {
        Self::All(AllTurns::All)
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(crate) enum AllTurns {
    #[default]
    All,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ConversationSource {
    History,
    Injection,
}

impl ConversationSeed {
    pub(super) fn import(self) -> Result<Vec<StoredMessage>, String> {
        let mut previous = None;
        let mut messages = Vec::with_capacity(self.messages.len());
        for item in self.messages {
            if matches!(item.message, Message::System { .. }) {
                return Err("conversation seed cannot contain system messages".into());
            }
            item.message
                .validate()
                .map_err(|error| error.detail().to_owned())?;
            if let Some(index) = item.turn_index {
                if previous.is_some_and(|previous| index < previous) {
                    return Err("conversation Turn groups must be in order".into());
                }
                previous = Some(index);
            }
            messages.push(StoredMessage {
                message: item.message,
                turn_index: item.turn_index,
                source: match item.source {
                    ConversationSource::History => StoredMessageSource::History,
                    ConversationSource::Injection => StoredMessageSource::Injection,
                },
            });
        }
        if complete_prefix_len(&messages.iter().collect::<Vec<_>>()) != messages.len() {
            return Err("conversation seed contains an incomplete tool exchange".into());
        }
        // Imported history counts as Turns, so a later forked spawn can count it.
        assign_turn_indexes(&mut messages);
        Ok(messages)
    }
}

/// Copies whole Turns for a new agent. When the last assistant message's tool batch is still
/// running, that message stays in the copy: each call that already finished keeps its result
/// from `finished_results`, and each call still running is closed by `pending_call_result`.
/// The new agent sees that the source made the calls, and its prompt keeps the source's prefix
/// up to and including that message.
pub(super) fn export(
    messages: &[StoredMessage],
    context: SpawnContext,
    finished_results: &[&Message],
    pending_call_result: &str,
) -> Result<ConversationSeed, String> {
    let limit = match context {
        SpawnContext::Fresh(_) => return Ok(ConversationSeed::default()),
        SpawnContext::Forked(ForkedContext {
            turns: ForkedTurns::All(_),
            ..
        }) => usize::MAX,
        SpawnContext::Forked(ForkedContext {
            turns: ForkedTurns::Latest(turns),
            ..
        }) => usize::try_from(turns.get()).unwrap_or(usize::MAX),
    };
    let history = messages
        .iter()
        .filter(|message| {
            !message.is_generated_system() && !matches!(message.message, Message::System { .. })
        })
        .collect::<Vec<_>>();
    let complete = complete_prefix_len(&history);
    let closed = close_pending_calls(&history[complete..], finished_results, pending_call_result)
        .unwrap_or_default();
    let history = history[..complete]
        .iter()
        .copied()
        .chain(&closed)
        .collect::<Vec<_>>();
    let groups = history
        .iter()
        .filter_map(|message| message.turn_index)
        .collect::<BTreeSet<_>>();
    let selected = groups
        .iter()
        .rev()
        .take(limit)
        .copied()
        .collect::<BTreeSet<_>>();
    Ok(ConversationSeed {
        messages: history
            .iter()
            .filter(|message| {
                message
                    .turn_index
                    .is_some_and(|index| selected.contains(&index))
            })
            .map(|message| ConversationMessage {
                message: message.message.clone(),
                turn_index: message.turn_index,
                source: if message.is_injected() {
                    ConversationSource::Injection
                } else {
                    ConversationSource::History
                },
            })
            .collect(),
    })
}

/// Closes a trailing tool batch that is still running: one assistant message with calls. Core
/// commits the batch's results to the conversation only when every call has finished, so the
/// results of finished calls come from `finished_results`, in call order. Returns `None` for
/// any other tail, which the export then leaves out.
fn close_pending_calls(
    tail: &[&StoredMessage],
    finished_results: &[&Message],
    pending_call_result: &str,
) -> Option<Vec<StoredMessage>> {
    let [call_message] = tail else {
        return None;
    };
    let Message::Assistant { content } = &call_message.message else {
        return None;
    };
    let calls = content
        .iter()
        .filter_map(|part| match part {
            AssistantPart::Semantic(AssistantSemanticPart::ToolCall { id, name, .. }) => {
                Some((id.as_str(), name.as_str()))
            }
            _ => None,
        })
        .collect::<Vec<_>>();
    if calls.is_empty() {
        return None;
    }
    let results = calls.into_iter().map(|(id, name)| {
        let finished = finished_results.iter().find(
            |message| matches!(message, Message::Tool { tool_call_id, .. } if tool_call_id == id),
        );
        match finished {
            Some(message) => StoredMessage {
                message: (*message).clone(),
                source: StoredMessageSource::History,
                turn_index: call_message.turn_index,
            },
            None => StoredMessage {
                message: Message::tool(
                    id.to_owned(),
                    name.to_owned(),
                    ToolOutcome::Success,
                    text_content(pending_call_result),
                    None,
                ),
                source: StoredMessageSource::Injection,
                turn_index: call_message.turn_index,
            },
        }
    });
    Some(
        std::iter::once((*call_message).clone())
            .chain(results)
            .collect(),
    )
}

fn complete_prefix_len(messages: &[&StoredMessage]) -> usize {
    let mut pending = BTreeSet::new();
    let mut complete = 0;
    for (index, stored) in messages.iter().enumerate() {
        match &stored.message {
            Message::Assistant { content } if pending.is_empty() => {
                for part in content {
                    if let AssistantPart::Semantic(AssistantSemanticPart::ToolCall { id, .. }) =
                        part
                    {
                        pending.insert(id.as_str());
                    }
                }
            }
            Message::Tool { tool_call_id, .. } => {
                if !pending.remove(tool_call_id.as_str()) {
                    break;
                }
            }
            _ if !pending.is_empty() => break,
            _ => {}
        }
        if pending.is_empty() {
            complete = index + 1;
        }
    }
    complete
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::{assistant_tool, assistant_tools};

    fn history_seed(messages: Vec<(Message, Option<u64>)>) -> ConversationSeed {
        ConversationSeed {
            messages: messages
                .into_iter()
                .map(|(message, turn_index)| ConversationMessage {
                    message,
                    turn_index,
                    source: ConversationSource::History,
                })
                .collect(),
        }
    }

    /// *Prepare*: A seed whose otherwise valid history contains a system message.
    /// *Do*: Import the seed.
    /// *Assert*: Import rejects it, so a parent's instructions cannot reach a child.
    #[test]
    fn import_rejects_system_messages() {
        // Prepare
        let seed = ConversationSeed {
            messages: vec![ConversationMessage {
                message: Message::system_text("PARENT INSTRUCTIONS"),
                turn_index: None,
                source: ConversationSource::History,
            }],
        };

        // Do
        let result = seed.import();

        // Assert
        assert_eq!(
            result.unwrap_err(),
            "conversation seed cannot contain system messages"
        );
    }

    /// *Prepare*: A seed whose second Turn group has a lower index than the first.
    /// *Do*: Import the seed.
    /// *Assert*: Import rejects it, because later selections count groups in order.
    #[test]
    fn import_rejects_turn_groups_that_decrease() {
        // Prepare
        let seed = history_seed(vec![
            (Message::user_text("first request"), Some(7)),
            (Message::user_text("second request"), Some(2)),
        ]);

        // Do
        let result = seed.import();

        // Assert
        assert_eq!(
            result.unwrap_err(),
            "conversation Turn groups must be in order"
        );
    }

    /// *Prepare*: A seed with an incomplete tool exchange: an unfinished call, an orphan
    /// result, a parallel batch with one result missing, or a user message between a
    /// call and its result.
    /// *Do*: Import the seed.
    /// *Assert*: Import rejects it, so a child never starts from a call the model cannot
    /// pair with its result.
    #[rstest::rstest]
    #[case::unfinished_call(vec![assistant_tool("read_file", serde_json::json!({"path": "notes.txt"}))])]
    #[case::orphan_result(vec![Message::tool_success_text("call-1".into(), "read_file".into(), "notes")])]
    #[case::partial_batch(vec![
        assistant_tools(vec![
            ("call-a", "read_file", serde_json::json!({"path": "a.txt"})),
            ("call-b", "read_file", serde_json::json!({"path": "b.txt"})),
        ]),
        Message::tool_success_text("call-a".into(), "read_file".into(), "a contents"),
    ])]
    #[case::interleaved_user(vec![
        assistant_tool("read_file", serde_json::json!({"path": "notes.txt"})),
        Message::user_text("another request before the tool result"),
        Message::tool_success_text("call-1".into(), "read_file".into(), "notes"),
    ])]
    fn import_rejects_an_incomplete_tool_exchange(#[case] messages: Vec<Message>) {
        // Prepare
        let seed = history_seed(
            messages
                .into_iter()
                .map(|message| (message, Some(0)))
                .collect(),
        );

        // Do
        let result = seed.import();

        // Assert
        assert_eq!(
            result.unwrap_err(),
            "conversation seed contains an incomplete tool exchange"
        );
    }

    fn all_turns() -> SpawnContext {
        SpawnContext::Forked(ForkedContext {
            mode: ForkedMode::Forked,
            turns: ForkedTurns::All(AllTurns::All),
        })
    }

    fn stored(messages: Vec<Message>) -> Vec<StoredMessage> {
        messages
            .into_iter()
            .map(|message| StoredMessage {
                message,
                source: StoredMessageSource::History,
                turn_index: Some(0),
            })
            .collect()
    }

    /// *Prepare*: A Turn whose last assistant message made two calls. The batch still runs,
    /// and the second call already finished.
    /// *Do*: Export the conversation with the batch's finished result.
    /// *Assert*: The copy keeps the call message, closes the running call with the given text,
    /// keeps the finished result in call order, and imports as a complete exchange.
    #[test]
    fn export_closes_calls_still_running() {
        // Prepare
        let messages = stored(vec![
            Message::user_text("read both files"),
            assistant_tools(vec![
                ("call-a", "read_file", serde_json::json!({"path": "a.txt"})),
                ("call-b", "read_file", serde_json::json!({"path": "b.txt"})),
            ]),
        ]);
        let finished =
            Message::tool_success_text("call-b".into(), "read_file".into(), "b contents");

        // Do
        let seed = export(&messages, all_turns(), &[&finished], "STILL RUNNING").unwrap();

        // Assert
        let exported = seed
            .messages
            .iter()
            .map(|item| (item.message.clone(), item.turn_index))
            .collect::<Vec<_>>();
        let mut expected = messages
            .iter()
            .map(|stored| (stored.message.clone(), stored.turn_index))
            .collect::<Vec<_>>();
        expected.push((
            Message::tool_success_text("call-a".into(), "read_file".into(), "STILL RUNNING"),
            Some(0),
        ));
        expected.push((finished, Some(0)));
        assert_eq!(exported, expected);
        assert!(matches!(
            seed.messages[2].source,
            ConversationSource::Injection
        ));
        assert!(matches!(
            seed.messages[3].source,
            ConversationSource::History
        ));
        assert_eq!(seed.import().unwrap().len(), 4);
    }

    /// *Prepare*: A call followed by a user message before its result.
    /// *Do*: Export the conversation.
    /// *Assert*: The copy stops before the call, because only a trailing exchange can be closed.
    #[test]
    fn export_leaves_out_an_interrupted_exchange() {
        // Prepare
        let messages = stored(vec![
            Message::user_text("read the notes"),
            assistant_tool("read_file", serde_json::json!({"path": "notes.txt"})),
            Message::user_text("another request before the tool result"),
        ]);

        // Do
        let seed = export(&messages, all_turns(), &[], "STILL RUNNING").unwrap();

        // Assert
        assert_eq!(seed.messages.len(), 1);
        assert_eq!(
            seed.messages[0].message,
            Message::user_text("read the notes")
        );
    }
}
