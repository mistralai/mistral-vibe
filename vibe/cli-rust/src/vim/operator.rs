//! The second key of a Normal-mode operator (`d`, `c`) or prefix (`g`).

use super::edit::past_mention;
use super::motion::{
    first_non_blank, last_char, next_word_start, prev_word_start, right, word_end, word_end_here,
};
use super::{insert, Plan, Vim};
use crate::app::ChatInput;
use crate::utils::input_edit::line_bounds;

/// The second key of `d`, `c` or `g`; anything unbound cancels the operator.
pub(super) fn operator(vim: &mut Vim, op: char, motion: char, chat: &ChatInput) -> Plan {
    let (input, cursor) = (chat.input.as_str(), chat.cursor);
    let (start, end) = line_bounds(input, cursor);
    let span = match (op, motion) {
        ('g', 'g') => return Plan::caret(first_non_blank(input, 0)),
        ('d', 'd') => return delete_line(input, start, end),
        ('c', 'c') => (start, end),
        // `cw` on a word changes to its end; on a blank it acts like `dw`.
        ('c' | 'd', 'w') => match word_end_here(input, cursor).filter(|_| op == 'c') {
            Some(last) => (cursor, right(input, last)),
            // `dw` on an empty line deletes its newline; a mention goes whole, within the line.
            None => {
                let to = end + usize::from(op == 'd' && start == end);
                let to = next_word_start(input, cursor).min(to);
                (cursor, past_mention(chat, 'w', to).min(end.max(to)))
            }
        },
        ('c' | 'd', 'e') => (cursor, right(input, word_end(input, cursor))),
        ('c' | 'd', 'b') => {
            let from = prev_word_start(input, cursor);
            match cursor == start && from < start {
                false => (from, cursor),
                // From a line start, vim stops at the previous line's end, or takes whole lines from a first non-blank.
                true if from <= first_non_blank(input, from) => {
                    (line_bounds(input, from).0, start - usize::from(op == 'c'))
                }
                true => (from, start - 1),
            }
        }
        ('c' | 'd', '0') => (start, cursor),
        // On a blank line `^` is its last blank.
        ('c' | 'd', '^') => (
            first_non_blank(input, cursor).min(last_char(input, cursor)),
            cursor,
        ),
        ('c' | 'd', '$') => (cursor, end),
        _ => return Plan::default(),
    };
    match op {
        // An empty change still moves to its start, as `cb` onto an empty line does.
        'c' => insert(
            vim,
            Plan {
                caret: Some(span.0),
                ..Plan::delete(span.0, span.1)
            },
        ),
        _ => Plan::delete(span.0, span.1),
    }
}

/// `dd`: the caret's line and one newline, leaving the caret on the next line's first non-blank.
fn delete_line(input: &str, start: usize, end: usize) -> Plan {
    let (from, to, after) = match end < input.len() {
        true => (
            start,
            end + 1,
            first_non_blank(input, end + 1) - (end + 1 - start),
        ),
        false if start > 0 => (start - 1, end, first_non_blank(input, start - 1)),
        false => (start, end, start),
    };
    Plan {
        after: Some(after),
        ..Plan::delete(from, to)
    }
}
