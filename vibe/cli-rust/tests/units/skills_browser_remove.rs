//! `/skills` removal: `d` twice on the list and Enter twice on a page, like `/resume`'s delete.

use crossterm::event::KeyCode;
use ratatui::layout::Rect;
use vibe_rs::hints::{action, key};
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::{self, help};

use crate::skills_browser_support::{
    catalog_row, highlight_row, installed_row, next, open_page, opened, page, pin, press, registry,
    search, session, shadowed, shared, through,
};
use crate::support::{down, render, rows, stub};

#[tokio::test]
async fn d_twice_removes_a_shared_skill_from_every_scope_and_another_key_disarms_it() {
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
        pin("lint", "local", "global"),
    ];
    let mut app = opened(both, None);
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char('d'));
    assert_eq!(app.skills_browser.removing.as_deref(), Some("deploy"));
    press(&mut app, &client, KeyCode::Char('j'));
    assert_eq!(app.skills_browser.removing, None, "a move disarms it");
    press(&mut app, &client, KeyCode::Char('d'));
    assert_eq!(
        app.skills_browser.removing, None,
        "a local skill is not removable here"
    );
    press(&mut app, &client, KeyCode::Char('k'));
    press(&mut app, &client, KeyCode::Char('d'));
    press(&mut app, &client, KeyCode::Esc);
    assert!(app.skills_browser.open, "Esc only disarms");
    press(&mut app, &client, KeyCode::Char('d'));
    press(&mut app, &client, KeyCode::Char('d'));
    let seen = through(&mut frames, "skills/installed").await;
    let removed: Vec<_> = seen
        .iter()
        .filter(|(method, _)| method == "skills/remove")
        .map(|(_, params)| params["scope"].as_str().unwrap_or_default().to_owned())
        .collect();
    assert_eq!(removed, ["global", "project"]);
}

#[tokio::test]
async fn enter_waits_while_a_removal_is_armed() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], None);
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char('d'));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(app.skills_browser.removing.as_deref(), Some("deploy"));
    press(&mut app, &client, KeyCode::Char(' '));
    assert_eq!(
        next(&mut frames).await.0,
        "skills/setEnabled",
        "Space disarms and acts"
    );
}

#[test]
fn d_on_a_local_row_leaves_the_registry_pins_it_shadows() {
    let client = stub();
    let local = pin("deploy", "local", "global");
    let mut app = opened(
        vec![local, shadowed(registry("deploy", "global", 2, None))],
        None,
    );
    press(&mut app, &client, KeyCode::Char('d'));
    assert_eq!(app.skills_browser.removing, None, "the row is a local file");
    let footer = help(&app.skills_browser);
    assert!(!footer.iter().any(|(key, _)| *key == "d"));
}

#[test]
fn esc_disarms_a_removal_and_keeps_the_filter() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], None);
    press(&mut app, &client, KeyCode::Char('/'));
    search(&mut app, &client, "dep");
    press(&mut app, &client, KeyCode::Esc);
    press(&mut app, &client, KeyCode::Char('d'));
    assert_eq!(app.skills_browser.removing.as_deref(), Some("deploy"));
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(app.skills_browser.removing, None);
    assert_eq!(app.skills_browser.search.query, "dep");
}

#[test]
fn a_press_on_the_search_row_disarms_a_removal() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], None);
    press(&mut app, &client, KeyCode::Char('d'));
    app.skills_browser.search.area = Rect::new(3, 2, 40, 1);
    let event = down(5, 2);
    skills_browser::mouse(&mut app, &client, event);
    assert!(app.skills_browser.search.focused);
    assert_eq!(app.skills_browser.removing, None);
}

#[tokio::test]
async fn remove_on_a_page_asks_for_enter_again_and_esc_only_disarms() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    highlight_row(&mut app, Property::Remove);
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(app.skills_browser.removing.as_deref(), Some("deploy"));
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(app.skills_browser.removing, None);
    assert!(page(&app).picker.is_none(), "still on the page");
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Enter);
    let methods: Vec<String> = through(&mut frames, "skills/installed")
        .await
        .into_iter()
        .map(|(method, _)| method)
        .collect();
    assert_eq!(methods, ["skills/remove", "skills/installed"]);
}

#[test]
fn a_pin_shadowed_by_a_local_file_is_removed_from_its_shared_page() {
    let local = pin("deploy", "local", "global");
    let mut no_project = shared();
    no_project.project_available = false;
    let pinned = shadowed(registry("deploy", "global", 2, None));
    let mut app = opened(vec![local, pinned], Some(no_project));
    let entry = catalog_row("id-deploy");
    crate::skills_browser_support::highlight(&mut app, &entry);
    assert!(app.skills_browser.rows().contains(&entry));
    let view = (key::ENTER, action::VIEW);
    assert!(
        help(&app.skills_browser).contains(&view),
        "its page is the installed one"
    );
    open_page(&mut app, entry);
    assert!(page(&app).rows.contains(&Property::Remove));
}

#[test]
fn a_narrow_row_keeps_the_whole_removal_prompt() {
    let long = registry("summarize-pull-request-review", "global", 2, None);
    let mut app = opened(vec![long], None);
    press(&mut app, &stub(), KeyCode::Char('d'));
    for width in [60, 34] {
        let screen = rows(&render(width, 30, |frame| app.draw(frame)));
        let prompt = screen
            .iter()
            .any(|row| row.contains("Press d again to remove"));
        assert!(prompt, "{width} columns");
    }
}
