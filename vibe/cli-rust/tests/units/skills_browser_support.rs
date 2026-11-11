//! Builders shared by the `/skills` tests: pins, catalogs, an open browser.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use tokio::sync::mpsc::UnboundedReceiver;
use vibe_rs::app::App;
use vibe_rs::server::{Client, InstalledSkill, InstalledSkills, SkillCatalog, SkillVersion};
use vibe_rs::skills_browser::bodies;
use vibe_rs::skills_browser::list::Row;
use vibe_rs::skills_browser::model::Body;
use vibe_rs::skills_browser::page::{Page, Property};
use vibe_rs::skills_browser::picker::PickerRow;
use vibe_rs::skills_browser::{self, Event, Opening, Tab};

use crate::support::{ctrl, key, render, rows, stub};

pub fn pin(name: &str, source: &str, scope: &str) -> Value {
    json!({"name": name, "prompt": format!("# {name}"), "source": source, "scope": scope})
}

pub fn registry(name: &str, scope: &str, version: i64, alias: Option<&str>) -> Value {
    let mut value = pin(name, "registry", scope);
    let reference = json!({"skillId": format!("id-{name}"), "version": version, "alias": alias});
    value["registry"] = reference;
    value
}

/// A pin another skill of the same name wins over.
pub fn shadowed(mut value: Value) -> Value {
    value["active"] = json!(false);
    value
}

pub fn pins(values: Vec<Value>) -> Vec<InstalledSkill> {
    let answer = json!({ "skills": values });
    serde_json::from_value::<InstalledSkills>(answer)
        .unwrap()
        .skills
}

pub fn catalog(value: Value) -> SkillCatalog {
    serde_json::from_value(value).unwrap()
}

/// Three shared skills, a newer version of `deploy`, and a project.
pub fn shared() -> SkillCatalog {
    catalog(json!({
        "skills": [
            {"name": "zeta", "skillId": "id-zeta", "description": "Last", "latestVersion": 2, "sharingScope": "workspace"},
            {"name": "deploy", "skillId": "id-deploy", "description": "Ship it", "latestVersion": 4},
            {"name": "Alpha", "skillId": "id-alpha", "description": "Lint helper", "latestVersion": 1, "sharingScope": "private"},
        ],
        "updates": {"deploy": 4},
        "loaded": true,
        "projectAvailable": true,
    }))
}

pub fn opened(installed: Vec<Value>, catalog: Option<SkillCatalog>) -> App {
    let mut app = App::default();
    open(&mut app, &stub(), installed, catalog);
    app
}

/// Turn registry skills on in the session's runtime, as a config with the flag reports it.
pub fn registry_on(app: &mut App) {
    app.session.runtime =
        json!({"runtime": {"config": {"experimentalEnableRegistrySkills": true}}});
}

/// Open the browser on `installed` as `/skills` does; with a `catalog`, registry skills are on and its read answers it.
pub fn open(
    app: &mut App,
    client: &Arc<Client>,
    installed: Vec<Value>,
    catalog: Option<SkillCatalog>,
) {
    if catalog.is_some() {
        registry_on(app);
    }
    skills_browser::apply_event(app, client, Event::Opened(Ok(pins(installed))));
    if let Some(catalog) = catalog {
        let generation = app.skills_browser.generation;
        let answer = Event::Catalog {
            generation,
            catalog,
        };
        skills_browser::apply_event(app, client, answer);
    }
}

pub fn press(app: &mut App, client: &Arc<Client>, code: KeyCode) {
    skills_browser::handle_key(app, client, key(code));
}

/// Replace the focused search field's query, one key at a time.
pub fn search(app: &mut App, client: &Arc<Client>, query: &str) {
    let clear = ctrl('u');
    skills_browser::handle_key(app, client, clear);
    for ch in query.chars() {
        press(app, client, KeyCode::Char(ch));
    }
}

/// The list row of the installed skill `name`.
pub fn installed_row(name: &str) -> Row {
    Row::Installed(name.to_owned())
}

/// The list row of the shared skill `skill_id`.
pub fn catalog_row(skill_id: &str) -> Row {
    Row::Catalog(skill_id.to_owned())
}

/// The list row under the highlight.
pub fn highlighted(app: &App) -> Option<Row> {
    let state = &app.skills_browser;
    state.rows().get(state.list.selected()).cloned()
}

/// Highlight the first list row equal to `row`.
pub fn highlight(app: &mut App, row: &Row) {
    let tab = match row {
        Row::Catalog(_) => Tab::Shared,
        _ => Tab::Mine,
    };
    if app.skills_browser.tab != tab {
        press(app, &stub(), KeyCode::Tab);
    }
    let state = &mut app.skills_browser;
    let index = state
        .rows()
        .iter()
        .position(|candidate| candidate == row)
        .unwrap();
    state.list.select(index);
}

pub fn versions() -> Vec<SkillVersion> {
    serde_json::from_value(
        json!([{"version": 3}, {"version": 2, "aliases": ["stable"]}, {"version": 1}]),
    )
    .unwrap()
}

/// Open the page of the list `row` as if it had asked for its versions and they had answered.
pub fn open_page(app: &mut App, row: Row) {
    open_page_with(app, row, versions());
}

/// Open the page of the list `row` as if its versions had answered `versions`.
pub fn open_page_with(app: &mut App, row: Row, versions: Vec<SkillVersion>) {
    highlight(app, &row);
    let label = String::new();
    app.skills_browser.opening = Some(Opening::Page {
        row: row.clone(),
        label,
    });
    let event = Event::Page {
        row,
        versions: Ok(versions),
    };
    skills_browser::apply_event(app, &stub(), event);
}

pub fn page(app: &App) -> &Page {
    app.skills_browser.page.as_ref().unwrap()
}

/// The page row under the highlight.
pub fn page_row(app: &App) -> Option<Property> {
    let page = page(app);
    page.rows.get(page.list.selected()).copied()
}

/// Highlight the page row `property`.
pub fn highlight_row(app: &mut App, property: Property) {
    let page = app.skills_browser.page.as_mut().unwrap();
    let index = page.rows.iter().position(|row| *row == property).unwrap();
    page.list.select(index);
}

/// The picker row under the highlight.
pub fn picker_row(app: &App) -> Option<PickerRow> {
    let picker = page(app).picker.as_ref()?;
    picker.rows.get(picker.list.selected()).cloned()
}

/// Highlight the picker row `row`.
pub fn highlight_pick(app: &mut App, row: &PickerRow) {
    let page = app.skills_browser.page.as_mut().unwrap();
    let picker = page.picker.as_mut().unwrap();
    let index = picker
        .rows
        .iter()
        .position(|candidate| candidate == row)
        .unwrap();
    picker.list.select(index);
}

pub async fn sent(frames: &mut UnboundedReceiver<Value>) -> Value {
    let wait = tokio::time::timeout(Duration::from_secs(5), frames.recv());
    wait.await
        .expect("a request is sent")
        .expect("the stub answers")
}

/// The method and params of the next frame sent.
pub async fn next(frames: &mut UnboundedReceiver<Value>) -> (String, Value) {
    let frame = sent(frames).await;
    let method = frame["method"].as_str().unwrap_or_default().to_owned();
    (method, frame["params"].clone())
}

/// The frames sent up to and including the next call of `method`.
pub async fn through(frames: &mut UnboundedReceiver<Value>, method: &str) -> Vec<(String, Value)> {
    let mut seen = Vec::new();
    loop {
        let frame = next(frames).await;
        let done = frame.0 == method;
        seen.push(frame);
        if done {
            return seen;
        }
    }
}

pub fn session(app: &mut App) -> (Arc<Client>, UnboundedReceiver<Value>) {
    app.session.session_id = Some("s".to_owned());
    let (client, frames) = Client::stub_answering();
    (Arc::new(client), frames)
}

/// The inline notice above the input, if one is up.
pub fn notice(app: &App) -> Option<&str> {
    app.overlays
        .notice
        .as_ref()
        .map(|notice| notice.text.as_str())
}

/// The body the pane shows, `None` when the highlight shows none.
pub fn shown_body(app: &App) -> Option<Body> {
    bodies::view(&app.skills_browser).map(|(_, body)| body)
}

/// The answer to a preview fetch of `version` of `skill_id`.
pub fn answer(app: &mut App, client: &Arc<Client>, skill_id: &str, version: i64) {
    let event = Event::Body {
        generation: app.skills_browser.generation,
        key: (skill_id.to_owned(), Some(version)),
        body: Ok(format!("# {skill_id} v{version}")),
    };
    skills_browser::apply_event(app, client, event);
}

/// The cell where `text` first shows on a 100x40 screen.
pub fn cell_of(app: &mut App, text: &str) -> (u16, u16) {
    let screen = rows(&render(100, 40, |frame| app.draw(frame)));
    for (y, line) in screen.iter().enumerate() {
        if let Some(byte) = line.find(text) {
            let x = line[..byte].chars().count() + 1;
            return (x as u16, y as u16);
        }
    }
    panic!("{text} is not on screen");
}
