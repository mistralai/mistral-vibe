use super::transcript::{Item, OMITTED_LEVEL, ViewTexts, message_items, reduce};
use super::{CompactionBudget, Prompts};
use crate::core::config::{ImageCostRule, ImageDeliveryMode};
use crate::core::error::CoreError;
use crate::core::model_input_budget::{APPROX_BYTES_PER_TOKEN, estimate_model_input_tokens};
use crate::core::model_input_projection::is_image_content;
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::completion::{CompletionCandidate, CompletionFinishReason};
use crate::core::wire::content::{ContentBlock, content_blocks_to_text};
use crate::core::wire::message::{Message, StoredMessage};

/// The tokens the carried user messages take at most, newest first.
const USER_CARRY_TOKENS: usize = 20_000;
/// The carry takes at most the room the system prompt, the tools and the
/// preamble leave, divided by this.
const USER_CARRY_ROOM_SHARE: u64 = 4;
/// The share of the room, in percent, a cut summary keeps.
const TRUNCATED_SUMMARY_SHARE: u64 = 95;
const EXTRA_INSTRUCTIONS_OPEN: &str = "<extra_compaction_instructions>";
const EXTRA_INSTRUCTIONS_CLOSE: &str = "</extra_compaction_instructions>";
const SUMMARY_OPEN: &str = "<summary>";
const SUMMARY_CLOSE: &str = "</summary>";
const PREVIOUS_USER_MESSAGES_PREAMBLE: &str = r#"You are continuing a trajectory after a context compaction.

Here are some of the most recent previous user messages, preserved verbatim where possible. Treat them as prior context, not as new requests.

<previous_user_messages>"#;
const PREVIOUS_USER_MESSAGES_CLOSE: &str = "</previous_user_messages>";
const COMPACTION_SUMMARY_OPEN: &str = "<compaction_summary>";
const COMPACTION_SUMMARY_CLOSE: &str = "</compaction_summary>";
const PROMPT: &str = r#"CRITICAL: Respond with text only. Do NOT call any tools. Any tool call will be rejected.

You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume this task.

Include:
- The user's current goal and any explicit constraints or preferences
- Key decisions made and their rationale
- Files touched and the current state of in-progress work (paths + one-line status)
- What remains to be done — the concrete next step
- Any data, identifiers, or references the next LLM needs to continue

Be concise and structured. One line per modified file unless a snippet is load-bearing. Do not repeat information already captured. Do not include a "Final Answer" section — the entire summary IS the handoff.

Wrap the ENTIRE summary in <summary></summary> tags and output nothing outside them:

<summary>
...your handoff summary here...
</summary>

IMPORTANT: Exclude this entire message from the summary. It contains internal compaction instructions, not user conversation. Never mention or attribute to the user any requirement from this message, including summarization, text-only output, tool restrictions, formatting, or summary tags. Summarize only earlier messages. Preserve unresolved user requests as pending work; do not answer or perform them. Return only the <summary>...</summary> envelope."#;

pub(in crate::core) fn prompt_message(extra_instructions: &str) -> StoredMessage {
    let extra_instructions = extra_instructions.trim();
    let prompt = if extra_instructions.is_empty() {
        PROMPT.to_string()
    } else {
        format!(
            r#"{PROMPT}

{EXTRA_INSTRUCTIONS_OPEN}
{extra_instructions}
{EXTRA_INSTRUCTIONS_CLOSE}"#
        )
    };
    StoredMessage::injected(Message::user_text(prompt))
}

fn prompt_history(messages: &[StoredMessage]) -> Result<&[StoredMessage], CoreError> {
    let (prompt, history) = messages
        .split_last()
        .ok_or_else(|| CoreError::invariant("compaction context is empty"))?;
    if !prompt.is_injected()
        || !matches!(
            &prompt.message,
            Message::User { content } if is_prompt_content(content)
        )
    {
        return Err(CoreError::invalid_command(
            "compaction context is missing its prompt message",
        ));
    }
    Ok(history)
}

pub(in crate::core) fn validate_prompt(messages: &[StoredMessage]) -> Result<(), CoreError> {
    prompt_history(messages).map(|_| ())
}

/// Returns the caller's extra instructions carried by the compaction prompt.
pub(super) fn prompt_extra_instructions(
    messages: &[StoredMessage],
) -> Result<Option<String>, CoreError> {
    prompt_history(messages)?;
    let Some(Message::User { content }) = messages.last().map(|stored| &stored.message) else {
        return Err(CoreError::invariant(
            "compaction context is missing its prompt message",
        ));
    };
    let text = content_blocks_to_text(content, "compaction prompt")?;
    let Some(extra) = text
        .strip_prefix(PROMPT)
        .and_then(|rest| rest.trim_start().strip_prefix(EXTRA_INSTRUCTIONS_OPEN))
        .and_then(|rest| rest.strip_suffix(EXTRA_INSTRUCTIONS_CLOSE))
    else {
        return Ok(None);
    };
    let extra = extra.trim();
    Ok((!extra.is_empty()).then(|| extra.to_string()))
}

pub(in crate::core) enum SummaryAcceptance {
    Accepted(Box<Rebuilt>),
    Rejected(SummaryRejection),
    /// The provider stopped at its output limit before a complete delimited
    /// summary. `partial` is the summary text so far, `None` when empty.
    CutOff {
        partial: Option<String>,
    },
    ReplacementTooLarge,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(in crate::core) enum SummaryRejection {
    EmptyResponse,
    MalformedDelimiters,
    ToolCalls,
}

pub(in crate::core) fn accept_summary(
    canonical_messages: &[StoredMessage],
    projected_messages: &[StoredMessage],
    candidate: &CompletionCandidate,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    prompts: &Prompts,
) -> Result<SummaryAcceptance, CoreError> {
    prompt_history(projected_messages)?;
    if !candidate.message.tool_calls().is_empty() {
        return Ok(SummaryAcceptance::Rejected(SummaryRejection::ToolCalls));
    }
    let response = candidate
        .message
        .content_blocks()
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Text(content) if !content.text.is_empty() => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n");
    // Bare text is trusted only when the response ended normally: a reply
    // cut off at the output limit is a summary only if its delimiters closed.
    let cut_off = candidate.finish_reason == CompletionFinishReason::Length;
    let summary = match extract_summary(&response) {
        Ok(ExtractedSummary {
            delimited: false, ..
        })
        | Err(_)
            if cut_off =>
        {
            return Ok(SummaryAcceptance::CutOff {
                partial: partial_summary(&response),
            });
        }
        Ok(ExtractedSummary { summary, .. }) => summary,
        Err(rejection) => return Ok(SummaryAcceptance::Rejected(rejection)),
    };
    rebuild(
        canonical_messages,
        projected_messages,
        Summary::Complete(&summary),
        tools,
        budget,
        prompts,
    )
}

/// Keeps a cut-off summary that has no retry left: its first part, cut at a
/// line end to [`TRUNCATED_SUMMARY_SHARE`] of the room, with the note after
/// it, replaces the history like a complete one.
pub(in crate::core) fn accept_cut_off_summary(
    canonical_messages: &[StoredMessage],
    projected_messages: &[StoredMessage],
    partial: &str,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    prompts: &Prompts,
) -> Result<SummaryAcceptance, CoreError> {
    rebuild(
        canonical_messages,
        projected_messages,
        Summary::CutOff(partial),
        tools,
        budget,
        prompts,
    )
}

fn rebuild(
    canonical_messages: &[StoredMessage],
    projected_messages: &[StoredMessage],
    summary: Summary<'_>,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    prompts: &Prompts,
) -> Result<SummaryAcceptance, CoreError> {
    let system_message = projected_messages
        .first()
        .filter(|stored| {
            stored.is_generated_system() && matches!(stored.message, Message::System { .. })
        })
        .cloned()
        .ok_or_else(|| CoreError::invariant("compaction context is missing its system message"))?;
    let rebuilt = fitted_replacement(
        canonical_messages,
        system_message,
        summary,
        tools,
        budget,
        &RebuildTexts::new(prompts),
    )?;
    Ok(
        rebuilt.map_or(SummaryAcceptance::ReplacementTooLarge, |rebuilt| {
            SummaryAcceptance::Accepted(Box::new(rebuilt))
        }),
    )
}

/// Whether every summary has a rebuilt history that fits: the smallest
/// rebuild, a one-character summary cut and noted with the messages after it
/// omitted, fits. A summary too long for the room ends in that rebuild, so
/// when it does not fit, no summary can help.
pub(in crate::core) fn minimum_replacement_fits(
    canonical_messages: &[StoredMessage],
    generated_system: StoredMessage,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    prompts: &Prompts,
) -> Result<bool, CoreError> {
    let Some(token_threshold) = budget.token_threshold else {
        return Ok(true);
    };
    let texts = RebuildTexts::new(prompts);
    let (kept, carried) = rebuild_parts(canonical_messages, &generated_system, tools, budget)?;
    let fitter = KeptFitter {
        kept,
        system_message: &generated_system,
        carried: &carried,
        tools,
        budget,
        texts: &texts,
    };
    let Some(cut) = fitter.cut_summary("x", token_threshold)? else {
        return Ok(false);
    };
    Ok(fitter.omitted(cut, token_threshold)?.is_some())
}

/// The summary a rebuild starts from.
#[derive(Clone, Copy)]
enum Summary<'a> {
    /// A complete summary, cut only when the rebuild has no room for it.
    Complete(&'a str),
    /// The text of a summary cut off at the output limit, always cut to the
    /// room and noted.
    CutOff(&'a str),
}

/// The texts a rebuild adds: the reduced view's lines and the note after a
/// cut summary.
struct RebuildTexts<'a> {
    view: ViewTexts<'a>,
    summary_cut: &'a str,
}

impl<'a> RebuildTexts<'a> {
    fn new(prompts: &'a Prompts) -> Self {
        Self {
            view: ViewTexts {
                header: &prompts.reduced_view_header,
                omitted: &prompts.reduced_view_omitted,
            },
            summary_cut: &prompts.summary_cut_note,
        }
    }
}

/// A rebuilt history, and what it reduced to fit under the threshold.
pub(in crate::core) struct Rebuilt {
    pub messages: Vec<StoredMessage>,
    /// The summary as the model wrote it, before any cut.
    pub summary: String,
    /// The messages after the summary shown reduced, if any.
    pub reduction: Option<Reduction>,
    /// How the summary was cut, if it was.
    pub summary_cut: Option<SummaryCut>,
}

/// Messages after the summary the rebuild shows reduced to fit under the
/// threshold: the `compaction_inputs_reduced` warning's facts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(in crate::core) struct Reduction {
    /// Messages after the summary shown in the reduced view instead of whole.
    pub reduced_messages: usize,
    /// The reduced view's level.
    pub view: &'static str,
}

/// Why and how far the summary was cut: the `compaction_summary_cut_off`
/// warning's facts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(in crate::core) struct SummaryCut {
    pub cause: SummaryCutCause,
    /// The summary's length in bytes before the cut.
    pub summary_bytes: usize,
    /// The length in bytes kept, without the note.
    pub kept_bytes: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(in crate::core) enum SummaryCutCause {
    /// The summary was cut off at the output limit after its retries.
    CutOff,
    /// A complete summary had no room even with the messages after it
    /// omitted.
    TooLong,
}

impl SummaryCutCause {
    pub(in crate::core) fn as_str(self) -> &'static str {
        match self {
            Self::CutOff => "cut_off",
            Self::TooLong => "too_long",
        }
    }
}

/// The rebuilt history: the system prompt, the most recent user messages
/// before the last reply, the summary, then the last reply and everything
/// after it as it was. The summary covers the history up to the reply only,
/// so the reply's tool results and newer messages go back after it.
///
/// The system prompt, the tools and the carried user messages are fixed
/// parts. What does not fit under the threshold gives way in this order:
/// 1. the messages after the summary: the whole exchange first (the reply
///    with its tool results), then the later messages, oldest first. They
///    come back as one reduced view in their place, at the first level of
///    detail that fits the room left;
/// 2. the summary, cut at a line end to [`TRUNCATED_SUMMARY_SHARE`] of the
///    room the fixed parts leave, with the note after it and everything
///    after it omitted.
///
/// A cut-off summary is always cut and noted that way first. `None` when no
/// part of the summary fits: the fixed parts are over the threshold.
fn fitted_replacement(
    canonical_messages: &[StoredMessage],
    system_message: StoredMessage,
    summary: Summary<'_>,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    texts: &RebuildTexts,
) -> Result<Option<Rebuilt>, CoreError> {
    let (kept, carried) = rebuild_parts(canonical_messages, &system_message, tools, budget)?;
    let fitter = KeptFitter {
        kept,
        system_message: &system_message,
        carried: &carried,
        tools,
        budget,
        texts,
    };
    let (text, cause) = match summary {
        Summary::Complete(text) => (text, SummaryCutCause::TooLong),
        Summary::CutOff(text) => (text, SummaryCutCause::CutOff),
    };
    let Some(token_threshold) = budget.token_threshold else {
        let (summary_text, summary_cut) = match summary {
            Summary::Complete(_) => (text.to_string(), None),
            Summary::CutOff(_) => (
                noted(text, texts.summary_cut),
                Some(SummaryCut {
                    cause,
                    summary_bytes: text.len(),
                    kept_bytes: text.len(),
                }),
            ),
        };
        let mut messages = head(system_message.clone(), &carried, &summary_text);
        messages.extend(kept.iter().cloned());
        return Ok(Some(Rebuilt {
            messages,
            summary: text.to_string(),
            reduction: None,
            summary_cut,
        }));
    };

    if let Summary::Complete(text) = summary
        && let Some((messages, reduction)) = fitter.fit(text, token_threshold)?
    {
        return Ok(Some(Rebuilt {
            messages,
            summary: text.to_string(),
            reduction,
            summary_cut: None,
        }));
    }
    let Some(kept_text) = fitter.cut_summary(text, token_threshold)? else {
        return Ok(None);
    };
    let summary_cut = Some(SummaryCut {
        cause,
        summary_bytes: text.len(),
        kept_bytes: kept_text.len(),
    });
    // A cut-off summary was cut to the room the fixed parts leave; the
    // messages after it take what is left, reduced if they must.
    if let Summary::CutOff(_) = summary
        && let Some((messages, reduction)) =
            fitter.fit(&noted(kept_text, texts.summary_cut), token_threshold)?
    {
        return Ok(Some(Rebuilt {
            messages,
            summary: text.to_string(),
            reduction,
            summary_cut,
        }));
    }
    let Some(messages) = fitter.omitted(kept_text, token_threshold)? else {
        return Ok(None);
    };
    Ok(Some(Rebuilt {
        messages,
        summary: text.to_string(),
        reduction: (!kept.is_empty()).then_some(Reduction {
            reduced_messages: kept.len(),
            view: OMITTED_LEVEL,
        }),
        summary_cut,
    }))
}

/// The messages after the last reply, which the rebuild keeps after the
/// summary, and the user messages it carries before the summary.
fn rebuild_parts<'m>(
    canonical_messages: &'m [StoredMessage],
    system_message: &StoredMessage,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<(&'m [StoredMessage], Vec<StoredMessage>), CoreError> {
    let (summarized, kept) = split_at_last_reply(canonical_messages);
    let carried = collect_user_messages(
        summarized,
        carry_tokens(system_message, tools, budget)?,
        budget.image_delivery,
        budget.image_cost,
    )?;
    Ok((kept, carried))
}

/// The tokens the carried user messages may take, newest first: at most
/// [`USER_CARRY_TOKENS`], and at most [`USER_CARRY_ROOM_SHARE`] of the room
/// the system prompt, the tools and the preamble leave. The carry is decided
/// once, before the summary and the messages after it, and never gives way
/// to them.
///
/// Text over the budget is cut to it, and left out when the budget is 0: the
/// summary covers it. The newest message is carried whole over the budget
/// only when it holds an image, which cannot be cut; the rebuild then fits
/// with it or is refused.
fn carry_tokens(
    system_message: &StoredMessage,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<usize, CoreError> {
    let Some(token_threshold) = budget.token_threshold else {
        return Ok(USER_CARRY_TOKENS);
    };
    let preamble = head(system_message.clone(), &[], "");
    let fixed = estimate(&preamble[..2], tools, budget)?;
    let room = token_threshold.saturating_sub(fixed) / USER_CARRY_ROOM_SHARE;
    Ok(usize::try_from(room)
        .unwrap_or(usize::MAX)
        .min(USER_CARRY_TOKENS))
}

/// `text` with `note` on its own paragraph after it.
fn noted(text: &str, note: &str) -> String {
    format!("{text}\n\n{note}")
}

/// A rebuilt history that fits, and what was reduced to make it fit.
type Fitted = (Vec<StoredMessage>, Option<Reduction>);

/// Fits the messages after the summary under the threshold, after the fixed
/// parts: the system prompt, the tools and the carried user messages.
struct KeptFitter<'a> {
    kept: &'a [StoredMessage],
    system_message: &'a StoredMessage,
    carried: &'a [StoredMessage],
    tools: &'a [ToolDefinition],
    budget: CompactionBudget,
    texts: &'a RebuildTexts<'a>,
}

impl KeptFitter<'_> {
    /// The rebuilt history with `summary` whole: the fewest dropped units
    /// after the summary whose reduced view fits, and what was reduced.
    /// `None` when even the omitted line does not fit beside the summary.
    fn fit(&self, summary: &str, token_threshold: u64) -> Result<Option<Fitted>, CoreError> {
        let kept = self.kept;
        let units = kept_units(kept);
        for dropped in 0..=units.len() {
            let mut reduced = units[..dropped]
                .iter()
                .flatten()
                .copied()
                .collect::<Vec<_>>();
            reduced.sort_unstable();
            let mut messages = head(self.system_message.clone(), self.carried, summary);
            let view_index = messages.len();
            messages.extend(
                (0..kept.len())
                    .filter(|index| reduced.binary_search(index).is_err())
                    .map(|index| kept[index].clone()),
            );
            if reduced.is_empty() {
                if self.fits(&messages, token_threshold)? {
                    return Ok(Some((messages, None)));
                }
                continue;
            }
            let base = self.estimate(&messages)?;
            let Some(room) = token_threshold.checked_sub(base.saturating_add(1)) else {
                continue;
            };
            let items = reduced
                .iter()
                .flat_map(|index| message_items(&kept[*index].message))
                .filter(|item| !matches!(item, Item::AssistantText { .. }))
                .collect::<Vec<_>>();
            let view = reduce(
                &items,
                room,
                |text| {
                    let mut candidate = messages.clone();
                    candidate.insert(view_index, view_message(text));
                    self.estimate(&candidate)
                        .unwrap_or(u64::MAX)
                        .saturating_sub(base)
                },
                &self.texts.view,
            );
            messages.insert(view_index, view_message(view.text()));
            if self.fits(&messages, token_threshold)? {
                return Ok(Some((
                    messages,
                    Some(Reduction {
                        reduced_messages: reduced.len(),
                        view: view.level(),
                    }),
                )));
            }
        }
        Ok(None)
    }

    /// The longest part of `summary`, cut at a line end, within
    /// [`TRUNCATED_SUMMARY_SHARE`] of the room the fixed parts, the note and
    /// the omitted line leave. `None` when not even one character fits.
    fn cut_summary<'s>(
        &self,
        summary: &'s str,
        token_threshold: u64,
    ) -> Result<Option<&'s str>, CoreError> {
        let fixed = self.estimate(&self.minimal(&noted("", self.texts.summary_cut)))?;
        let room = token_threshold.saturating_sub(fixed.saturating_add(1));
        let share = room.saturating_mul(TRUNCATED_SUMMARY_SHARE) / 100;
        let empty = self.text_tokens("")?;
        let kept = longest_fitting_prefix(summary, |prefix| {
            Ok(self.text_tokens(prefix)?.saturating_sub(empty) <= share)
        })?;
        Ok(kept.map(|kept| &summary[..kept.len()]))
    }

    /// The fixed parts, `cut` noted as cut, and the omitted line in place of
    /// the messages after it: the rebuild of a summary too long for the room.
    /// `None` when it does not fit.
    fn omitted(
        &self,
        cut: &str,
        token_threshold: u64,
    ) -> Result<Option<Vec<StoredMessage>>, CoreError> {
        let messages = self.minimal(&noted(cut, self.texts.summary_cut));
        Ok(self.fits(&messages, token_threshold)?.then_some(messages))
    }

    /// The fixed parts, `summary` and, when messages follow the last reply,
    /// the omitted line in their place.
    fn minimal(&self, summary: &str) -> Vec<StoredMessage> {
        let mut messages = head(self.system_message.clone(), self.carried, summary);
        if !self.kept.is_empty() {
            messages.push(view_message(self.texts.view.omitted));
        }
        messages
    }

    fn text_tokens(&self, text: &str) -> Result<u64, CoreError> {
        estimate(&[view_message(text)], &[], self.budget)
    }

    fn estimate(&self, messages: &[StoredMessage]) -> Result<u64, CoreError> {
        estimate(messages, self.tools, self.budget)
    }

    fn fits(&self, messages: &[StoredMessage], token_threshold: u64) -> Result<bool, CoreError> {
        Ok(self.estimate(messages)? < token_threshold)
    }
}

/// The units the rebuild drops, oldest first: the reply with its tool
/// results as one whole exchange, then each later message on its own.
fn kept_units(kept: &[StoredMessage]) -> Vec<Vec<usize>> {
    if kept.is_empty() {
        return Vec::new();
    }
    let is_result = |index: &usize| matches!(kept[*index].message, Message::Tool { .. });
    let exchange = std::iter::once(0)
        .chain((1..kept.len()).filter(is_result))
        .collect::<Vec<_>>();
    let others = (1..kept.len())
        .filter(|index| !is_result(index))
        .map(|index| vec![index]);
    std::iter::once(exchange).chain(others).collect()
}

/// The longest non-empty prefix of `text`, cut at a line end where it has
/// one, that `fits`; `None` when not even one character does.
fn longest_fitting_prefix(
    text: &str,
    fits: impl Fn(&str) -> Result<bool, CoreError>,
) -> Result<Option<String>, CoreError> {
    if fits(text)? {
        return Ok(Some(text.to_string()));
    }
    let mut lower = 1;
    let mut upper = text.len();
    let mut fitted = None;
    while lower <= upper {
        let max_bytes = lower + (upper - lower) / 2;
        let prefix = cut_at_line_end(text, max_bytes);
        if !prefix.is_empty() && fits(prefix)? {
            fitted = Some(prefix.to_string());
            lower = max_bytes + 1;
        } else {
            upper = max_bytes - 1;
        }
    }
    Ok(fitted)
}

/// The longest prefix of `text` within `max_bytes` that ends at a line end,
/// or at a character boundary when no line ends within it.
pub(super) fn cut_at_line_end(text: &str, max_bytes: usize) -> &str {
    if text.len() <= max_bytes {
        return text;
    }
    let prefix = &text[..prefix_end(text, max_bytes)];
    match prefix.rfind('\n') {
        Some(line_end) if line_end > 0 => prefix[..line_end].trim_end(),
        _ => prefix,
    }
}

/// The system prompt, the carried user messages and the summary: the parts
/// every rebuilt history starts with.
fn head(
    system_message: StoredMessage,
    carried: &[StoredMessage],
    summary: &str,
) -> Vec<StoredMessage> {
    let mut messages = vec![
        system_message,
        StoredMessage::injected(Message::user_text(PREVIOUS_USER_MESSAGES_PREAMBLE)),
    ];
    messages.extend(carried.iter().cloned());
    messages.push(StoredMessage::injected(Message::user_text(format!(
            "{PREVIOUS_USER_MESSAGES_CLOSE}\n\nHere is a summary of what has happened so far:\n\n{COMPACTION_SUMMARY_OPEN}\n{summary}\n{COMPACTION_SUMMARY_CLOSE}"
        ))));
    messages
}

fn view_message(text: &str) -> StoredMessage {
    StoredMessage::injected(Message::user_text(text))
}

fn estimate(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<u64, CoreError> {
    estimate_model_input_tokens(messages, tools, budget.image_delivery, budget.image_cost)
}

/// The history up to the last reply, which the summary covers, and the reply
/// with everything after it, which the rebuilt history keeps.
fn split_at_last_reply(messages: &[StoredMessage]) -> (&[StoredMessage], &[StoredMessage]) {
    let reply = messages
        .iter()
        .rposition(|stored| matches!(stored.message, Message::Assistant { .. }))
        .unwrap_or(messages.len());
    messages.split_at(reply)
}

struct ExtractedSummary {
    summary: String,
    delimited: bool,
}

fn extract_summary(response: &str) -> Result<ExtractedSummary, SummaryRejection> {
    let response = response.trim();
    if response.is_empty() {
        return Err(SummaryRejection::EmptyResponse);
    }

    let Some(summary_start) = response.find(SUMMARY_OPEN) else {
        return if response.contains(SUMMARY_CLOSE) {
            Err(SummaryRejection::MalformedDelimiters)
        } else {
            Ok(ExtractedSummary {
                summary: response.to_string(),
                delimited: false,
            })
        };
    };
    let content_start = summary_start + SUMMARY_OPEN.len();
    let Some(relative_end) = response[content_start..].find(SUMMARY_CLOSE) else {
        return Err(SummaryRejection::MalformedDelimiters);
    };
    let summary = response[content_start..content_start + relative_end].trim();
    if summary.is_empty() {
        return Err(SummaryRejection::MalformedDelimiters);
    }
    Ok(ExtractedSummary {
        summary: summary.to_string(),
        delimited: true,
    })
}

/// The summary text of a cut-off response: what follows an opening
/// `<summary>`, or the whole response without one, up to a closing
/// `</summary>`. `None` when empty.
fn partial_summary(response: &str) -> Option<String> {
    let response = response.trim();
    let partial = response
        .find(SUMMARY_OPEN)
        .map_or(response, |start| &response[start + SUMMARY_OPEN.len()..]);
    let partial = partial
        .find(SUMMARY_CLOSE)
        .map_or(partial, |end| &partial[..end])
        .trim();
    (!partial.is_empty()).then(|| partial.to_string())
}

fn is_prompt_content(content: &[ContentBlock]) -> bool {
    let Ok(text) = content_blocks_to_text(content, "compaction prompt") else {
        return false;
    };
    text == PROMPT
        || (text.starts_with(PROMPT)
            && text.contains(EXTRA_INSTRUCTIONS_OPEN)
            && text.ends_with(EXTRA_INSTRUCTIONS_CLOSE))
}

fn collect_user_messages(
    messages: &[StoredMessage],
    max_tokens: usize,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageCostRule,
) -> Result<Vec<StoredMessage>, CoreError> {
    let candidates = messages
        .iter()
        .filter(|stored| !stored.is_injected() && !stored.is_generated_system())
        .filter(|stored| matches!(&stored.message, Message::User { .. }))
        .collect::<Vec<_>>();

    let mut selected = Vec::new();
    let mut remaining = max_tokens;
    for stored in candidates.into_iter().rev() {
        let cost = estimate_user_message_tokens(stored, image_delivery, image_cost)?;
        if cost <= remaining {
            selected.push(stored.clone());
            remaining -= cost;
            continue;
        }
        if let Some(message) = cut_user_message(stored, remaining, image_delivery, image_cost)? {
            selected.push(message);
        } else if selected.is_empty() && holds_image(&stored.message) {
            // An image cannot be cut, and leaving it out would drop it unseen:
            // the newest one goes whole, and the rebuild fits with it or is
            // refused. Other content, an embedded text file's included, is
            // left to the summary like long text.
            selected.push(stored.clone());
        }
        break;
    }
    selected.reverse();
    Ok(selected)
}

/// The longest cut of a text user message that fits `max_tokens` with its
/// framing; `None` when no cut fits.
fn cut_user_message(
    stored: &StoredMessage,
    max_tokens: usize,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageCostRule,
) -> Result<Option<StoredMessage>, CoreError> {
    let with_cut = |cut: &Message| {
        let mut message = stored.clone();
        message.message = cut.clone();
        message
    };
    let cut = longest_fitting_cut(&stored.message, max_tokens, |cut| {
        Ok(estimate_user_message_tokens(&with_cut(cut), image_delivery, image_cost)? <= max_tokens)
    })?;
    Ok(cut.map(|cut| with_cut(&cut)))
}

/// What a carried user message adds to the rebuilt history, its message
/// framing included, so many short messages are not undercounted.
fn estimate_user_message_tokens(
    stored: &StoredMessage,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageCostRule,
) -> Result<usize, CoreError> {
    let tokens = estimate_model_input_tokens(
        std::slice::from_ref(stored),
        &[],
        image_delivery,
        image_cost,
    )?;
    Ok(usize::try_from(tokens).unwrap_or(usize::MAX))
}

fn truncate_text_user_message(message: &Message, max_tokens: usize) -> Option<Message> {
    let Message::User { content } = message else {
        return None;
    };
    let text = content
        .iter()
        .map(|block| match block {
            ContentBlock::Text(content) => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<Option<Vec<_>>>()?
        .join("\n");
    let truncated = truncate_middle_to_tokens(&text, max_tokens);
    (!truncated.is_empty()).then(|| Message::user_text(truncated))
}

fn approx_token_count(text: &str) -> usize {
    text.len().div_ceil(APPROX_BYTES_PER_TOKEN)
}

pub(super) fn truncate_middle_to_tokens(text: &str, max_tokens: usize) -> String {
    if text.is_empty() {
        return String::new();
    }
    let max_bytes = max_tokens.saturating_mul(APPROX_BYTES_PER_TOKEN);
    if max_tokens > 0 && text.len() <= max_bytes {
        return text.to_string();
    }
    let mut marker = "<system>truncated here for size</system>".to_string();
    for _ in 0..4 {
        if marker.len() >= max_bytes {
            return text[..prefix_end(text, max_bytes)].to_string();
        }
        let content_budget = max_bytes - marker.len();
        let left_budget = content_budget / 2;
        let right_budget = content_budget - left_budget;
        let prefix_end = prefix_end(text, left_budget);
        let suffix_start = suffix_start(text, right_budget);
        let removed_tokens = text[prefix_end..suffix_start]
            .len()
            .div_ceil(APPROX_BYTES_PER_TOKEN);
        let next_marker =
            format!("<system>{removed_tokens} tokens truncated here for size</system>");
        if next_marker == marker {
            return format!("{}{}{}", &text[..prefix_end], marker, &text[suffix_start..]);
        }
        marker = next_marker;
    }

    if marker.len() >= max_bytes {
        return text[..prefix_end(text, max_bytes)].to_string();
    }
    let content_budget = max_bytes.saturating_sub(marker.len());
    let left_budget = content_budget / 2;
    let right_budget = content_budget - left_budget;
    format!(
        "{}{}{}",
        &text[..prefix_end(text, left_budget)],
        marker,
        &text[suffix_start(text, right_budget)..]
    )
}

fn prefix_end(text: &str, byte_budget: usize) -> usize {
    text.char_indices()
        .map(|(index, character)| index + character.len_utf8())
        .take_while(|end| *end <= byte_budget)
        .last()
        .unwrap_or(0)
}

fn suffix_start(text: &str, byte_budget: usize) -> usize {
    let target = text.len().saturating_sub(byte_budget);
    text.char_indices()
        .map(|(index, _)| index)
        .find(|index| *index >= target)
        .unwrap_or(text.len())
}

pub(in crate::core) fn latest_preserved_user_index(messages: &[StoredMessage]) -> Option<usize> {
    messages.iter().rposition(|stored| {
        !stored.is_injected()
            && !stored.is_generated_system()
            && matches!(stored.message, Message::User { .. })
    })
}

pub(in crate::core) fn fit_latest_user_message_to_budget(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    token_threshold: u64,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageCostRule,
) -> Result<Option<Vec<StoredMessage>>, CoreError> {
    let Some(index) = latest_preserved_user_index(messages) else {
        return Ok(None);
    };
    fit_user_message_to_budget(
        messages,
        index,
        tools,
        token_threshold,
        image_delivery,
        image_cost,
    )
}

/// Truncates the text user message at `index` to the longest that brings
/// the messages under the threshold. `None` when the message has non-text
/// content or no truncation fits.
fn fit_user_message_to_budget(
    messages: &[StoredMessage],
    index: usize,
    tools: &[ToolDefinition],
    token_threshold: u64,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageCostRule,
) -> Result<Option<Vec<StoredMessage>>, CoreError> {
    let with_cut = |cut: &Message| {
        let mut candidate = messages.to_vec();
        candidate[index].message = cut.clone();
        candidate
    };
    let cut = longest_fitting_cut(
        &messages[index].message,
        usize::try_from(token_threshold).unwrap_or(usize::MAX),
        |cut| {
            Ok(
                estimate_model_input_tokens(&with_cut(cut), tools, image_delivery, image_cost)?
                    < token_threshold,
            )
        },
    )?;
    Ok(cut.map(|cut| with_cut(&cut)))
}

/// The longest middle cut of a text user message, in text tokens up to
/// `max_tokens`, that `fits`. The cut counts text only while `fits` sees
/// framing and escaping too, so the length is searched rather than
/// derived. `None` when the message has non-text content or no cut fits.
fn longest_fitting_cut(
    message: &Message,
    max_tokens: usize,
    mut fits: impl FnMut(&Message) -> Result<bool, CoreError>,
) -> Result<Option<Message>, CoreError> {
    let Some(text) = text_user_message(message) else {
        return Ok(None);
    };
    let mut lower = 1;
    let mut upper = approx_token_count(&text).min(max_tokens);
    let mut fitted = None;
    while lower <= upper {
        let tokens = lower + (upper - lower) / 2;
        let Some(cut) = truncate_text_user_message(message, tokens) else {
            break;
        };
        if fits(&cut)? {
            fitted = Some(cut);
            lower = tokens + 1;
        } else {
            upper = tokens.saturating_sub(1);
        }
    }
    Ok(fitted)
}

fn holds_image(message: &Message) -> bool {
    matches!(message, Message::User { content } if content.iter().any(is_image_content))
}

fn text_user_message(message: &Message) -> Option<String> {
    let Message::User { content } = message else {
        return None;
    };
    content
        .iter()
        .map(|block| match block {
            ContentBlock::Text(content) => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<Option<Vec<_>>>()
        .map(|parts| parts.join("\n"))
}

#[cfg(test)]
mod tests;
