//! `/thinking [level]`: no level opens the picker, a level the model offers is saved at once.

use std::sync::Arc;

use super::submission::new_message_id;
use crate::app::App;
use crate::config_write::Scope;
use crate::server::Client;
use crate::thinking_picker;
use crate::transcript::local;

pub fn command(app: &mut App, client: &Arc<Client>, level: &str) {
    if level.is_empty() {
        return thinking_picker::open(app);
    }
    thinking_picker::cancel(app);
    let levels = &app.thinking_picker.levels;
    let Some(index) = levels.iter().position(|it| it.eq_ignore_ascii_case(level)) else {
        let message = format!(
            "Unknown thinking level: {level}. Use one of: {}",
            levels.join(", ")
        );
        return local::add_command_error(&mut app.view.transcript, &new_message_id(), &message);
    };
    app.thinking_picker.list.highlight(index);
    thinking_picker::select(app, client, Scope::Saved);
}
