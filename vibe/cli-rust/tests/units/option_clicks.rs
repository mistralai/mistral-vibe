//! Lists with per-option actions confirm on a click on the highlight; the others on one click.

use std::sync::Arc;

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use serde_json::json;
use vibe_rs::app::{App, ModelOption};
use vibe_rs::mouse::{self, MouseTarget};
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::{config, config_edit, config_fields, log_level_picker, resume_picker, subagents};

const AREA: Rect = Rect::new(0, 0, 80, 20);

fn event(kind: MouseEventKind, row: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column: 4,
        row,
        modifiers: KeyModifiers::NONE,
    }
}

/// Press then release on `row` through the central mouse router.
fn click(app: &mut App, row: u16) {
    click_at(app, 4, row);
}

/// Press then release on a cell through the central mouse router.
fn click_at(app: &mut App, column: u16, row: u16) {
    let client = Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _rx) = tokio::sync::mpsc::channel(1);
    for kind in [
        MouseEventKind::Down(MouseButton::Left),
        MouseEventKind::Up(MouseButton::Left),
    ] {
        let event = MouseEvent {
            column,
            ..event(kind, row)
        };
        mouse::handle(app, &client, &config_tx, event);
    }
}

fn app_on(target: MouseTarget) -> App {
    let mut app = App::default();
    mouse::register_region(&mut app, AREA, target);
    app
}

#[test]
fn a_subagent_row_opens_on_one_click() {
    let mut app = app_on(MouseTarget::SubagentList);
    app.subagents.replace_child_session(PublicChildSession {
        id: "a".into(),
        status: SessionStatus::Running,
        ..Default::default()
    });
    subagents::refresh(&mut app);
    app.subagents.list.options.layout_rows(2, AREA);

    click(&mut app, 1);
    assert_eq!(app.subagents.list.options.selected(), 1);
    assert_eq!(app.subagents.viewed_subagent_id.as_deref(), Some("a"));
}

#[test]
fn a_config_field_opens_its_editor_on_the_second_click() {
    let mut app = app_on(MouseTarget::Config);
    config::apply_loaded(
        &mut app,
        config_fields::parse(&json!({"fields": [
            {"name": "a", "path": "/a", "kind": "int", "value": 1},
            {"name": "b", "path": "/b", "kind": "int", "value": 2},
        ]})),
    );
    app.config_screen.list.layout_rows(2, AREA);

    click(&mut app, 1);
    assert_eq!(app.config_screen.list.selected(), 1);
    assert!(app.config_screen.edit.is_none());

    click(&mut app, 1);
    assert_eq!(app.config_screen.edit.unwrap().field.path, "/b");
}

#[test]
fn a_config_choice_commits_on_one_click() {
    let mut app = App::default();
    let loaded = config_fields::parse(&json!({"fields": [
        {"name": "flag", "path": "/flag", "kind": "bool", "value": true},
    ]}));
    config_edit::open(&mut app, loaded.fields[0].clone());
    let edit = app.config_screen.edit.as_mut().unwrap();
    edit.list.layout_rows(2, AREA);
    let clicked = |app: &mut App| {
        [
            MouseEventKind::Down(MouseButton::Left),
            MouseEventKind::Up(MouseButton::Left),
        ]
        .map(|kind| config_edit::mouse(app, event(kind, 1)))
        .contains(&true)
    };

    assert!(clicked(&mut app));
    assert_eq!(app.config_screen.edit.as_ref().unwrap().draft, "False");
}

#[test]
fn an_mcp_auth_action_runs_on_one_click() {
    let mut app = app_on(MouseTarget::McpOAuth);
    app.mcp_oauth.auth_url = Some("https://auth".into());
    app.mcp_oauth
        .list
        .layout(&[None, None, Some(0), Some(1), Some(2)], AREA);

    click(&mut app, 4);
    assert_eq!(app.mcp_oauth.list.selected(), 2);
    assert!(app.mcp_oauth.auth_url_visible);
}

#[test]
fn a_connector_auth_action_runs_on_one_click() {
    let mut app = app_on(MouseTarget::ConnectorAuth);
    app.connector_auth.fetched = true;
    app.connector_auth.auth_url = Some("https://auth".into());
    app.connector_auth
        .list
        .layout(&[None, None, Some(0), Some(1), Some(2)], AREA);

    click(&mut app, 4);
    assert_eq!(app.connector_auth.list.selected(), 2);
    assert!(app.connector_auth.auth_url_visible);
}

#[test]
fn a_model_opens_its_thinking_levels_on_the_second_click() {
    let mut app = app_on(MouseTarget::ModelPicker);
    app.model_picker.models = vec![ModelOption {
        alias: "a".into(),
        thinking_levels: vec!["low".into(), "high".into()],
        ..Default::default()
    }];
    app.model_picker.open = true;
    app.model_picker.list.layout_rows(2, AREA);

    click(&mut app, 1);
    assert_eq!(app.model_picker.list.selected(), 1);
    assert!(app.model_picker.open);

    click(&mut app, 1);
    assert!(!app.model_picker.open);
    assert!(app.thinking_picker.open);
    assert_eq!(app.thinking_picker.model_pick.unwrap().alias, "a");
}

#[test]
fn a_thinking_level_commits_on_the_second_click() {
    let mut app = app_on(MouseTarget::ThinkingPicker);
    vibe_rs::thinking_picker::open(&mut app);
    let levels = app.thinking_picker.levels.len();
    app.thinking_picker.list.layout_rows(levels, AREA);

    click(&mut app, 2);
    assert_eq!(app.thinking_picker.list.selected(), 2);
    assert!(app.thinking_picker.open);

    click(&mut app, 2);
    assert!(!app.thinking_picker.open);
}

#[test]
fn a_log_level_row_highlights_and_its_badge_toggles_on_one_click() {
    let mut app = app_on(MouseTarget::LogLevelPicker);
    app.log_level_picker.list.layout_rows(5, AREA);
    app.log_level_picker.badges = vec![(Rect::new(20, 2, 6, 1), log_level_picker::BADGE_CONFIG)];

    click(&mut app, 2);
    assert_eq!(app.log_level_picker.list.selected(), 2);
    assert_eq!(app.log_level_picker.config, None);

    click_at(&mut app, 22, 2);
    assert_eq!(
        app.log_level_picker.focused_badge,
        log_level_picker::BADGE_CONFIG
    );
    assert_eq!(
        app.log_level_picker.config.as_deref(),
        Some(log_level_picker::options()[2])
    );
}

#[tokio::test]
async fn a_saved_session_resumes_on_the_second_click() {
    let mut app = app_on(MouseTarget::ResumePicker);
    let (tx, _rx) = tokio::sync::mpsc::channel(1);
    app.resume_picker.tx = Some(tx);
    app.resume_picker.open = true;
    app.resume_picker.sessions = ["a", "b"]
        .map(|id| resume_picker::Session {
            id: id.into(),
            ..Default::default()
        })
        .to_vec();
    app.resume_picker.list.layout_rows(2, AREA);

    click(&mut app, 1);
    assert_eq!(app.resume_picker.list.selected(), 1);
    assert!(!app.resume_picker.resuming);

    click(&mut app, 1);
    assert!(app.resume_picker.resuming);
}

#[test]
fn a_remote_project_click_on_the_highlight_while_searching_confirms_it() {
    let mut app = app_on(MouseTarget::RemoteProject);
    let state = &mut app.vibe_code_project;
    state.view = Some(
        serde_json::from_value(json!({
            "context": {"repoUrl": "https://github.com/org/repo.git", "repoName": "repo"},
            "state": {"nextCursor": null, "projects": []},
            "git": {"defaultBranch": "main", "branch": "feature"}
        }))
        .unwrap(),
    );
    state.show_picker();
    state.query.text = "missing".into();
    state.refresh(None);
    let lines: Vec<_> = (0..state.items.len())
        .map(|index| state.items[index].selectable().then_some(index))
        .collect();
    state.list.layout(&lines, AREA);
    let row = state.list.selected() as u16;
    state.search_focused = true;

    click(&mut app, row);
    assert!(!app.vibe_code_project.search_focused);
    assert!(app.vibe_code_project.create.is_some());
}
