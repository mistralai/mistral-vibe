//! Keys around the subagent list and read-only child views: draft keeping, Esc, Ctrl+C warning and quit.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::quit_manager::QuitConfirmKey;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::subagents::{self, Severity, READ_ONLY_MESSAGE};

use crate::support::send_key;

struct Harness {
    app: App,
}

impl Harness {
    /// A main conversation with one running child listed under the composer.
    fn new() -> Self {
        let mut app = App::default();
        app.subagents.replace_child_session(PublicChildSession {
            id: "child".into(),
            name: "explore-1".into(),
            agent_type: "explore".into(),
            status: SessionStatus::Running,
            created_at: 1,
            updated_at: 1,
            ..Default::default()
        });
        subagents::refresh(&mut app);
        Self { app }
    }

    /// Press a key; true means the application exits.
    fn press(&mut self, code: KeyCode, modifiers: KeyModifiers) -> bool {
        send_key(&mut self.app, KeyEvent::new(code, modifiers))
    }

    fn ctrl_c(&mut self) -> bool {
        self.press(KeyCode::Char('c'), KeyModifiers::CONTROL)
    }

    fn read_only_warnings(&self) -> Vec<(String, Severity)> {
        let child = self.app.subagents.transcripts.child("child");
        child.map_or_else(Vec::new, |child| child.local_user_messages.clone())
    }
}

#[tokio::test]
async fn moving_focus_to_the_subagent_list_and_back_keeps_the_draft() {
    let mut harness = Harness::new();
    for character in "Draft prompt".chars() {
        harness.press(KeyCode::Char(character), KeyModifiers::NONE);
    }
    assert!(!harness.press(KeyCode::Down, KeyModifiers::NONE));
    assert!(harness.app.subagents.list.focused);
    assert_eq!(harness.app.subagents.list.options.selected(), 0);
    assert_eq!(harness.app.chat_input.input, "Draft prompt");

    assert!(!harness.press(KeyCode::Up, KeyModifiers::NONE));
    assert!(!harness.app.subagents.list.focused);
    assert_eq!(harness.app.chat_input.input, "Draft prompt");
}

#[tokio::test]
async fn ctrl_c_in_a_child_view_warns_once_then_a_second_ctrl_c_quits() {
    let mut harness = Harness::new();
    subagents::show_subagent_chat(&mut harness.app, "child");
    let main_rows = harness.app.view.transcript.lines().count();

    assert!(!harness.ctrl_c());
    assert_eq!(
        harness.app.subagents.viewed_subagent_id.as_deref(),
        Some("child")
    );
    let warnings = harness.read_only_warnings();
    assert_eq!(warnings.len(), 1);
    assert_eq!(warnings[0].0, READ_ONLY_MESSAGE);
    assert!(warnings[0].1 == Severity::Error);
    assert!(harness.app.quit.is_confirmed(QuitConfirmKey::CtrlC));
    assert_eq!(harness.app.view.transcript.lines().count(), main_rows);

    assert!(harness.ctrl_c(), "the confirmed Ctrl+C quits");
    assert_eq!(harness.read_only_warnings().len(), 1, "no second warning");
}

#[tokio::test]
async fn escape_from_a_child_view_cancels_the_pending_quit_confirmation() {
    let mut harness = Harness::new();
    subagents::show_subagent_chat(&mut harness.app, "child");

    assert!(!harness.ctrl_c());
    assert!(!harness.press(KeyCode::Esc, KeyModifiers::NONE));
    assert_eq!(harness.app.subagents.viewed_subagent_id, None);
    assert!(!harness.app.quit.is_confirmed(QuitConfirmKey::CtrlC));

    assert!(
        !harness.ctrl_c(),
        "the first Ctrl+C on Main only arms the quit"
    );
    assert!(harness.app.quit.is_confirmed(QuitConfirmKey::CtrlC));
    assert!(harness.ctrl_c());
}

#[tokio::test]
async fn escape_on_the_focused_list_returns_to_the_input_on_main() {
    let mut harness = Harness::new();
    assert!(subagents::focus_first(&mut harness.app));
    assert!(!harness.press(KeyCode::Down, KeyModifiers::NONE));
    assert_eq!(harness.app.subagents.list.options.selected(), 1);

    assert!(!harness.press(KeyCode::Esc, KeyModifiers::NONE));
    assert!(!harness.app.subagents.list.focused);
    assert!(harness.app.view.app_focus);
    assert_eq!(harness.app.subagents.list.options.selected(), 0, "Main row");
    assert_eq!(harness.app.subagents.viewed_subagent_id, None);
    assert!(
        harness.app.overlays.last_escape.is_none(),
        "leaving the list does not arm the double Esc"
    );
}
