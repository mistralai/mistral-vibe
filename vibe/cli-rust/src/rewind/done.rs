//! Apply an answered rewind: rebuild the session's history and put the rewound prompt back in the input.

use super::quit;
use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::input_modes::ClassifiedInput;
use crate::server::PublicSessionState;
use crate::transcript::local;

pub(super) fn apply_done(
    app: &mut App,
    entry_id: &str,
    message: String,
    restore_errors: Vec<String>,
    old_session_id: String,
    inplace: bool,
    state: PublicSessionState,
) {
    // Read before the transcript is rebuilt: the rewound message's collapsed
    // pastes and attached images.
    let rewound = app.view.transcript.entry_raw(entry_id);
    let display = rewound
        .and_then(|raw| raw.get("userDisplayContent"))
        .cloned();
    let images = rewound
        .map(crate::inline_images::attachments)
        .unwrap_or_default();
    quit(app);
    app.session.take_back_sent = false;
    let typed =
        std::mem::take(&mut app.session.taking_back) && !app.chat_input.full_text().is_empty();
    // The rewound turns are gone, so no `/retry` offer survives them.
    crate::commands::retry::cancel(app);
    let new_session_id = state.session.id.clone();
    crate::background_work::adopt_session(app, &state);
    app.session.active_turn_id = None;
    // Drop the queued prompts and rebuild every row from the returned
    // history, so no client-owned entry survives the rewind.
    app.view.transcript.clear();
    // Either rewind mode leaves the harness session without its todo list.
    app.todo_tracker.clear();
    crate::goal::adopt(app, state.session.goal.clone(), false);
    app.queue.clear();
    app.view.expanded.clear();
    app.view.transcript.load_snapshot(&state);
    crate::worktree::adopt_state(app, &state);
    crate::subagents::refresh(app);
    app.expand_rebuilt_tools();
    if !inplace {
        local::add_rewind_fork(
            &mut app.view.transcript,
            &new_message_id(),
            &old_session_id,
            &new_session_id,
        );
    }
    // After the rebuild, which would otherwise drop them.
    for error in restore_errors {
        crate::notices::refused(app, &error);
    }
    app.view.scroll = 0;
    app.view.scroll_target = 0;
    if typed {
        return;
    }
    // The rewound message goes back into the composer, caret at the end, ready to be edited.
    // A skill invocation reloads in `/` mode as typed; any other message is a prompt.
    match crate::input_modes::classify(&message, &app.completion.skills) {
        ClassifiedInput::Skill { .. } => app.chat_input.load_full_text(message.clone()),
        _ => app.chat_input.load_prompt_text(message.clone()),
    }
    crate::long_paste::restore(app, display.as_ref());
    crate::inline_images::restore_placeholders(app, &message, &images);
    app.chat_input.cursor = app.chat_input.input.len();
    crate::completion_manager::input_changed(app);
    // A trailing `@file` or `/skill` mention stays closed until the next edit, like a recall.
    if crate::completion_manager::active_is_mention(app) {
        crate::completion_manager::reset_for_recall(app);
    }
}
