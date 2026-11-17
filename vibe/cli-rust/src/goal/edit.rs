//! `/goal edit`: a multi-line objective and a token budget field.

use crossterm::event::{KeyCode, KeyEvent};

use super::format;
use crate::chat_input::{self, Action};
use crate::proxy_setup::Field;
use crate::server::{GoalSetParams, PublicGoal};
use crate::ui::composer_layout::ComposerLayout;

/// Objective rows shown before the field scrolls.
pub const OBJECTIVE_ROWS: usize = 5;
const EMPTY_OBJECTIVE: &str = "Enter an objective.";

pub struct EditForm {
    pub goal_id: String,
    pub revision: u64,
    pub objective: String,
    pub cursor: usize,
    pub anchor: Option<usize>,
    /// First visible objective row and the row width of the last frame.
    pub scroll: usize,
    pub width: u16,
    pub budget: Field,
    pub budget_focused: bool,
    pub error: Option<&'static str>,
}

impl EditForm {
    pub fn new(goal: &PublicGoal) -> Self {
        Self {
            goal_id: goal.goal_id.clone(),
            revision: goal.revision,
            cursor: goal.objective.len(),
            objective: goal.objective.clone(),
            anchor: None,
            scroll: 0,
            width: 1,
            budget: Field::new(format::budget_text(goal.token_budget)),
            budget_focused: false,
            error: None,
        }
    }

    pub fn layout(&self) -> ComposerLayout<'_> {
        ComposerLayout::hard_wrapped(&self.objective, self.cursor, self.width)
    }

    /// Edit the focused field with `key`; Tab, Enter and Esc are the caller's.
    pub fn edit(&mut self, key: &KeyEvent) {
        if !self.budget_focused && matches!(key.code, KeyCode::Up | KeyCode::Down) {
            let (cursor, _) = self.layout().vertical_offset(key.code == KeyCode::Down);
            self.cursor = cursor;
            self.anchor = None;
            return;
        }
        if let Some(action) = crate::keymap::action_for(key) {
            self.apply(action);
        }
    }

    pub fn apply(&mut self, action: Action) {
        self.error = None;
        if self.budget_focused {
            if action != Action::Insert('\n') {
                self.budget.edit(action);
            }
            return;
        }
        chat_input::apply(
            &action,
            &mut self.objective,
            &mut self.cursor,
            &mut self.anchor,
        );
    }

    pub fn switch_field(&mut self) {
        self.budget_focused = !self.budget_focused;
        if self.budget_focused {
            self.budget.edit(Action::SelectAll);
        }
    }

    /// The `goal/set` edit, or why the form cannot be saved.
    pub fn params(&self) -> Result<GoalSetParams, &'static str> {
        let objective = self.objective.trim();
        if objective.is_empty() {
            return Err(EMPTY_OBJECTIVE);
        }
        let budget = format::parse_budget(&self.budget.text)?;
        Ok(GoalSetParams {
            expected_goal_id: Some(self.goal_id.clone()),
            expected_revision: Some(self.revision),
            objective: Some(objective.to_owned()),
            token_budget: Some(budget),
            ..Default::default()
        })
    }
}
