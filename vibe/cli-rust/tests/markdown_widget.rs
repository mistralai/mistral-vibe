//! Standalone `Markdown` widget rendering (`render_widget`, the
//! theme-selection preview's renderer): Textual default styles and
//! margins. A separate binary because the palette it renders with is
//! process-global (`theme::set_active`); the chat profile's rules-dropped
//! and task-list pins live in `units/markdown_parse.rs` instead.

use vibe_rs::ui::markdown;
use vibe_rs::ui::theme;

const PREVIEW: &str = "### Heading\n\n**Bold**, *italic*, and `inline code`.\n\n- Bullet point\n- Another bullet point\n\n1. First item\n2. Second item\n\n```python\ndef greet(name: str = \"World\") -> str:\n    return f\"Hello, {name}!\"\n```\n\n> Blockquote\n\n---\n\n| Column 1 | Column 2 |\n|----------|----------|\n| Item 1   | Item 2   |";

fn line_text(line: &ratatui::text::Line<'_>) -> String {
    let mut out = String::new();
    for span in &line.spans {
        out.push_str(&span.content);
    }
    out.trim_end().to_string()
}

#[test]
fn widget_preview_matches_the_textual_markdown_layout() {
    theme::set_active("tokyo-night");
    let lines = markdown::render_widget(PREVIEW, 64);
    let rows: Vec<String> = lines.iter().map(line_text).collect();
    let expected = [
        "",
        "  Heading",
        "",
        "  Bold, italic, and inline code.",
        "",
        "  • Bullet point",
        "  • Another bullet point",
        "",
        "   1. First item",
        "   2. Second item",
        "",
        "",
        "    def greet(name: str = \"World\") -> str:",
        "        return f\"Hello, {name}!\"",
        "",
        "",
        "  ▌ Blockquote",
        "",
        "",
        "  ────────────────────────────────────────────────────────────",
        "",
        "  ┌────────────────────────────┬─────────────────────────────┐",
        "  │ Column 1                   │ Column 2                    │",
        "  ├────────────────────────────┼─────────────────────────────┤",
        "  │ Item 1                     │ Item 2                      │",
        "  └────────────────────────────┴─────────────────────────────┘",
        "",
    ];
    assert_eq!(rows, expected);
}

#[test]
fn widget_renders_task_list_markers() {
    theme::set_active("tokyo-night");
    let lines = markdown::render_widget("- [ ] open\n- [x] done", 64);
    let rows: Vec<String> = lines.iter().map(line_text).collect();
    assert_eq!(rows, vec!["  • [ ] open", "  • [x] done", ""]);
}
