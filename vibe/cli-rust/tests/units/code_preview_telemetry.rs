//! Code preview telemetry: `opened` on the first paint, one `done` per attempt, and copies out of it tagged.

use std::time::Duration;

use crossterm::event::MouseEventKind;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::code_preview;
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::ui_event::UiEvent;

use crate::support::{down, drag, message, mouse, render, send_mouse, session_state, up};

pub struct Preview {
    pub app: App,
    pub dir: tempfile::TempDir,
    width: u16,
    telemetry: mpsc::Receiver<TelemetryEvent>,
    events: mpsc::Receiver<UiEvent>,
}

impl Preview {
    /// A session in a fresh directory on a `width`-column terminal, whose answer is `answer(cwd)`.
    pub fn new(width: u16, answer: impl FnOnce(&str) -> String) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let cwd = dir.path().to_str().unwrap().to_owned();
        let mut app = App::default();
        app.set_session_id("s".to_owned());
        app.set_status(Status::Ready);
        app.session.cwd = Some(cwd.clone());
        app.session.startup_config.autocopy_to_clipboard = true;
        let (tx, telemetry) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
        app.telemetry_tx = Some(tx);
        let (tx, events) = mpsc::channel(8);
        app.events = Some(tx);
        let transcript = vec![message("a", "assistant", &answer(&cwd))];
        app.view
            .transcript
            .load_snapshot(&session_state("s", transcript));
        let mut preview = Self {
            app,
            dir,
            width,
            telemetry,
            events,
        };
        preview.redraw();
        preview
    }

    pub fn write(&self, name: &str, text: &str) {
        std::fs::write(self.dir.path().join(name), text).unwrap();
    }

    pub fn redraw(&mut self) {
        let app = &mut self.app;
        render(self.width, 24, |frame| app.draw(frame));
    }

    /// The first cell of the `index`th link on screen.
    pub fn link(&self, index: usize) -> (u16, u16) {
        let (y, x, _) = self.app.view.link_hitmap[index].rows()[0];
        (x, y)
    }

    pub fn point(&mut self, (x, y): (u16, u16)) {
        send_mouse(&mut self.app, mouse(MouseEventKind::Moved, x, y));
    }

    /// Hover link `index` and let the delay elapse, as the event loop does.
    pub fn show(&mut self, index: usize) {
        self.point(self.link(index));
        code_preview::show(&mut self.app);
    }

    /// Apply the file read the preview started, then paint.
    pub async fn load(&mut self) {
        let event = tokio::time::timeout(Duration::from_secs(5), self.events.recv()).await;
        let Ok(Some(UiEvent::CodePreview(event))) = event else {
            panic!("the file read lands");
        };
        code_preview::apply_event(&mut self.app, event);
        self.redraw();
    }

    /// The events recorded since the last call, with a `done`'s real-clock `duration_ms` checked and dropped.
    pub fn recorded(&mut self) -> Vec<(&'static str, Value)> {
        std::iter::from_fn(|| self.telemetry.try_recv().ok())
            .map(|event| {
                let mut properties = json!(event.properties);
                if let Some(duration) = properties.as_object_mut().unwrap().remove("duration_ms") {
                    assert!(duration.is_u64(), "{duration}");
                    properties["duration_ms"] = json!("measured");
                }
                (event.name, properties)
            })
            .collect()
    }
}

/// The `done` properties of a `path` link to line 2, over `extra`.
pub fn done(extra: Value) -> Value {
    let mut properties = json!({
        "link_kind": "path",
        "has_linked_line": true,
        "has_scrolled": false,
        "has_copied": false,
        "is_scrollable": false,
    });
    for (key, value) in extra.as_object().unwrap() {
        properties[key] = value.clone();
    }
    properties
}

pub fn numbered(lines: usize) -> String {
    (1..=lines).map(|n| format!("x{n} = {n}\n")).collect()
}

pub fn line_link() -> Value {
    json!({"link_kind": "path", "has_linked_line": true})
}

#[tokio::test]
async fn a_scrolled_and_copied_preview_reports_both_when_the_pointer_leaves() {
    let mut preview = Preview::new(80, |_| "[f.py](f.py:2)".into());
    preview.write("f.py", &numbered(60));
    preview.show(0);
    preview.load().await;
    assert_eq!(
        preview.recorded(),
        [(event::CODE_PREVIEW_OPENED, line_link())]
    );

    let area = preview.app.code_preview.area;
    let (x, y) = (area.x + 10, area.y + 1);
    send_mouse(&mut preview.app, mouse(MouseEventKind::ScrollDown, x, y));
    preview.redraw();
    send_mouse(&mut preview.app, down(x, y));
    send_mouse(&mut preview.app, drag(x + 4, y + 1));
    send_mouse(&mut preview.app, up(x + 4, y + 1));
    preview.redraw();
    preview.point((0, area.bottom()));

    let copied = json!({"text_length": 9, "source": "code_preview"});
    let closed = done(json!({
        "status": "success",
        "close_reason": "pointer_left",
        "duration_ms": "measured",
        "has_scrolled": true,
        "has_copied": true,
        "is_scrollable": true,
    }));
    assert_eq!(
        preview.recorded(),
        [
            (event::USER_COPIED_TEXT, copied),
            (event::CODE_PREVIEW_DONE, closed)
        ]
    );
}

#[tokio::test]
async fn a_missing_file_reports_an_error_and_no_opened() {
    let mut preview = Preview::new(80, |_| "[f.py](f.py)".into());
    preview.show(0);
    preview.load().await;
    let failed = done(json!({
        "has_linked_line": false,
        "status": "error",
        "error_type": "not_found",
    }));
    assert_eq!(preview.recorded(), [(event::CODE_PREVIEW_DONE, failed)]);
    preview.point((0, 0));
    assert!(preview.recorded().is_empty());
}

#[tokio::test]
async fn an_editor_link_closed_by_a_click_outside_names_its_scheme() {
    let mut preview = Preview::new(80, |cwd| format!("[f.py](vscode://file{cwd}/f.py:2)"));
    preview.write("f.py", &numbered(3));
    preview.show(0);
    preview.load().await;
    send_mouse(&mut preview.app, down(0, 0));
    let link = json!({"link_kind": "editor", "editor_scheme": "vscode", "has_linked_line": true});
    let closed = done(json!({
        "link_kind": "editor",
        "editor_scheme": "vscode",
        "status": "success",
        "close_reason": "click_outside",
        "duration_ms": "measured",
    }));
    assert_eq!(
        preview.recorded(),
        [
            (event::CODE_PREVIEW_OPENED, link),
            (event::CODE_PREVIEW_DONE, closed)
        ]
    );
}

#[tokio::test]
async fn a_preview_left_before_its_file_loads_is_cancelled_once() {
    let mut preview = Preview::new(80, |_| "[f.py](f.py:2)".into());
    preview.write("f.py", &numbered(3));
    preview.show(0);
    preview.point((0, 0));
    preview.load().await;
    let cancelled = done(json!({"status": "cancelled", "close_reason": "pointer_left"}));
    assert_eq!(preview.recorded(), [(event::CODE_PREVIEW_DONE, cancelled)]);
}

#[tokio::test]
async fn a_terminal_too_narrow_for_the_popup_reports_cancelled_and_no_opened() {
    let mut preview = Preview::new(20, |_| "[f.py](f.py:2)".into());
    preview.write("f.py", &numbered(3));
    preview.show(0);
    preview.load().await;
    assert!(preview.app.code_preview.area.is_empty());
    assert!(preview.recorded().is_empty());
    preview.point((0, 0));
    let cancelled = done(json!({"status": "cancelled", "close_reason": "pointer_left"}));
    assert_eq!(preview.recorded(), [(event::CODE_PREVIEW_DONE, cancelled)]);
}
