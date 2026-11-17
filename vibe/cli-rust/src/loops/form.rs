//! The form that adds a loop: a one-line title and schedule, and multi-line instructions.

use serde_json::{json, Value};

use crate::chat_input::{self, Action};
use crate::proxy_setup::{Field, MAX_INPUT_BYTES};
use crate::ui::composer_layout::ComposerLayout;

/// Instructions rows shown before the field scrolls.
pub const INSTRUCTIONS_ROWS: usize = 5;

/// The form's inputs, top to bottom.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FormField {
    Title,
    Schedule,
    Instructions,
}

impl FormField {
    pub const ALL: [Self; 3] = [Self::Title, Self::Schedule, Self::Instructions];

    pub fn label(self) -> &'static str {
        match self {
            Self::Title => "Title",
            Self::Schedule => "Every",
            Self::Instructions => "Instructions",
        }
    }

    pub fn placeholder(self) -> &'static str {
        match self {
            Self::Title => "A short name for the loop",
            Self::Schedule => "10m, 2h, 1d, or a cron such as 0 9 * * 1-5",
            Self::Instructions => "The prompt sent to the agent each time the loop runs",
        }
    }
}

#[derive(Default)]
pub struct Form {
    pub title: Field,
    pub schedule: Field,
    pub instructions: Field,
    pub focused: usize,
    /// The in-flight `loops/create` (its `LoopsApp::submissions` count); Enter waits for its answer.
    pub submission: Option<u64>,
    /// The instructions input's width in the last frame, for moving the caret by wrapped row.
    pub width: u16,
}

impl Form {
    pub fn field(&mut self, which: FormField) -> &mut Field {
        match which {
            FormField::Title => &mut self.title,
            FormField::Schedule => &mut self.schedule,
            FormField::Instructions => &mut self.instructions,
        }
    }

    pub fn focused_field(&self) -> FormField {
        FormField::ALL[self.focused]
    }

    /// Tab and the arrows move between the inputs, wrapping at both ends.
    pub fn move_focus(&mut self, down: bool) {
        let len = FormField::ALL.len();
        self.focused = (self.focused + if down { 1 } else { len - 1 }) % len;
    }

    /// The instructions wrapped to `width`, the last cell kept for the caret.
    pub fn instructions_layout(&self, width: u16) -> ComposerLayout<'_> {
        let field = &self.instructions;
        ComposerLayout::new(&field.text, "", field.cursor, width)
    }

    /// Move the caret one wrapped instructions row; `false` on the field's edge or another field.
    pub fn move_caret(&mut self, down: bool) -> bool {
        if self.focused_field() != FormField::Instructions {
            return false;
        }
        let (cursor, moved) = self.instructions_layout(self.width).vertical_offset(down);
        if moved {
            self.instructions.cursor = cursor;
        }
        moved
    }

    /// Edit the focused input; only the instructions take line breaks and tabs.
    pub fn edit(&mut self, action: Action) {
        let which = self.focused_field();
        if which != FormField::Instructions {
            return self.field(which).edit(action);
        }
        let field = &mut self.instructions;
        if let Action::Insert(c) = action {
            let control = c.is_control() && !matches!(c, '\n' | '\t');
            if control || field.text.len() + c.len_utf8() > MAX_INPUT_BYTES {
                return;
            }
        }
        // The field draws no selection, so the anchor is discarded.
        field.anchor = None;
        chat_input::apply(
            &action,
            &mut field.text,
            &mut field.cursor,
            &mut field.anchor,
        );
        field.anchor = None;
    }

    /// The `loops/create` params, or why the form cannot be sent yet.
    pub fn params(&self, session_id: &str) -> Result<Value, String> {
        let title = self.title.text.trim();
        let instructions = self.instructions.text.trim();
        if title.is_empty() {
            return Err("Give the loop a title.".into());
        }
        if instructions.is_empty() {
            return Err("Write the instructions the loop sends.".into());
        }
        let prompt = format!("{title}\n{instructions}");
        let (key, value) = schedule(&self.schedule.text)?;
        Ok(json!({"sessionId": session_id, key: value, "prompt": prompt}))
    }
}

/// `("interval", "10m")` for `10m` or `every 10m`, `("cron", ..)` for five fields.
pub fn schedule(text: &str) -> Result<(&'static str, String), String> {
    let text = text.trim();
    let text = match text.get(..6) {
        Some(prefix) if prefix.eq_ignore_ascii_case("every ") => text[6..].trim(),
        _ => text,
    };
    match text.split_whitespace().count() {
        0 => Err("Say how often the loop runs.".into()),
        1 => Ok(("interval", text.to_lowercase())),
        5 => Ok((
            "cron",
            text.split_whitespace().collect::<Vec<_>>().join(" "),
        )),
        _ => Err("Use an interval such as 10m, or a five-field cron.".into()),
    }
}
