//! `/skills` body pane on the list and per page row: what each highlight shows, fetched once.

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
    answer, catalog_row, highlight_pick, highlight_row, installed_row, next, open, open_page,
    opened, pin, press, registry, search, session, shadowed, shared, shown_body,
};
use crate::support::{render, rows, stub};

#[test]
fn an_installed_row_previews_the_body_its_name_resolves_to() {
    let mut lint = pin("lint", "local", "global");
    lint["prompt"] = json!("# lint\n\nCheck the style.");
    let app = opened(vec![lint], None);
    let shown = bodies::shown(&app.skills_browser).map(|shown| shown.content);
    let lint_pin = Content::Pin {
        name: "lint".to_owned(),
        scope: "global".to_owned(),
        source: "local".to_owned(),
    };
    assert_eq!(shown, Some(lint_pin));
    let body = shown_body(&app);
    assert_eq!(
        body,
        Some(Body::Ready("# lint\n\nCheck the style.".to_owned()))
    );
}

#[tokio::test]
async fn shared_rows_fetch_one_newest_body_at_a_time_the_highlighted_one_next() {
    let mut app = App::default();
    let (client, mut frames) = session(&mut app);
    open(&mut app, &client, Vec::new(), Some(shared()));
    assert_eq!(next(&mut frames).await.0, "skills/catalog");
    press(&mut app, &client, KeyCode::Tab);
    let (method, params) = next(&mut frames).await;
    assert_eq!(
        (method.as_str(), params["skillId"].clone()),
        ("skills/detail", json!("id-alpha"))
    );
    press(&mut app, &client, KeyCode::Char('j'));
    press(&mut app, &client, KeyCode::Char('j'));
    let early = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(early.is_err(), "nothing more while Alpha is on its way");
    assert_eq!(shown_body(&app), Some(Body::Loading));
    answer(&mut app, &client, "id-alpha", 1);
    let (_, params) = next(&mut frames).await;
    let zeta = (params["skillId"].clone(), params["version"].clone());
    assert_eq!(
        zeta,
        (json!("id-zeta"), json!(2)),
        "the highlighted row, deploy passed over"
    );
    press(&mut app, &client, KeyCode::Char('k'));
    answer(&mut app, &client, "id-zeta", 2);
    let (_, params) = next(&mut frames).await;
    let deploy = (params["skillId"].clone(), params["version"].clone());
    assert_eq!(deploy, (json!("id-deploy"), json!(4)), "the newest version");
    answer(&mut app, &client, "id-deploy", 4);
    press(&mut app, &client, KeyCode::Char('k'));
    press(&mut app, &client, KeyCode::Char('j'));
    let again = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(again.is_err(), "each body is fetched once");
    let shown = bodies::shown(&app.skills_browser).unwrap();
    let deploy = Content::Version(("id-deploy".to_owned(), Some(4)));
    assert_eq!(
        (shown.content, shown.intro.as_deref()),
        (deploy, Some("Ship it"))
    );
}

#[tokio::test]
async fn a_body_answer_for_an_earlier_opening_is_dropped() {
    let mut app = App::default();
    let (client, mut frames) = session(&mut app);
    let reopen = |app: &mut App| {
        open(app, &client, Vec::new(), Some(shared()));
        press(app, &client, KeyCode::Tab);
    };
    reopen(&mut app);
    assert_eq!(next(&mut frames).await.0, "skills/catalog");
    assert_eq!(next(&mut frames).await.1["skillId"], "id-alpha");
    let earlier = app.skills_browser.generation;
    press(&mut app, &client, KeyCode::Esc);
    assert!(!app.skills_browser.open);
    reopen(&mut app);
    assert_eq!(next(&mut frames).await.0, "skills/catalog");
    assert_eq!(
        next(&mut frames).await.1["skillId"],
        "id-alpha",
        "the new opening asks again"
    );
    let stale = Event::Body {
        generation: earlier,
        key: ("id-alpha".to_owned(), Some(1)),
        body: Err("skills/detail timed out".to_owned()),
    };
    skills_browser::apply_event(&mut app, &client, stale);
    assert_eq!(
        shown_body(&app),
        Some(Body::Loading),
        "the stale failure is dropped"
    );
    answer(&mut app, &client, "id-alpha", 1);
    let body = Body::Ready("# id-alpha v1".to_owned());
    assert_eq!(shown_body(&app), Some(body));
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
fn no_row_to_show_means_no_pane() {
    let client = stub();
    let mut app = opened(vec![pin("lint", "local", "global")], None);
    press(&mut app, &client, KeyCode::Char('/'));
    search(&mut app, &client, "zzz");
    assert_eq!(shown_body(&app), None);
}

#[test]
fn a_box_from_140_columns_previews_beside_the_list_and_under_it_below() {
    let mut lint = pin("lint", "local", "global");
    lint["prompt"] = json!("# lint\n\nCheck the style.");
    let mut app = opened(vec![lint], None);
    let mut headings = |width: u16| {
        let screen = rows(&render(width, 40, |frame| app.draw(frame)));
        let found = screen
            .into_iter()
            .find(|row| row.contains("Skill") && row.contains("Scope"));
        found.expect("the column names are drawn")
    };
    assert!(headings(140).contains("# lint"), "beside the list");
    assert!(!headings(139).contains("# lint"), "under the rows");
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
    highlight_row(&mut app, Property::Enabled);
    assert_eq!(
        shown(&app),
        Some(Body::Ready("# deploy v3".to_owned())),
        "the nearest pin"
    );
    let shared_row = catalog_row("id-deploy");
    assert!(
        !app.skills_browser.rows().contains(&shared_row),
        "a project pin hides it"
    );
}
