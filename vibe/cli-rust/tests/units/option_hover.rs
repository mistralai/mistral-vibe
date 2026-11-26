//! The option under the mouse gets Textual's faint hover background, unless it is highlighted.

use vibe_rs::app::{App, ModelOption};
use vibe_rs::model_picker;
use vibe_rs::ui::theme;

use crate::support::{redraw, stub};

/// The model picker drawn once, and the first cell of each option row.
fn picker() -> (App, Vec<(u16, u16)>) {
    let mut app = App::default();
    app.model_picker.models = ["a", "b"]
        .map(|alias| ModelOption {
            alias: alias.into(),
            ..Default::default()
        })
        .to_vec();
    model_picker::open(&mut app, &stub());
    redraw(&mut app);
    let rows = (0..3)
        .map(|option| {
            let row = app.model_picker.list.rows_of(option).next().unwrap();
            (row.x, row.y)
        })
        .collect();
    (app, rows)
}

#[test]
fn the_hovered_option_takes_the_hover_background() {
    let (mut app, rows) = picker();
    let (x, y) = rows[2];
    let base = redraw(&mut app)[(x, y)].bg;
    app.view.mouse_position = Some((x + 2, y));

    let buffer = redraw(&mut app);
    assert_ne!(theme::block_hover_bg(base), base);
    assert_eq!(buffer[(x, y)].bg, theme::block_hover_bg(base));
    assert_eq!(buffer[rows[1]].bg, base);
}

#[test]
fn the_highlighted_option_keeps_its_bar_under_the_mouse() {
    let (mut app, rows) = picker();
    let (x, y) = rows[app.model_picker.list.selected()];
    let bar = redraw(&mut app)[(x, y)].bg;
    app.view.mouse_position = Some((x + 2, y));

    assert_eq!(redraw(&mut app)[(x, y)].bg, bar);
}

#[test]
fn a_sessions_page_reloading_from_empty_tints_no_old_row() {
    let mut app = crate::support::with_page();
    let session = vibe_rs::sessions_page::Session {
        id: "other".into(),
        title: "Other session".into(),
        ..Default::default()
    };
    app.sessions_page.sessions.push(session);
    vibe_rs::sessions_page::relayout(&mut app);
    redraw(&mut app);
    let row = app.sessions_page.browse.list.rows_of(1).next().unwrap();
    // The list empties for a reload while the mouse rests on the old row.
    app.sessions_page.sessions.clear();
    app.sessions_page.loading = true;
    vibe_rs::sessions_page::relayout(&mut app);
    let base = redraw(&mut app)[(row.x, row.y)].bg;
    app.view.mouse_position = Some((row.x + 2, row.y));

    let buffer = redraw(&mut app);
    assert_eq!(buffer[(row.x, row.y)].bg, base);
    assert_eq!(buffer[(row.x + 10, row.y)].bg, base);
}
