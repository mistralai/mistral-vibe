//! The bottom bar's segments: the cwd and its copy icon once shortened, the branch and PR link, and the order they shrink in as the row narrows.

use ratatui::style::Modifier;
use serde_json::json;

use vibe_rs::app::App;
use vibe_rs::server::PublicSessionArtifact;
use vibe_rs::ui::bottom_bar::{self, BarLink, BarLinkAction};
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

/// The PR/MR link's hit area and action, past the cwd's copy icon.
fn pr_link(app: &App) -> Option<&BarLink> {
    app.view
        .bottom_bar_links
        .iter()
        .find(|link| !matches!(link.action, BarLinkAction::CopyPath(_)))
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

    assert!(
        row.starts_with("/tmp/project  ● PR #123 mergeable "),
        "{row:?}"
    );
    assert!(row.ends_with(" ctx ▎      23k/600k"), "{row:?}");
}

#[test]
fn the_state_dot_carries_the_color_and_the_label_stays_plain() {
    let mut app = app();
    let buffer = crate::support::bottom_bar(&mut app, 120);
    let row = crate::support::row(&buffer, 0);
    let dot = row.chars().position(|c| c == '●').expect("dot") as u16;
    let style = |x: u16| buffer[(x, 0)].style();

    assert_ne!(
        style(dot),
        style(dot + 2),
        "the dot is colored, the label is not"
    );
    assert!(!style(dot + 2).add_modifier.contains(Modifier::UNDERLINED));
    assert_eq!(
        style(dot + 10),
        style(0),
        "the status reads muted like the cwd"
    );
}

#[test]
fn the_link_sits_after_the_cwd_and_its_hit_area_covers_the_dot_and_status() {
    let mut app = app();
    let row = bottom_bar_row(&mut app, 120);
    let link = pr_link(&app).expect("link");
    let x = row.chars().position(|c| c == '●').expect("dot") as u16;
    let width = "● PR #123 mergeable".chars().count() as u16;

    assert_eq!((link.area.x, link.area.width), (x, width));
    assert!(matches!(link.action, BarLinkAction::ListArtifacts));
    assert!(bottom_bar::link_at(&app, (x, 0)).is_some());
    assert!(bottom_bar::link_at(&app, (x - 1, 0)).is_none());

    // `/config` and `/release-notes` draw over the footer and block its clicks.
    app.config_screen.open = true;
    assert!(bottom_bar::link_at(&app, (x, 0)).is_none());
    app.config_screen.open = false;
    app.release_notes.open = true;
    assert!(bottom_bar::link_at(&app, (x, 0)).is_none());
}

#[test]
fn several_artifacts_paint_a_count_that_opens_the_modal() {
    let mut app = app();
    app.session.artifacts = vec![pr("a1", 123, None), pr("a2", 124, None)];

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.contains("  PRs (2) "));
    assert!(!row.contains('●'), "a count has no single state");
    let link = pr_link(&app).expect("link");
    assert!(matches!(link.action, BarLinkAction::ListArtifacts));
}

#[test]
fn narrowing_turns_gauges_into_levels_then_drops_the_pr_status_before_the_cwd_shortens() {
    let mut app = app();
    app.session.cwd = Some("~/work/dashboard/vibe/cli-rust".into());
    let mut row = |width| bottom_bar_row(&mut app, width).trim_end().to_owned();

    assert_eq!(
        row(73),
        "~/work/dashboard/vibe/cli-rust  ● PR #123 mergeable   ctx ▎      23k/600k"
    );
    assert_eq!(
        row(70),
        "~/work/dashboard/vibe/cli-rust  ● PR #123 mergeable           ctx ▁ 4%",
        "the gauge turns into a level first"
    );
    assert_eq!(
        row(58),
        "~/work/dashboard/vibe/cli-rust  ● PR #123         ctx ▁ 4%",
        "then the status goes"
    );
    assert_eq!(
        row(49),
        "…/dashboard/vibe/cli-rust ⧉  ● PR #123   ctx ▁ 4%",
        "only then the cwd shortens and gains its copy icon"
    );
    assert_eq!(row(30), "…/cli-rust ⧉  ● #123  ctx ▁ 4%");
    assert_eq!(row(20), "…/cli-rust ⧉   ctx ▁");
}

#[test]
fn a_hidden_link_has_no_hit_area() {
    let mut app = app();
    let row = bottom_bar_row(&mut app, 20);

    assert!(!row.contains("123"), "{row:?}");
    assert!(pr_link(&app).is_none());
}

#[test]
fn the_footer_names_neither_the_branch_nor_the_model() {
    let mut app = app();

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.starts_with("/tmp/project  ● PR #123"), "{row:?}");
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
fn the_cwd_shrinks_to_its_last_directories_once_the_gauge_is_a_level_and_the_status_gone() {
    let mut app = app();
    app.session.cwd = Some("~/work/dashboard/vibe/cli-rust".into());

    let row = bottom_bar_row(&mut app, 45);

    assert_eq!(row, "…/vibe/cli-rust ⧉  ● PR #123         ctx ▁ 4%");
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
fn a_pending_quit_hint_hides_the_copy_icon_branch_and_link() {
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
    assert!(
        !row.contains(['⧉', '⎇']) && !row.contains("#123"),
        "{row:?}"
    );
    assert!(app.view.bottom_bar_links.is_empty());
}

#[test]
fn without_a_context_window_the_left_side_stands_alone() {
    let mut app = app();
    app.session.tokens = (5, 0);

    let row = bottom_bar_row(&mut app, 120);

    assert_eq!(row.trim_end(), "/tmp/project  ● PR #123 mergeable");
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

#[test]
fn hovering_the_link_shows_the_hand_and_highlights_only_the_link() {
    use crossterm::event::MouseEventKind;
    use vibe_rs::pointer::{self, Shape};

    let mut app = app();
    app.set_status(vibe_rs::app::Status::Ready);
    // A seen link is not bold, so the hover's bold shows on the test theme's reset background.
    app.session.artifacts[0].viewed_at = Some("2026-10-18T11:00:00+00:00".into());
    let row = bottom_bar_row(&mut app, 120);
    let dot = row.chars().position(|c| c == '●').expect("dot") as u16;
    let plain = crate::support::bottom_bar(&mut app, 120)[(dot + 4, 0)].style();

    crate::support::send_mouse(
        &mut app,
        crate::support::mouse(MouseEventKind::Moved, dot + 4, 0),
    );
    let buffer = crate::support::bottom_bar(&mut app, 120);

    assert_eq!(pointer::shape(&app), Shape::Pointer);
    assert_ne!(buffer[(dot + 4, 0)].style(), plain);
    assert_ne!(buffer[(dot - 1, 0)].style(), buffer[(dot + 4, 0)].style());
}
