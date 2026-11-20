//! `/skills` page layout: long values wrap, and a long description never pushes the facts off screen.

use serde_json::json;

use crate::drawn::drawn_value;
use crate::skills_browser_support::{installed_row, open_page, opened, pin};
use crate::support::{render, rows};

#[test]
fn a_long_file_path_wraps_under_its_label_instead_of_being_cut() {
    let path = "/repo/.agents/skills/an-unusually-long-skill-folder-name/SKILL.md";
    let mut lint = pin("lint", "local", "project");
    lint["path"] = json!(path);
    let mut app = opened(vec![lint], None);
    open_page(&mut app, installed_row("lint"));
    let screen = rows(&render(48, 40, |frame| app.draw(frame)));
    assert_eq!(drawn_value(&screen, "/repo/"), path);
}

#[test]
fn a_long_description_never_hides_the_page_s_facts() {
    let path = "/repo/.agents/skills/create-pr/SKILL.md";
    let mut lint = pin("create-pr", "local", "global");
    lint["path"] = json!(path);
    lint["description"] =
        json!("Creates a GitHub PR end to end and watches it until it merges. ".repeat(12));
    let mut app = opened(vec![lint], None);
    open_page(&mut app, installed_row("create-pr"));
    let screen = rows(&render(120, 44, |frame| app.draw(frame)));
    assert!(
        screen.iter().any(|row| row.contains(path)),
        "{}",
        screen.join("\n")
    );
}
