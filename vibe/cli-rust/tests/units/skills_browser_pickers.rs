//! `/skills` version pickers: pending scopes, writes, unpins, previews.

use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::server::SkillScope;
use vibe_rs::skills_browser::labels;
use vibe_rs::skills_browser::model::{Body, Choice};
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::picker::{self, PickerRow};
use vibe_rs::skills_browser::{self, Event};

use crate::skills_browser_support::{
    answer, catalog_row, highlight_pick, highlight_row, installed_row, next, notice, open_page,
    opened, page, page_row, picker_row, press, registry, session, shadowed, shared, shown_body,
    through,
};
use crate::support::stub;

fn pick(choice: Choice) -> PickerRow {
    PickerRow::Pick(choice)
}

fn alias(name: &str) -> Choice {
    Choice::Alias(name.to_owned())
}

#[tokio::test]
async fn each_scope_keeps_its_own_pending_choice_until_install_imports_both() {
    let mut app = opened(Vec::new(), Some(shared()));
    open_page(&mut app, catalog_row("id-deploy"));
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    let open = page(&app).picker.as_ref().unwrap();
    assert_eq!(open.scope, SkillScope::Global);
    let head = [
        PickerRow::Heading("Aliases"),
        pick(alias("latest")),
        pick(alias("stable")),
    ];
    assert_eq!(open.rows[..3], head);
    assert_eq!(
        picker_row(&app),
        Some(pick(alias("latest"))),
        "opens on the choice in effect"
    );
    highlight_pick(&mut app, &pick(Choice::Version(2)));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(page_row(&app), Some(Property::Scope(SkillScope::Global)));
    press(&mut app, &client, KeyCode::Down);
    press(&mut app, &client, KeyCode::Enter);
    highlight_pick(&mut app, &pick(alias("stable")));
    press(&mut app, &client, KeyCode::Enter);
    let chosen: Vec<_> = page(&app)
        .subject
        .pending()
        .unwrap()
        .clone()
        .into_iter()
        .collect();
    let expected = [
        (SkillScope::Global, Choice::Version(2)),
        (SkillScope::Project, alias("stable")),
    ];
    assert_eq!(chosen, expected);
    highlight_row(&mut app, Property::Install);
    press(&mut app, &client, KeyCode::Enter);
    let seen = through(&mut frames, "skills/installed").await;
    let imports: Vec<_> = seen
        .into_iter()
        .filter(|(method, _)| method == "skills/import")
        .map(|(_, params)| params)
        .collect();
    let global = json!({"sessionId": "s", "skillId": "id-deploy", "version": 2, "alias": null, "scope": "global"});
    let project = json!({"sessionId": "s", "skillId": "id-deploy", "version": null, "alias": "stable", "scope": "project"});
    assert_eq!(imports, [global, project]);
}

#[tokio::test]
async fn dont_pin_here_drops_a_scope_from_the_install_but_never_the_last() {
    let mut app = opened(Vec::new(), Some(shared()));
    open_page(&mut app, catalog_row("id-deploy"));
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    let last = page(&app).picker.as_ref().unwrap().rows.last().cloned();
    assert_eq!(
        last,
        Some(PickerRow::Unpin { inert: true }),
        "Install needs a scope"
    );
    press(&mut app, &client, KeyCode::Esc);
    press(&mut app, &client, KeyCode::Down);
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Enter);
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Enter);
    highlight_pick(&mut app, &PickerRow::Unpin { inert: false });
    press(&mut app, &client, KeyCode::Enter);
    let pending = page(&app).subject.pending().unwrap();
    assert_eq!(
        pending.keys().copied().collect::<Vec<_>>(),
        [SkillScope::Project]
    );
    highlight_row(&mut app, Property::Install);
    press(&mut app, &client, KeyCode::Enter);
    let seen = through(&mut frames, "skills/import").await;
    let (_, params) = seen.last().unwrap();
    let wanted = json!({"sessionId": "s", "skillId": "id-deploy", "version": null, "alias": "latest", "scope": "project"});
    assert_eq!(*params, wanted);
}

#[tokio::test]
async fn an_installed_scope_writes_its_choice_at_once_and_returns_to_its_row() {
    let mut app = opened(vec![registry("deploy", "project", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    highlight_row(&mut app, Property::Scope(SkillScope::Project));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(picker_row(&app), Some(pick(Choice::Version(2))));
    let (method, params) = next(&mut frames).await;
    assert_eq!(
        (method.as_str(), &params["version"]),
        ("skills/detail", &json!(2)),
        "the preview"
    );
    highlight_pick(&mut app, &pick(alias("stable")));
    press(&mut app, &client, KeyCode::Enter);
    let import = json!({"sessionId": "s", "skillId": "id-deploy", "version": null, "alias": "stable", "scope": "project"});
    assert_eq!(
        next(&mut frames).await,
        ("skills/import".to_owned(), import)
    );
    assert_eq!(page_row(&app), Some(Property::Scope(SkillScope::Project)));
    assert_eq!(app.skills_browser.busy.as_deref(), Some("Tracking stable"));
    highlight_row(&mut app, Property::Enabled);
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(
        notice(&app),
        Some(labels::BUSY),
        "a second change waits for the first"
    );
}

#[tokio::test]
async fn an_unpinned_scope_imports_and_only_a_second_pin_can_be_unpinned() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    let last = page(&app).picker.as_ref().unwrap().rows.last().cloned();
    assert_eq!(
        last,
        Some(PickerRow::Unpin { inert: true }),
        "the only pin stays"
    );
    press(&mut app, &client, KeyCode::Esc);
    press(&mut app, &client, KeyCode::Down);
    press(&mut app, &client, KeyCode::Enter);
    let rows = &page(&app).picker.as_ref().unwrap().rows;
    assert!(!rows
        .iter()
        .any(|row| matches!(row, PickerRow::Unpin { .. })));
    highlight_pick(&mut app, &pick(Choice::Version(2)));
    press(&mut app, &client, KeyCode::Enter);
    let import = json!({"sessionId": "s", "skillId": "id-deploy", "version": 2, "alias": null, "scope": "project"});
    let seen = through(&mut frames, "skills/import").await;
    assert_eq!(seen.last(), Some(&("skills/import".to_owned(), import)));
}

#[tokio::test]
async fn unpin_removes_one_scope_and_remove_takes_every_scope() {
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    highlight_pick(&mut app, &PickerRow::Unpin { inert: false });
    press(&mut app, &client, KeyCode::Enter);
    let unpin = json!({"sessionId": "s", "name": "deploy", "scope": "global"});
    let seen = through(&mut frames, "skills/remove").await;
    assert_eq!(seen.last(), Some(&("skills/remove".to_owned(), unpin)));
    assert_eq!(next(&mut frames).await.0, "skills/installed");
    app.skills_browser.busy = None;
    highlight_row(&mut app, Property::Remove);
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Enter);
    let seen = through(&mut frames, "skills/installed").await;
    let scopes: Vec<&str> = seen
        .iter()
        .filter(|(method, _)| method == "skills/remove")
        .filter_map(|(_, params)| params["scope"].as_str())
        .collect();
    assert_eq!(scopes, ["global", "project"]);
}

#[tokio::test]
async fn a_picker_fetches_one_preview_at_a_time_the_highlighted_one_next() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(next(&mut frames).await.1["version"], json!(2));
    for key in ['j', 'k', 'k'] {
        press(&mut app, &client, KeyCode::Char(key));
    }
    let highlighted = picker::previewed(&app.skills_browser, page(&app));
    assert_eq!(highlighted.and_then(|key| key.1), Some(3));
    let early = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(
        early.is_err(),
        "nothing more is asked while v2 is on its way"
    );
    answer(&mut app, &client, "id-deploy", 2);
    assert_eq!(
        next(&mut frames).await.1["version"],
        json!(3),
        "the highlighted version, never the v1 passed over"
    );
    answer(&mut app, &client, "id-deploy", 3);
    press(&mut app, &client, KeyCode::Char('j'));
    let cached = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(cached.is_err(), "v2 is already here");
}

#[tokio::test]
async fn a_failed_preview_is_fetched_again_on_the_next_visit() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(next(&mut frames).await.1["version"], json!(2));
    let failed = Event::Body {
        generation: app.skills_browser.generation,
        key: ("id-deploy".to_owned(), Some(2)),
        body: Err("timed out".to_owned()),
    };
    skills_browser::apply_event(&mut app, &client, failed);
    press(&mut app, &client, KeyCode::Char('j'));
    assert_eq!(next(&mut frames).await.1["version"], json!(1));
    press(&mut app, &client, KeyCode::Char('k'));
    let early = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(early.is_err(), "v2 waits for v1");
    assert_eq!(
        shown_body(&app),
        Some(Body::Loading),
        "not the stale failure"
    );
    answer(&mut app, &client, "id-deploy", 1);
    assert_eq!(
        next(&mut frames).await.1["version"],
        json!(2),
        "tried again once v1 landed"
    );
}

#[test]
fn an_answer_that_lands_mid_change_keeps_the_change_spinner() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    app.skills_browser.busy = Some("Pinning v1".to_owned());
    app.view.command_loading = false;
    let preview = Event::Body {
        generation: app.skills_browser.generation,
        key: ("id-deploy".to_owned(), Some(1)),
        body: Ok("# v1".to_owned()),
    };
    skills_browser::apply_event(&mut app, &stub(), preview);
    assert!(app.view.command_loading, "the change is still in flight");
}

#[tokio::test]
async fn space_on_a_list_row_switches_the_skill() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], None);
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char(' '));
    let enabled = json!({"sessionId": "s", "name": "deploy", "enabled": false});
    assert_eq!(
        next(&mut frames).await,
        ("skills/setEnabled".to_owned(), enabled)
    );
    assert_eq!(
        next(&mut frames).await.0,
        "skills/installed",
        "every change re-reads the pins"
    );
}

#[test]
fn a_new_scope_starts_on_the_version_the_other_scope_pins() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    highlight_row(&mut app, Property::Scope(SkillScope::Project));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(picker_row(&app), Some(pick(Choice::Version(2))));
}
