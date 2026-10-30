//! Completion popup scroll reconciliation, skill origins and the selected entry.

use ratatui::buffer::Buffer;
use ratatui::style::Modifier;
use vibe_rs::app::App;
use vibe_rs::completion_manager::{self, CompletionEntry};
use vibe_rs::skill::SkillOrigin;
use vibe_rs::ui::completion_popup;

use crate::support::{render, rows};

#[test]
fn moving_up_past_the_widest_file_keeps_the_selection_in_view() {
    let mut app = App::default();
    app.completion.entries = std::iter::once("@a_longest_name.md".to_owned())
        .chain(('b'..='m').map(|letter| format!("@{letter}.md")))
        .map(|label| CompletionEntry {
            label,
            description: String::new(),
            origin: None,
        })
        .collect();
    for _ in 0..11 {
        completion_manager::navigate(&mut app, false);
        completion_popup::reconcile_scroll(&mut app, 80);
    }
    assert_eq!(app.completion.selected, 2);
    assert_eq!(app.completion.scroll, 2, "the selection is the top row");
}

const HEIGHT: u16 = 8;

/// The popup at `width` columns with its first entry selected: `/deploy` first, or `/model`.
fn popup(width: u16, deploy_first: bool) -> Buffer {
    let deploy = CompletionEntry {
        label: "/deploy".into(),
        description: "Deploy the app".into(),
        origin: Some(SkillOrigin::Project),
    };
    let model = CompletionEntry {
        label: "/model".into(),
        description: "Select active model".into(),
        origin: None,
    };
    let mut app = App::default();
    app.completion.entries = if deploy_first {
        vec![deploy, model]
    } else {
        vec![model, deploy]
    };
    render(width, HEIGHT, |f| {
        completion_popup::draw(&mut app, f, f.area())
    })
}

fn row_of(buffer: &Buffer, needle: &str) -> u16 {
    rows(buffer)
        .iter()
        .position(|row| row.contains(needle))
        .expect("row") as u16
}

/// The screen column where `text` starts in `row`: rows hold multi-byte glyphs (`│`, `·`).
fn column(row: &str, text: &str) -> u16 {
    let byte = row.find(text).expect("text");
    row[..byte].chars().count() as u16
}

#[test]
fn a_skill_description_starts_with_its_origin() {
    let buffer = popup(80, true);
    let deploy = &rows(&buffer)[row_of(&buffer, "/deploy") as usize];

    assert!(deploy.contains("project · Deploy the app"), "{deploy:?}");
}

#[test]
fn a_built_in_description_has_no_origin_and_shares_the_column() {
    let buffer = popup(80, true);
    let rows = rows(&buffer);
    let deploy = &rows[row_of(&buffer, "/deploy") as usize];
    let model = &rows[row_of(&buffer, "/model") as usize];

    assert_eq!(
        column(deploy, "project"),
        column(model, "Select active model")
    );
    assert!(!model.contains('·'), "{model:?}");
}

#[test]
fn the_origin_stands_out_from_the_rest_of_the_description() {
    let buffer = popup(80, false);
    let y = row_of(&buffer, "/deploy");
    let row = &rows(&buffer)[y as usize];
    let origin_x = column(row, "project");
    let desc_x = column(row, "Deploy the");

    assert_eq!(buffer[(origin_x, y)].symbol(), "p");
    assert_eq!(buffer[(desc_x, y)].symbol(), "D");
    assert_ne!(buffer[(origin_x, y)].fg, buffer[(desc_x, y)].fg);
}

#[test]
fn the_selected_entry_reverses_its_name_and_italicizes_its_whole_description() {
    let buffer = popup(80, true);
    let y = row_of(&buffer, "/deploy");
    let row = &rows(&buffer)[y as usize];
    let at = |text: &str| buffer[(column(row, text), y)].modifier;

    assert!(at("/deploy").contains(Modifier::REVERSED));
    assert!(at("project").contains(Modifier::ITALIC));
    assert!(at("Deploy the").contains(Modifier::ITALIC));
    assert!(!at("project").contains(Modifier::REVERSED));
}
