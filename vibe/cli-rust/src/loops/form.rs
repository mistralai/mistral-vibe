//! The form that adds or edits a loop: a one-line title and schedule, and multi-line instructions.

use ratatui::layout::Rect;
use serde_json::{json, Value};

use crate::chat_input::{self, Action};
use crate::proxy_setup::{Field, MAX_INPUT_BYTES};
use crate::server::{method, ScheduledLoop};
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
    /// The loop being edited; `None` adds a new one.
    pub editing: Option<Editing>,
    /// The in-flight `loops/create` or `loops/update` (its `LoopsApp::submissions` count); Enter waits for its answer.
    pub submission: Option<u64>,
    /// The instructions input's width in the last frame, for moving the caret by wrapped row.
    pub width: u16,
    /// Each field as last drawn, for clicks.
    pub areas: Vec<FieldArea>,
}

/// The loop a form saves over.
pub struct Editing {
    pub id: String,
    /// It had no instructions (the agent scheduled it on one line), so it may keep none.
    pub one_line: bool,
}

/// Where a field was drawn: its whole row, label included, and its input.
#[derive(Clone, Copy)]
pub struct FieldArea {
    pub field: FormField,
    pub row: Rect,
    pub input: Rect,
}

impl Form {
    /// A form filled from `scheduled`, saving over it.
    pub fn editing(scheduled: &ScheduledLoop) -> Self {
        let field = |text: String| Field {
            anchor: None,
            ..Field::new(text)
        };
        let every = match (&scheduled.cron, scheduled.interval_seconds) {
            (Some(cron), _) => cron.clone(),
            (None, Some(seconds)) => super::schedule::interval(seconds),
            (None, None) => String::new(),
        };
        let instructions = super::schedule::instructions(scheduled);
        Self {
            title: field(super::schedule::title(scheduled).to_owned()),
            schedule: field(every),
            instructions: field(instructions.to_owned()),
            editing: Some(Editing {
                id: scheduled.id.clone(),
                one_line: instructions.is_empty(),
            }),
            ..Self::default()
        }
    }

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

    /// A click on a field's row focuses it; on its text it also moves the caret there.
    pub fn press(&mut self, at: (u16, u16)) {
        let Some(&FieldArea {
            field: which,
            input,
            ..
        }) = self.areas.iter().find(|area| area.row.contains(at.into()))
        else {
            return;
        };
        self.focused = FormField::ALL
            .iter()
            .position(|field| *field == which)
            .unwrap_or(0);
        if !input.contains(at.into()) {
            return;
        }
        let (row, column) = (usize::from(at.1 - input.y), usize::from(at.0 - input.x));
        let cursor = match which {
            FormField::Instructions => {
                let top = self.instructions.scroll;
                self.instructions_layout(self.width)
                    .offset_at(top + row, column)
            }
            _ => self.field(which).byte_at(column),
        };
        let field = self.field(which);
        field.cursor = cursor;
        field.anchor = None;
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

    /// The method and params that save the form, `loops/update` when editing, or why it cannot be sent yet.
    pub fn request(&self, session_id: &str) -> Result<(&'static str, Value), String> {
        let title = self.title.text.trim();
        let instructions = self.instructions.text.trim();
        if title.is_empty() {
            return Err("Give the loop a title.".into());
        }
        let one_line = self
            .editing
            .as_ref()
            .is_some_and(|editing| editing.one_line);
        let prompt = match instructions {
            "" if one_line => title.to_owned(),
            "" => return Err("Write the instructions the loop sends.".into()),
            _ => format!("{title}\n{instructions}"),
        };
        let (key, value) = schedule(&self.schedule.text)?;
        let mut params = json!({"sessionId": session_id, key: value, "prompt": prompt});
        let Some(editing) = &self.editing else {
            return Ok((method::LOOPS_CREATE, params));
        };
        params["loopId"] = json!(editing.id);
        Ok((method::LOOPS_UPDATE, params))
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
