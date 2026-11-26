//! `/skills` description above the list, and the body pane per page row: what each highlight shows, fetched once.

use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::SkillScope;
use vibe_rs::skills_browser::bodies::{self, Content};
use vibe_rs::skills_browser::model::{Body, Choice};
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::picker::PickerRow;
use vibe_rs::skills_browser::{self, Event};

use crate::skills_browser_support::{
    catalog_row, highlight_pick, highlight_row, installed_row, next, open, open_page, opened, pin,
    press, registry, session, shadowed, shared, shown_body,
};
use crate::support::{render, rows, stub};

#[test]
fn the_list_shows_the_highlighted_description_above_its_rows_and_no_body() {
    let mut lint = pin("lint", "local", "global");
    lint["description"] = json!("Check the style before every commit.");
    lint["prompt"] = json!("# lint body");
    let mut app = opened(vec![lint], None);
    assert_eq!(shown_body(&app), None);
    let screen = rows(&render(100, 40, |frame| app.draw(frame)));
    let at = |text: &str| screen.iter().position(|row| row.contains(text));
    let description = at("Check the style before every commit.").expect("drawn");
    let headings = at("Scope").expect("drawn");
    assert!(description < headings, "above the rows");
    assert_eq!(at("# lint body"), None, "the body stays on the page");
}

#[tokio::test]
async fn the_shared_tab_fetches_no_body() {
    let mut app = App::default();
    let (client, mut frames) = session(&mut app);
    open(&mut app, &client, Vec::new(), Some(shared()));
    assert_eq!(next(&mut frames).await.0, "skills/catalog");
    press(&mut app, &client, KeyCode::Tab);
    press(&mut app, &client, KeyCode::Char('j'));
    let fetched = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(
        fetched.is_err(),
        "the list reads descriptions from the catalog"
    );
}

#[test]
fn a_body_answer_for_an_earlier_opening_is_dropped() {
    let client = stub();
    let mut app = opened(Vec::new(), Some(shared()));
    let earlier = app.skills_browser.generation;
    press(&mut app, &client, KeyCode::Esc);
    open(&mut app, &client, Vec::new(), Some(shared()));
    let stale = Event::Body {
        generation: earlier,
        key: ("id-alpha".to_owned(), Some(1)),
        body: Err("skills/detail timed out".to_owned()),
    };
    skills_browser::apply_event(&mut app, &client, stale);
    assert!(
        app.skills_browser.bodies.is_empty(),
        "the stale answer is dropped"
    );
}

#[test]
fn a_pending_install_previews_what_it_would_load_the_project_choice_first() {
    let client = stub();
    let mut app = opened(Vec::new(), Some(shared()));
    open_page(&mut app, catalog_row("id-deploy"));
    highlight_row(&mut app, Property::Scope(SkillScope::Project));
    press(&mut app, &client, KeyCode::Enter);
    highlight_pick(&mut app, &PickerRow::Pick(Choice::Version(2)));
    press(&mut app, &client, KeyCode::Enter);
    let shown = |app: &App| bodies::shown(&app.skills_browser).map(|shown| shown.content);
    let version = |number| Some(Content::Version(("id-deploy".to_owned(), Some(number))));
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    assert_eq!(shown(&app), version(3), "Global tracks latest");
    highlight_row(&mut app, Property::Install);
    assert_eq!(
        shown(&app),
        version(2),
        "the project's choice wins, as it will once installed"
    );
}

#[test]
fn a_skill_without_a_description_says_so_and_the_rows_stay_put() {
    let client = stub();
    let mut lint = pin("lint", "local", "global");
    lint["description"] = json!("Check the style before every commit, then run the formatter on every file you changed and fix what it reports, and repeat.");
    let mut app = opened(vec![lint, pin("zed", "local", "global")], None);
    let heading_row = |app: &mut App| {
        let screen = rows(&render(60, 40, |frame| app.draw(frame)));
        let at = screen.iter().position(|row| row.contains("Scope"));
        (at.expect("drawn"), screen.join("\n"))
    };
    let (long, screen) = heading_row(&mut app);
    assert!(screen.contains('…'), "a long description is cut");
    press(&mut app, &client, KeyCode::Char('j'));
    let (none, screen) = heading_row(&mut app);
    assert!(screen.contains("No description"));
    assert_eq!(long, none, "the rows never move");
}

#[test]
fn each_scope_row_shows_its_own_pin_and_the_other_rows_the_page_s() {
    let client = stub();
    let mut project = registry("deploy", "project", 3, None);
    project["prompt"] = json!("# deploy v3");
    let mut global = shadowed(registry("deploy", "global", 2, None));
    global["prompt"] = json!("# deploy v2");
    let mut app = opened(vec![project, global], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let shown = shown_body;
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Char('x'));
    assert_eq!(shown(&app), Some(Body::Ready("# deploy v2".to_owned())));
    highlight_row(&mut app, Property::Scope(SkillScope::Project));
    assert_eq!(shown(&app), Some(Body::Ready("# deploy v3".to_owned())));
    let shared_row = catalog_row("id-deploy");
    assert!(
        !app.skills_browser.rows().contains(&shared_row),
        "a project pin hides it"
    );
}
