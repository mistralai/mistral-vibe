//! Where the toast rack docks: above the bottom app while one is drawn, and
//! above the input box otherwise.

use ratatui::backend::TestBackend;
use ratatui::Terminal;

use vibe_rs::app::{App, ToastSeverity};
use vibe_rs::resume_picker::Session;

const TOAST_SECS: u64 = 60;

fn drawn(app: &mut App) {
    let mut terminal = Terminal::new(TestBackend::new(80, 24)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
}

fn toast_bottom(app: &App) -> u16 {
    app.view
        .toast_text_areas
        .last()
        .map(|(_, area)| area.bottom())
        .expect("a painted toast")
}

#[test]
fn a_bottom_app_docks_the_toast_above_its_box() {
    let mut app = App::default();
    app.resume_picker.open = true;
    app.resume_picker.sessions = vec![Session {
        id: "busy".into(),
        title: "Saved implementation".into(),
        ..Default::default()
    }];
    app.show_toast("Toast text".to_owned(), ToastSeverity::Error, TOAST_SECS);
    drawn(&mut app);

    let (_, box_area) = app.view.bottom_app.expect("the picker's box");
    assert_eq!(toast_bottom(&app), box_area.y - 1);
}

#[test]
fn without_a_bottom_app_the_toast_docks_above_the_input_box() {
    let mut app = App::default();
    app.show_toast("Toast text".to_owned(), ToastSeverity::Error, TOAST_SECS);
    drawn(&mut app);

    assert!(app.view.bottom_app.is_none());
    assert_eq!(
        toast_bottom(&app),
        app.view.input_area.y - 1,
        "the toast must sit above the input box, not at the frame bottom"
    );
}
