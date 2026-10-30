//! Boxed fence copies: rails and rules are layout; code text copies clean.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use serde_json::json;

use vibe_rs::app::{App, Selection};
use vibe_rs::selection::{region, RegionId, ScrollTarget};
use vibe_rs::ui::markdown;
use vibe_rs::utils::transcript_cache::LayoutEntry;

const WIDTH: u16 = 120;
const CODE: &str = concat!(
    "fn main() {\n",
    "    let value = 1;\n",
    "\n",
    "    if value > 0 {\n",
    "        println!(\"{value}\");\n",
    "    }\n",
    "}",
);

fn fenced_app(code: &str) -> App {
    let text = format!("```rust\n{code}\n```");
    let mut app = App::default();
    app.view.transcript.add(&json!({
        "entry": {
            "id": "m1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "generationStatus": "completed",
            "createdAt": 1_787_593_260_000u64, "updatedAt": 1_787_593_260_000u64,
            "turnId": "t1", "relatedEntryId": null,
        }
    }));
    let height = markdown::render(&text, WIDTH).len() as u16;
    app.view
        .transcript_cache
        .ensure_context(WIDTH, WIDTH, 0, false);
    app.view.transcript_cache.store_layout(
        app.view.transcript.revision(),
        WIDTH,
        height,
        vec![LayoutEntry {
            index: 0,
            top: 0,
            height,
            prewrapped: true,
        }],
        None,
    );
    app.view.selection_region.area = Rect::new(0, 0, WIDTH, 40);
    app.view.selection_region.document = true;
    // The copy selects the message only: the banner sits above it.
    let banner = app.view.banner.view(&app.session.startup_config, WIDTH);
    let top = i32::from(banner.lines().len() as u16 + 1);
    app.view.selection_region.end_exclusive = true;
    app.selection.region = Some(Selection {
        owner: RegionId::Main,
        // From the box's top rule row to its bottom one: both rules drop.
        anchor: (2, top + 2),
        head: (WIDTH - 2, top + i32::from(height) - 1),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: ScrollTarget::Transcript,
        table_cell: None,
        text: String::new(),
    });
    app
}

#[test]
fn a_boxed_fence_renders_rails_and_rules() {
    let text = format!("```rust\n{CODE}\n```");
    let rows: Vec<String> = markdown::render(&text, WIDTH)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    // The message opens with a blank row, the fence with its own top margin.
    assert!(rows[2].starts_with("  ┌─ rust ─") && rows[2].ends_with('┐'));
    assert!(rows[3].starts_with("  │ fn main() {") && rows[3].ends_with('│'));
    assert!(rows[5].starts_with("  │  ") && rows[5].ends_with('│'));
    assert!(rows.last().unwrap().starts_with("  └"));
}

#[test]
fn copying_a_boxed_fence_copies_only_its_code() {
    assert_eq!(region::extract_document(&fenced_app(CODE)).unwrap(), CODE);
}

#[test]
fn copying_a_fence_keeps_code_that_quotes_the_box_glyphs() {
    // The fence border's color tells chrome from content quoting the glyphs.
    let code = "┌──────┐\n────────\n│ keep │\n└──────┘";
    assert_eq!(region::extract_document(&fenced_app(code)).unwrap(), code);
}

#[test]
fn copying_keeps_rail_colored_content_inside_a_fence() {
    // A syntax color can equal the border on ANSI themes; chrome is
    // positional, so rails-colored content still copies.
    use ratatui::prelude::Widget;
    use ratatui::widgets::Paragraph;
    use vibe_rs::ui::theme;

    let text = format!("```rust\n{CODE}\n```");
    let lines = markdown::render(&text, WIDTH);
    let area = Rect::new(0, 0, WIDTH, lines.len() as u16);
    let mut buf = Buffer::empty(area);
    for (y, line) in lines.iter().enumerate() {
        Paragraph::new(line.clone()).render(
            Rect {
                y: y as u16,
                height: 1,
                ..area
            },
            &mut buf,
        );
    }
    // A `│` mid-code painted in the border color: the collision case.
    buf.cell_mut((8, 4))
        .unwrap()
        .set_symbol("│")
        .set_style(ratatui::style::Style::default().fg(theme::md_fence_border()));
    let mut app = App::default();
    app.view.selection_region.area = area;
    app.selection.region = Some(Selection {
        owner: RegionId::Main,
        anchor: (0, 0),
        head: (area.right() - 1, i32::from(area.bottom() - 1)),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: ScrollTarget::Transcript,
        table_cell: None,
        text: String::new(),
    });
    let copied = region::extract(&buf, &region::spans(&app, &buf, area));
    // The content rail copies; the box's rails do not.
    assert_eq!(copied.matches('│').count(), 1);
    assert!(copied.contains("fn main() {"));
}

#[test]
fn a_bare_or_cramped_fence_drops_the_language_label() {
    let top = |text: &str, width: u16| -> String {
        let rows = markdown::render(text, width);
        rows[2].spans.iter().map(|s| s.content.as_ref()).collect()
    };
    assert!(top("```\nx\n```", WIDTH).starts_with("  ┌──"));
    assert!(top("```typescript\nx\n```", 16).starts_with("  ┌──"));
}

#[test]
fn a_narrow_fence_renders_bare() {
    // Below five content columns the box cannot fit its rails and text.
    let rows: Vec<String> = markdown::render("```rust\nlet x = 1;\n```", 8)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    assert!(rows
        .iter()
        .all(|row| { !row.contains('│') && !row.contains('┌') && !row.contains('└') }));
    assert!(rows.iter().any(|row| row.contains("let")));
}
