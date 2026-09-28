//! Remote-project protocol, ranking, filtering, navigation, and form editing.

use ratatui::{backend::TestBackend, Terminal};
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::chat_input::Action;
use vibe_rs::server::proto_projects::{GitInfo, OpenResponse, PickerView};
use vibe_rs::vibe_code_project::{
    items::{build_project_picker_items, normalize_repo_url, repo_url_label, Item},
    Field, State,
};

fn view() -> PickerView {
    serde_json::from_value(json!({
        "context": {"repoUrl": "https://github.com/org/repo.git", "repoName": "repo", "savedLink": {"repoUrl": "git@github.com:org/repo.git", "projectId": "saved"}},
        "state": {"nextCursor": null, "projects": [
            {"projectId": "multi", "name": "A multi", "repositories": [{"repoUrl": "https://github.com/org/repo"}, {"repoUrl": "other"}]},
            {"projectId": "exact", "name": "Z exact", "repositories": [{"repoUrl": "git@github.com:ORG/REPO.git"}]},
            {"projectId": "saved", "name": "Saved", "repositories": [{"repoUrl": "https://github.com/org/repo.git"}]},
            {"projectId": "readonly", "name": "Read only", "isReadOnly": true, "repositories": [{"repoUrl": "https://github.com/org/repo"}]},
            {"projectId": "unrelated", "name": "Other", "repositories": [{"repoUrl": "https://github.com/org/other"}]}
        ]},
        "git": {"defaultBranch": "main", "branch": "feature"}
    })).unwrap()
}

fn ids(view: &PickerView, query: &str) -> Vec<String> {
    build_project_picker_items(view, query)
        .iter()
        .filter_map(|i| i.option_id(view))
        .collect()
}

#[test]
fn ranks_current_single_and_multi_repository_matches_and_hides_ineligible() {
    assert_eq!(
        ids(&view(), ""),
        [
            "project:saved",
            "project:exact",
            "project:multi",
            "action:create",
            "action:unlink"
        ]
    );
}

#[test]
fn saved_link_for_a_different_remote_does_not_rank_as_current() {
    let mut view = view();
    view.context.saved_link.as_mut().unwrap().repo_url = "https://github.com/other/repo".into();
    assert!(matches!(
        build_project_picker_items(&view, "")[1],
        Item::Project { rank: 1, .. }
    ));
}

#[test]
fn filters_names_and_all_repository_urls_case_insensitively() {
    assert_eq!(
        ids(&view(), "  z EXACT  "),
        ["project:exact", "action:create", "action:unlink"]
    );
    assert_eq!(
        ids(&view(), "OTHER"),
        ["project:multi", "action:create", "action:unlink"]
    );
}

#[test]
fn empty_search_retains_actions_and_only_recommends_create_without_more_pages() {
    let mut view = view();
    let items = build_project_picker_items(&view, " New project ");
    assert_eq!(
        items[3],
        Item::Create {
            name: "New project".into(),
            recommended: true
        }
    );
    view.state.next_cursor = Some(String::new());
    let items = build_project_picker_items(&view, "New project");
    assert_eq!(items[1], Item::LoadMore);
    assert!(items.iter().any(|i| matches!(
        i,
        Item::Create {
            recommended: false,
            ..
        }
    )));
}

#[test]
fn create_falls_back_to_repository_name_then_url() {
    let mut view = view();
    view.context.repo_name = "  ".into();
    view.context.saved_link = None;
    view.state.projects.clear();
    let items = build_project_picker_items(&view, "");
    assert_eq!(
        items.last(),
        Some(&Item::Create {
            name: "repo".into(),
            recommended: true
        })
    );
}

#[test]
fn refresh_preserves_option_identity_and_resets_missing_selection() {
    let mut state = State {
        view: Some(view()),
        ..State::default()
    };
    state.show_picker();
    state.refresh(Some("project:exact".into()));
    state.query.text = "exact".into();
    state.refresh(None);
    assert_eq!(
        state.items[state.selected]
            .option_id(state.view.as_ref().unwrap())
            .as_deref(),
        Some("project:exact")
    );
    state.query.text = "missing".into();
    state.refresh(None);
    assert!(matches!(state.items[state.selected], Item::Create { .. }));
}

#[test]
fn arrows_wrap_and_skip_section_rows() {
    let mut state = State {
        view: Some(view()),
        ..State::default()
    };
    state.show_picker();
    state.navigate(false);
    assert_eq!(state.items[state.selected], Item::Unlink);
    state.navigate(true);
    assert_eq!(
        state.items[state.selected]
            .option_id(state.view.as_ref().unwrap())
            .as_deref(),
        Some("project:saved")
    );
    state.show_picker();
    state.navigate(true);
    assert_eq!(
        state.items[state.selected]
            .option_id(state.view.as_ref().unwrap())
            .as_deref(),
        Some("project:exact")
    );
}

#[test]
fn form_prefill_is_selected_and_reuses_unicode_safe_editing() {
    let mut field = Field::new("repo".into());
    field.edit(Action::Insert('é'));
    assert_eq!(field.text, "é");
    field.edit(Action::Insert('界'));
    field.edit(Action::CursorLeft);
    field.edit(Action::DeleteLeft);
    assert_eq!(field.text, "界");
    assert_eq!(field.cursor, 0);
    field.edit(Action::Insert('\n'));
    assert_eq!(field.text, "界");
}

#[test]
fn default_branch_uses_branch_then_main() {
    for (default, branch, expected) in [
        (Some("trunk"), Some("feature"), "trunk"),
        (Some(""), Some("feature"), "feature"),
        (None, None, "main"),
    ] {
        let git = GitInfo {
            default_branch: default.map(str::to_owned),
            branch: branch.map(str::to_owned),
        };
        assert_eq!(git.suggested_default_branch(), expected);
    }
}

#[test]
fn repository_normalization_and_display_match_python() {
    assert_eq!(
        normalize_repo_url(" git@github.com:Org/Repo.git/ "),
        "github.com/org/repo"
    );
    assert_eq!(
        normalize_repo_url("https://github.com/Org/Repo.git"),
        "github.com/org/repo"
    );
    assert_eq!(
        repo_url_label("ssh://git@github.com/Org/Repo.git"),
        "github.com/Org/Repo"
    );
    assert_eq!(repo_url_label("git@host:Org/Repo.git"), "host/Org/Repo");
}

#[test]
fn protocol_requires_picker_id_but_tolerates_new_projection_fields() {
    let raw = json!({"pickerId": "id", "view": {
        "context": {"repoUrl": "repo", "repoName": "repo"},
        "state": {"projects": [], "nextCursor": ""}, "git": {}, "futureField": true
    }, "resolvedProjectId": null});
    let response: OpenResponse = serde_json::from_value(raw.clone()).unwrap();
    assert_eq!(response.picker_id, "id");
    assert!(response.view.state.next_cursor.is_some());
    let mut invalid = raw;
    invalid.as_object_mut().unwrap().remove("pickerId");
    assert!(serde_json::from_value::<OpenResponse>(invalid).is_err());
}

#[test]
fn command_is_available_and_never_runs_as_a_side_channel() {
    assert_eq!(
        vibe_rs::commands::parse("/REMOTE-PROJECT ignored"),
        Some("/remote-project")
    );
    assert!(!vibe_rs::commands::is_side_channel("/remote-project"));
}

#[test]
fn mouse_hit_testing_accounts_for_horizontal_scroll_and_wide_characters() {
    let mut field = Field::new("é界abc".into());
    field.scroll = 3;
    assert_eq!(field.byte_at(0), 5);
    assert_eq!(field.byte_at(2), 7);
    assert_eq!(field.byte_at(100), field.text.len());
}

#[test]
fn horizontal_scroll_does_not_split_wide_characters() {
    let mut field = Field::new("界abc".into());
    field.scroll_to_cursor(5);
    assert_eq!(field.scroll, 2);
    assert_eq!(field.byte_at(0), 3);
    assert_eq!(field.byte_at(1), 4);
}

#[test]
fn search_uses_full_unicode_case_folding() {
    let mut view = view();
    view.state.projects[1].name = "Straße ﬃ ᾲ".into();
    for query in ["STRASSE", "ffi", "ὰι"] {
        assert_eq!(ids(&view, query)[0], "project:exact", "query={query}");
    }
}

#[test]
fn field_bounds_preserve_utf8_and_allow_replacing_selected_text() {
    let mut field = Field::new("é".repeat(vibe_rs::vibe_code_project::MAX_INPUT_BYTES));
    assert_eq!(
        field.text.len(),
        vibe_rs::vibe_code_project::MAX_INPUT_BYTES
    );
    field.anchor = None;
    field.edit(Action::Insert('x'));
    assert_eq!(
        field.text.len(),
        vibe_rs::vibe_code_project::MAX_INPUT_BYTES
    );
    field.edit(Action::SelectAll);
    field.edit(Action::Insert('界'));
    assert_eq!(field.text, "界");
}

#[test]
fn page_up_from_first_project_keeps_a_selectable_row() {
    let mut state = State {
        view: Some(view()),
        ..State::default()
    };
    state.show_picker();
    state.search_focused = false;
    state.list_area.height = 5;
    let selected = state.selected;

    state.page(false);

    assert_eq!(state.selected, selected);
    assert!(state.items[state.selected].selectable());
}

#[test]
fn wide_project_names_keep_the_metadata_columns_aligned() {
    let mut picker_view = view();
    picker_view.state.projects[2].name = "界".repeat(28);
    let mut app = App::default();
    app.vibe_code_project.open = true;
    app.vibe_code_project.view = Some(picker_view);
    app.vibe_code_project.show_picker();

    let mut terminal = Terminal::new(TestBackend::new(100, 40)).expect("terminal");
    terminal
        .draw(|frame| {
            let area = frame.area();
            vibe_rs::ui::vibe_code_project::draw(&mut app, frame, area);
        })
        .expect("draw");

    let row = app.vibe_code_project.list_area.y + 1;
    let count_column = app.vibe_code_project.list_area.x + 1 + 48 + 3;
    assert_eq!(
        terminal.backend().buffer()[(count_column, row)].symbol(),
        "1"
    );
}
