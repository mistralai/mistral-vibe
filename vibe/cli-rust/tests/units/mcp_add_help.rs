//! `/mcp add --help` keeps its `<...>` placeholders through Markdown rendering.
use vibe_rs::mcp::add_args::add_help;
use vibe_rs::ui::markdown;

#[test]
fn help_keeps_angle_bracket_placeholders() {
    let rendered: String = markdown::command_result(&add_help(), 400)
        .iter()
        .flat_map(|line| line.spans.iter().map(|span| span.content.to_string()))
        .collect();
    assert!(
        rendered.contains("Usage: /mcp add <url> [--name <alias>] [--scope <scope> ...]"),
        "{rendered}"
    );
}
