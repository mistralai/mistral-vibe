//! `/skills` page keys and rows: Space only on Enabled, pending installs without a project, locked pins.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::server::SkillScope;
use vibe_rs::skills_browser::page::Property;

use crate::skills_browser_support::{
    catalog_row, highlight_row, installed_row, open_page, opened, page, page_row, press, registry,
    shared,
};
use crate::support::stub;

#[test]
fn space_slash_and_letters_do_nothing_off_the_enabled_row() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    for code in [KeyCode::Char(' '), KeyCode::Char('/'), KeyCode::Char('x')] {
        press(&mut app, &client, code);
    }
    assert!(page(&app).picker.is_none());
    assert_eq!(page_row(&app), Some(Property::Scope(SkillScope::Global)));
    assert!(app.skills_browser.busy.is_none(), "nothing was sent");
}

#[test]
fn a_pending_install_without_a_project_lists_global_and_install() {
    let mut no_project = shared();
    no_project.project_available = false;
    let mut app = opened(Vec::new(), Some(no_project));
    open_page(&mut app, catalog_row("id-deploy"));
    let rows = [Property::Scope(SkillScope::Global), Property::Install];
    assert_eq!(page(&app).rows, rows);
}

#[test]
fn a_locked_registry_pin_has_no_enabled_row() {
    let mut locked = registry("deploy", "global", 2, None);
    locked["locked"] = json!(true);
    let mut app = opened(vec![locked], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    assert!(!page(&app).rows.contains(&Property::Enabled));
    assert!(page(&app).rows.contains(&Property::Remove));
}
