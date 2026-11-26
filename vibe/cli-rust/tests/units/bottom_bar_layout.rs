//! The bottom bar's segments: the cwd and its copy icon once shortened, the gauges, and the order they shrink in as the row narrows.

use serde_json::json;

use vibe_rs::app::App;
use vibe_rs::server::PublicSessionArtifact;
use vibe_rs::ui::footer_fit::elide_path;

use crate::support::bottom_bar_row;

const URL: &str = "https://github.com/mistral-ai/vibe/pull/123";

fn pr(id: &str, number: u64, status: Option<&str>) -> PublicSessionArtifact {
    serde_json::from_value(json!({
        "id": id,
        "sessionId": "s",
        "kind": "pull_request",
        "provider": "github",
        "externalKey": format!("github.com/mistral-ai/vibe/pull/{number}"),
        "externalUrl": URL,
        "discoveredAt": "2026-10-18T10:00:00+00:00",
        "details": { "pullRequestNumber": number, "state": "open", "prStatus": status },
    }))
    .expect("artifact")
}

fn app() -> App {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.artifacts = vec![pr("a1", 123, Some("mergeable"))];
    app.session.startup_config.active_model = "mistral-medium-3.5[high]".into();
    app.session.tokens = (23_000, 600_000);
    app.artifacts_modal.enabled = true;
    app
}

#[test]
fn a_wide_row_shows_every_segment_in_full() {
    let row = bottom_bar_row(&mut app(), 120);

    assert!(row.starts_with("/tmp/project "), "{row:?}");
    assert!(!row.contains("#123"), "PRs live on the rail: {row:?}");
    assert!(row.ends_with(" ctx ▎      23k/600k"), "{row:?}");
}

#[test]
fn narrowing_turns_gauges_into_levels_before_the_cwd_shortens() {
    let mut app = app();
    app.session.cwd = Some("~/work/dashboard/vibe/cli-rust".into());
    let mut row = |width| bottom_bar_row(&mut app, width).trim_end().to_owned();

    assert_eq!(
        row(50),
        "~/work/dashboard/vibe/cli-rust ctx ▎      23k/600k"
    );
    assert_eq!(
        row(49),
        "~/work/dashboard/vibe/cli-rust           ctx ▁ 4%",
        "the gauge turns into a level first"
    );
    assert_eq!(
        row(38),
        "…/dashboard/vibe/cli-rust ⧉   ctx ▁ 4%",
        "only then the cwd shortens and gains its copy icon"
    );
    assert_eq!(row(20), "…/cli-rust ⧉   ctx ▁");
}

#[test]
fn the_footer_names_neither_the_branch_nor_the_model() {
    let mut app = app();

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.starts_with("/tmp/project "), "{row:?}");
    assert!(!row.contains('⎇') && !row.contains("mistral"), "{row:?}");
}

#[test]
fn the_gauge_reads_the_token_counts_and_its_short_form_never_more_than_full() {
    let mut app = app();
    app.session.tokens = (700_000, 600_000);

    assert!(bottom_bar_row(&mut app, 120).ends_with(" ctx ██████ 700k/600k"));
    assert!(bottom_bar_row(&mut app, 20).ends_with(" ctx █"));
}

#[test]
fn the_cwd_shrinks_to_its_last_directories_once_the_gauge_is_a_level() {
    let mut app = app();
    app.session.cwd = Some("~/work/dashboard/vibe/cli-rust".into());

    let row = bottom_bar_row(&mut app, 30);

    assert_eq!(row, "…/vibe/cli-rust ⧉     ctx ▁ 4%");
}

#[test]
fn a_long_path_keeps_its_last_components() {
    assert_eq!(
        elide_path("~/work/dashboard/vibe", 21),
        "~/work/dashboard/vibe"
    );
    assert_eq!(elide_path("~/work/dashboard/vibe", 16), "…/dashboard/vibe");
    assert_eq!(elide_path("~/work/dashboard/vibe", 8), "…/vibe");
    assert_eq!(elide_path("~/work/dashboard/vibe", 4), "…ibe");
    assert_eq!(elide_path("~/work/dashboard/vibe", 0), "");
}

#[test]
fn a_long_component_shrinks_word_by_word() {
    let path = "~/code/fix-uv-is-too-old/vibe";
    assert_eq!(elide_path(path, 24), "…/fix-uv-is-too-old/vibe");
    assert_eq!(elide_path(path, 20), "…uv-is-too-old/vibe");
    assert_eq!(elide_path(path, 12), "…old/vibe");
    assert_eq!(elide_path(path, 8), "…/vibe");
}

#[test]
fn a_pending_quit_hint_hides_the_copy_icon() {
    let mut app = app();
    assert_eq!(
        vibe_rs::input::handle_priority_key(
            &mut app,
            &crate::support::stub(),
            crate::support::ctrl('c')
        ),
        Some(false)
    );

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.starts_with("Press Ctrl+C again to quit "), "{row:?}");
    assert!(!row.contains(['⧉', '⎇']), "{row:?}");
    assert!(app.view.bottom_bar_links.is_empty());
}

#[test]
fn without_a_context_window_the_left_side_stands_alone() {
    let mut app = app();
    app.session.tokens = (5, 0);

    let row = bottom_bar_row(&mut app, 120);

    assert_eq!(row.trim_end(), "/tmp/project");
}

#[test]
fn windows_paths_keep_their_last_components_too() {
    assert_eq!(
        elide_path(r"C:\Users\me\work\dashboard", 12),
        r"…\dashboard"
    );
    assert_eq!(
        elide_path(r"C:\Users\me\work\dashboard", 17),
        r"…\work\dashboard"
    );
}

#[test]
fn wide_paths_are_cut_on_whole_cells() {
    assert_eq!(elide_path("~/工作/项目/仓库", 8), "…/仓库");
    assert_eq!(elide_path("~/工作/项目/仓库", 4), "…库");
    assert_eq!(elide_path("~/工作/项目/仓库", 2), "…");
    assert_eq!(elide_path("~/abc", 1), "…");
}
