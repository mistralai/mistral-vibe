//! Theme picker state uses configured names and restores canonical themes.

use vibe_rs::app::App;
use vibe_rs::{theme_picker, ui::theme};

#[test]
fn auto_uses_detection_and_failed_writes_restore_the_configured_theme() {
    let mut app = App::default();
    theme::prepare_active("atom-one-light", "ansi-dark");
    theme::set_active("auto");
    assert_eq!(theme::active().name, "ansi-dark");
    theme::set_active("atom-one-light");
    app.session.startup_config.theme = "auto".into();

    theme_picker::open(&mut app);

    assert_eq!(app.theme_picker.current, 0);
    assert_eq!(app.theme_picker.selected, 0);
    theme_picker::cancel(&mut app);
    assert_eq!(theme::active().name, "atom-one-light");

    app.session.startup_config.theme = "atom-one-light".into();
    theme::set_active("dracula");
    app.commit_started();
    theme_picker::apply_event(&mut app, theme_picker::Event::Failed);

    assert_eq!(theme::active().name, "atom-one-light");
}
