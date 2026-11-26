//! Bounded compact-input editing and horizontal hit testing.

use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::chat_input::{self, Action};

pub const MAX_INPUT_BYTES: usize = 65_536;

#[derive(Default)]
pub struct Field {
    pub text: String,
    pub cursor: usize,
    pub anchor: Option<usize>,
    pub scroll: usize,
}

impl Field {
    pub fn new(mut text: String) -> Self {
        let mut end = text.len().min(MAX_INPUT_BYTES);
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
        Self {
            cursor: text.len(),
            text,
            anchor: Some(0),
            scroll: 0,
        }
    }

    pub fn edit(&mut self, action: Action) {
        if let Action::Insert(c) = action {
            let selected = chat_input::selection_range(&self.text, self.cursor, self.anchor)
                .map_or(0, |(a, b)| b - a);
            if c.is_control() || self.text.len() - selected + c.len_utf8() > MAX_INPUT_BYTES {
                return;
            }
        }
        chat_input::apply(&action, &mut self.text, &mut self.cursor, &mut self.anchor);
    }

    pub fn scroll_to_cursor(&mut self, width: usize) {
        let target = self.text[..self.cursor]
            .width()
            .saturating_sub(width.saturating_sub(1));
        self.scroll = 0;
        for ch in self.text.chars() {
            if self.scroll >= target {
                break;
            }
            self.scroll += ch.width().unwrap_or(0);
        }
    }

    pub fn byte_at(&self, column: usize) -> usize {
        let mut used = 0;
        self.text
            .char_indices()
            .find_map(|(i, ch)| {
                used += ch.width().unwrap_or(0);
                (used > self.scroll + column).then_some(i)
            })
            .unwrap_or(self.text.len())
    }
}
