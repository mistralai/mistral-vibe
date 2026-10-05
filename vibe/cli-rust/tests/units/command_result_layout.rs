//! Command-result markdown layout: headings carry no margins (Python parity).

use vibe_rs::ui::markdown::command_result;

#[test]
fn headings_sit_directly_against_their_content() {
    // Python ground truth (measured): the stats heading is tight against its
    // list, one blank separates the sections, and the second heading is tight
    // against its list too.
    let text = "## Agent Statistics\n\n- **Steps**: 0\n\n## Model & Provider\n\n- **Model**: x";
    let body: Vec<String> = command_result(text, 80)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    let heading = body
        .iter()
        .position(|line| line.contains("Agent Statistics"))
        .unwrap();
    let model = body
        .iter()
        .position(|line| line.contains("Model & Provider"))
        .unwrap();
    assert!(!body[heading + 1].trim().is_empty(), "stats heading tight");
    assert!(body[model - 1].trim().is_empty(), "one section gap");
    assert!(
        !body[model - 2].trim().is_empty(),
        "only one blank before the heading"
    );
    assert!(!body[model + 1].trim().is_empty(), "model heading tight");
}

#[test]
fn assistant_headings_keep_their_margins() {
    // The tcss rule is scoped to command messages: assistant bodies keep
    // Textual's heading margins.
    let text = "## One\n\npara";
    let assistant = vibe_rs::ui::markdown::render(text, 80);
    let command = command_result(text, 80);
    assert!(assistant.len() > command.len());
}

#[test]
fn the_first_block_sits_directly_against_the_second() {
    // Python ground truth (measured): the first-child tcss rule strips the
    // first block's whole margin, so a paragraph-first body renders its first
    // two blocks tight, then one blank before the next block.
    let text = "Cleared 3 messages.\n\nSession saved for resume.\n\n- **Keep**: history";
    let body: Vec<String> = command_result(text, 80)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    let first = body
        .iter()
        .position(|line| line.contains("Cleared 3 messages."))
        .unwrap();
    let second = body
        .iter()
        .position(|line| line.contains("Session saved"))
        .unwrap();
    let list = body
        .iter()
        .position(|line| line.contains("Keep: history"))
        .unwrap();
    assert!(!body[first + 1].trim().is_empty(), "first block tight");
    assert!(body[list - 1].trim().is_empty(), "one blank before list");
    assert!(
        !body[list - 2].trim().is_empty(),
        "only one blank before the list"
    );
    assert_eq!(second, first + 1);
}
