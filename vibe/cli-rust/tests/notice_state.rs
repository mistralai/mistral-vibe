use vibe_rs::app::{App, ToastSeverity};
use vibe_rs::ui::notice;

#[test]
fn warning_notice_keeps_warning_severity() {
    let mut app = App::default();

    notice::show_warning(&mut app, "already processed", 8);

    assert!(matches!(
        app.overlays.notice.as_ref().map(|notice| notice.severity),
        Some(ToastSeverity::Warning)
    ));
}
