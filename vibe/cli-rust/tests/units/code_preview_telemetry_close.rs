//! Code preview telemetry: a modal, a transcript scroll and a move to another link each close with their reason.

use crossterm::event::MouseEventKind;
use serde_json::json;
use vibe_rs::sessions_page::{self, Trigger};
use vibe_rs::telemetry::event;

use crate::code_preview_telemetry::{done, line_link, numbered, Preview};
use crate::support::{mouse, send_mouse, stub};

async fn opened(answer: &str) -> Preview {
    let answer = answer.to_owned();
    let mut preview = Preview::new(80, move |_| answer);
    preview.write("f.py", &numbered(3));
    preview.write("g.py", &numbered(3));
    let last = preview.app.view.link_hitmap.len() - 1;
    preview.show(last);
    preview.load().await;
    assert_eq!(
        preview.recorded(),
        [(event::CODE_PREVIEW_OPENED, line_link())]
    );
    preview
}

fn closed(reason: &str) -> serde_json::Value {
    done(json!({"status": "success", "close_reason": reason, "duration_ms": "measured"}))
}

#[tokio::test]
async fn a_modal_taking_focus_closes_the_preview_with_focus_changed() {
    let mut preview = opened("[f.py](f.py:2)").await;
    sessions_page::open(&mut preview.app, &stub(), Trigger::SessionsCommand);
    preview.redraw();
    assert_eq!(
        preview.recorded(),
        [
            (
                event::SESSION_SEARCH_OPENED,
                json!({"trigger": "sessions_command"})
            ),
            (event::CODE_PREVIEW_DONE, closed("focus_changed")),
        ]
    );
}

#[tokio::test]
async fn scrolling_the_transcript_under_the_preview_closes_it_with_link_moved() {
    let answer = format!("{}[f.py](f.py:2)", "Some line.\n\n".repeat(30));
    let mut preview = opened(&answer).await;
    let area = preview.app.code_preview.area;
    assert!(area.y > 0, "{area:?}");
    send_mouse(&mut preview.app, mouse(MouseEventKind::ScrollUp, 0, 0));
    // The event loop eases the scroll toward its target before each frame; land it.
    preview.app.view.scroll = preview.app.view.scroll_target;
    preview.redraw();
    assert_eq!(
        preview.recorded(),
        [(event::CODE_PREVIEW_DONE, closed("link_moved"))]
    );
}

#[tokio::test]
async fn moving_straight_to_another_file_link_closes_with_link_changed() {
    let mut preview = opened("[g.py](g.py) or [f.py](f.py:2)").await;
    preview.point(preview.link(0));
    assert_eq!(
        preview.recorded(),
        [(event::CODE_PREVIEW_DONE, closed("link_changed"))]
    );
    preview.show(0);
    preview.load().await;
    let link = json!({"link_kind": "path", "has_linked_line": false});
    assert_eq!(preview.recorded(), [(event::CODE_PREVIEW_OPENED, link)]);
}
