//! `/skills` pages: scope rows, body, switch, and following a change.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::hints::{self, action, key};
use vibe_rs::server::SkillScope;
use vibe_rs::skills_browser::labels::{self, scope_tail, scope_value};
use vibe_rs::skills_browser::list::Row;
use vibe_rs::skills_browser::model::Body;
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::picker::PickerRow;
use vibe_rs::skills_browser::{self, help, Event, Outcome};

use crate::skills_browser_support::{
    catalog_row, highlight_pick, highlight_row, highlighted, installed_row, notice, open_page,
    opened, page, page_row, pin, pins, press, registry, session, shadowed, shared, shown_body,
};
use crate::support::stub;

/// Each scope row's value and dim tail, global first.
fn scopes(app: &App) -> Vec<(String, Option<&'static str>)> {
    let state = &app.skills_browser;
    let page = page(app);
    SkillScope::ALL
        .map(|scope| {
            (
                scope_value(state, page, scope),
                scope_tail(state, page, scope),
            )
        })
        .to_vec()
}

fn changed(installed: Vec<serde_json::Value>, error: Option<&str>) -> Event {
    Event::Changed {
        outcome: Outcome {
            answer: Some(json!({})),
            error: error.map(str::to_owned),
        },
        installed: Ok(pins(installed)),
    }
}

#[test]
fn a_local_skill_page_shows_its_switch_and_body_and_esc_returns_to_its_row() {
    let client = stub();
    let mut app = opened(
        vec![
            pin("other", "local", "global"),
            pin("lint", "local", "global"),
        ],
        None,
    );
    press(&mut app, &client, KeyCode::Down);
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(page(&app).rows, [Property::Enabled]);
    let body = shown_body(&app);
    assert_eq!(body, Some(Body::Ready("# lint".to_owned())));
    press(&mut app, &client, KeyCode::Esc);
    assert!(app.skills_browser.page.is_none());
    assert_eq!(
        highlighted(&app),
        Some(installed_row("lint")),
        "back on the skill it came from"
    );
}

#[test]
fn a_skill_pinned_in_both_scopes_is_one_row_whose_page_shows_each_pin() {
    let installed = vec![
        registry("deploy", "project", 3, Some("latest")),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(installed, Some(shared()));
    let listed = app.skills_browser.rows();
    let deploys = listed.iter().filter(|row| **row == installed_row("deploy"));
    assert_eq!(deploys.count(), 1);
    open_page(&mut app, installed_row("deploy"));
    let expected = [
        Property::Scope(SkillScope::Global),
        Property::Scope(SkillScope::Project),
        Property::Enabled,
        Property::Remove,
    ];
    assert_eq!(page(&app).rows, expected);
    let global = ("v2  pinned".to_owned(), Some(labels::SHADOWED));
    let project = ("latest → v3  tracking".to_owned(), None);
    assert_eq!(scopes(&app), [global, project]);
    let body = shown_body(&app);
    assert_eq!(
        body,
        Some(Body::Ready("# deploy".to_owned())),
        "the project pin's"
    );
}

#[tokio::test]
async fn a_shared_skill_page_installs_latest_globally_and_shows_its_fetched_body() {
    let mut app = opened(Vec::new(), Some(shared()));
    session(&mut app);
    open_page(&mut app, catalog_row("id-deploy"));
    let rows = [
        Property::Scope(SkillScope::Global),
        Property::Scope(SkillScope::Project),
        Property::Install,
    ];
    assert_eq!(page(&app).rows, rows);
    assert_eq!(page(&app).description, "Ship it");
    let global = ("latest → v3  tracking".to_owned(), None);
    let project = (
        labels::NOT_PINNED.to_owned(),
        Some(labels::blurb(SkillScope::Project)),
    );
    assert_eq!(scopes(&app), [global, project]);
    assert_eq!(shown_body(&app), Some(Body::Loading));
    let key = ("id-deploy".to_owned(), Some(3));
    let event = Event::Body {
        generation: app.skills_browser.generation,
        key,
        body: Ok("# deploy body".to_owned()),
    };
    skills_browser::apply_event(&mut app, &stub(), event);
    let body = shown_body(&app);
    assert_eq!(body, Some(Body::Ready("# deploy body".to_owned())));
}

#[test]
fn a_globally_pinned_skill_opened_from_shared_is_its_installed_page() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, catalog_row("id-deploy"));
    assert!(page(&app).subject.installed());
    assert_eq!(scopes(&app)[1].0, labels::NOT_PINNED);
    assert!(page(&app).rows.contains(&Property::Remove));
}

#[test]
fn without_a_project_a_page_lists_only_the_global_scope() {
    let mut catalog = shared();
    catalog.project_available = false;
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(catalog));
    open_page(&mut app, installed_row("deploy"));
    let rows = &page(&app).rows;
    assert_eq!(
        rows[..2],
        [Property::Scope(SkillScope::Global), Property::Enabled]
    );
}

#[test]
fn a_locked_skill_says_where_it_is_managed_instead_of_toggling() {
    let client = stub();
    let mut plugin = pin("pdf", "plugin", "global");
    plugin["locked"] = json!(true);
    let mut patterned = pin("legacy", "local", "global");
    patterned["locked"] = json!(true);
    let mut app = opened(vec![plugin, patterned], None);
    press(&mut app, &client, KeyCode::Char(' '));
    assert_eq!(notice(&app), Some(labels::PLUGIN_LOCK));
    press(&mut app, &client, KeyCode::Down);
    press(&mut app, &client, KeyCode::Char(' '));
    let config =
        "'legacy' is set by a config pattern; edit enabled_skills or disabled_skills to change it.";
    assert_eq!(notice(&app), Some(config));
    assert!(app.skills_browser.busy.is_none());
}

#[test]
fn a_change_keeps_the_page_while_a_pin_is_left_and_leaves_it_after_the_last() {
    let client = stub();
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let remaining = vec![registry("deploy", "global", 2, None)];
    skills_browser::apply_event(&mut app, &client, changed(remaining, None));
    assert!(app.skills_browser.page.is_some(), "the global pin is left");
    assert_eq!(scopes(&app)[1].0, labels::NOT_PINNED);
    app.skills_browser.busy = Some("Removing deploy".to_owned());
    let failed = changed(Vec::new(), Some("A turn is already running"));
    skills_browser::apply_event(&mut app, &client, failed);
    assert!(
        app.skills_browser.page.is_none(),
        "the re-read after a failure still lands"
    );
    assert_eq!(
        notice(&app),
        Some("Removing deploy failed: A turn is already running")
    );
    let none = Row::Note(labels::NO_SKILLS);
    assert!(app.skills_browser.rows().contains(&none));
}

#[test]
fn an_install_turns_the_page_into_the_installed_one() {
    let client = stub();
    let mut app = opened(Vec::new(), Some(shared()));
    open_page(&mut app, catalog_row("id-deploy"));
    let installed = vec![registry("deploy", "global", 3, Some("latest"))];
    skills_browser::apply_event(&mut app, &client, changed(installed, None));
    let page = page(&app);
    assert!(page.subject.installed());
    assert!(page.rows.contains(&Property::Remove));
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(
        highlighted(&app),
        Some(installed_row("deploy")),
        "back on its installed row"
    );
}

#[test]
fn a_change_that_fails_midway_still_applies_what_landed_and_says_what_failed() {
    let client = stub();
    let mut app = opened(Vec::new(), Some(shared()));
    let runtime = json!({"runtime": {"skills": [{"name": "deploy", "prompt": "# deploy"}]}});
    app.skills_browser.busy = Some("Adding deploy".to_owned());
    let event = Event::Changed {
        outcome: Outcome {
            answer: Some(runtime.clone()),
            error: Some("network down".to_owned()),
        },
        installed: Ok(Vec::new()),
    };
    skills_browser::apply_event(&mut app, &client, event);
    assert_eq!(app.session.runtime, runtime);
    assert_eq!(notice(&app), Some("Adding deploy failed: network down"));
}

#[test]
fn esc_steps_back_from_a_picker_to_its_scope_then_to_the_list() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    press(&mut app, &client, KeyCode::Char('j'));
    press(&mut app, &client, KeyCode::Enter);
    let open = page(&app).picker.as_ref().map(|picker| picker.scope);
    assert_eq!(open, Some(SkillScope::Project));
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(page_row(&app), Some(Property::Scope(SkillScope::Project)));
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(highlighted(&app), Some(installed_row("deploy")));
    assert!(app.skills_browser.open);
}

#[test]
fn the_page_footer_names_what_enter_does_to_the_highlighted_row() {
    let client = stub();
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let footer = |verb| [hints::NAVIGATE, verb, hints::BACK];
    assert_eq!(
        help(&app.skills_browser),
        footer((key::ENTER, action::CHANGE))
    );
    highlight_row(&mut app, Property::Enabled);
    let switch = (key::SPACE_ENTER, action::DISABLE);
    assert_eq!(help(&app.skills_browser), footer(switch));
    highlight_row(&mut app, Property::Remove);
    assert_eq!(
        help(&app.skills_browser),
        footer((key::ENTER, action::REMOVE))
    );
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(help(&app.skills_browser), footer(hints::SELECT));
    highlight_pick(&mut app, &PickerRow::Unpin { inert: false });
    assert_eq!(
        help(&app.skills_browser),
        footer((key::ENTER, action::UNPIN))
    );
    let mut unpinned = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut unpinned, installed_row("deploy"));
    highlight_row(&mut unpinned, Property::Scope(SkillScope::Project));
    assert_eq!(
        help(&unpinned.skills_browser),
        footer((key::ENTER, action::PIN))
    );
}
