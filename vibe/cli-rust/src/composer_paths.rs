//! Bare image paths in the composer, rewritten as `@` mentions around the
//! mentions already there.

use crate::app::ChatInput;
use crate::input_modes::InputMode;
use crate::mentions::Mention;
use crate::paste_path::rewrite_bare_image_paths_with_spans;

/// Rewrite bare image paths as `@` mentions, leaving every mention, the text
/// a collapsed paste hides, and a word glued to either side of a mention as
/// they are: a path runs up to whitespace, so a word touching a placeholder
/// is no path, unless the paste it hides ends (or starts) with whitespace.
pub fn rewrite_image_paths(input: &mut ChatInput) {
    // A shell command reads paths as they are; only prompts resolve `@path`.
    if input.mode == InputMode::Bash {
        return;
    }
    input.sync_mentions();
    let mentions = input.mentions.spans(&input.input).to_vec();
    let mut text = String::with_capacity(input.input.len());
    let mut kept = Vec::with_capacity(mentions.len());
    let mut added = Vec::new();
    let mut at = 0;
    let mut previous: Option<&Mention> = None;
    for mention in mentions.iter().map(Some).chain([None]) {
        let end = mention.map_or(input.input.len(), |mention| mention.start);
        let gap = &input.input[at..end];
        let lead = match previous
            .is_some_and(|previous| glued(previous, |paste| paste.chars().next_back()))
        {
            false => 0,
            true => gap.find(char::is_whitespace).unwrap_or(gap.len()),
        };
        let tail =
            match mention.is_some_and(|mention| glued(mention, |paste| paste.chars().next())) {
                false => gap.len(),
                true => gap
                    .char_indices()
                    .rev()
                    .find(|(_, character)| character.is_whitespace())
                    .map_or(0, |(index, character)| index + character.len_utf8()),
            }
            .max(lead);
        text.push_str(&gap[..lead]);
        let (middle, spans) = rewrite_bare_image_paths_with_spans(&gap[lead..tail]);
        let offset = text.len();
        added.extend(
            spans
                .into_iter()
                .map(|(start, end)| (offset + start, offset + end)),
        );
        text.push_str(&middle);
        text.push_str(&gap[tail..]);
        if let Some(mention) = mention {
            let start = text.len();
            text.push_str(&input.input[mention.start..mention.end]);
            kept.push(Mention {
                start,
                end: text.len(),
                paste: mention.paste.clone(),
            });
            at = mention.end;
        }
        previous = mention;
    }
    if text == input.input {
        return;
    }
    input.input = text;
    input.mentions.restore(&input.input, kept);
    input.cursor = input.input.len();
    input.anchor = None;
    for (start, end) in added {
        input.add_mention(start, end);
    }
}

/// Whether a word touching `mention` on the side `edge` reads from would run
/// into it once submitted: always, unless it hides a paste whose text starts
/// or ends there with whitespace.
fn glued(mention: &Mention, edge: impl Fn(&str) -> Option<char>) -> bool {
    mention
        .paste
        .as_deref()
        .and_then(edge)
        .is_none_or(|character| !character.is_whitespace())
}
