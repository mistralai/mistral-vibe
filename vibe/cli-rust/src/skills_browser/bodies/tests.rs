use std::rc::Rc;
use std::sync::Arc;

use super::sync;
use crate::app::App;
use crate::server::{Client, InstalledSkill};
use crate::skills_browser::{list, State};

fn deploy() -> InstalledSkill {
    InstalledSkill {
        name: "deploy".to_owned(),
        prompt: "# deploy\n\nShip it.".to_owned(),
        source: "local".to_owned(),
        scope: "global".to_owned(),
        enabled: true,
        active: true,
        ..InstalledSkill::default()
    }
}

#[test]
fn the_body_is_wrapped_once_per_change_and_width() {
    let browser = State {
        open: true,
        installed: vec![deploy()],
        ..State::default()
    };
    let mut app = App {
        skills_browser: browser,
        ..App::default()
    };
    list::reset(&mut app.skills_browser);
    let state = &mut app.skills_browser;
    let first = state.body_lines(40);
    assert!(
        Rc::ptr_eq(&first, &state.body_lines(40)),
        "a frame with no change keeps the wrap"
    );
    assert!(
        !Rc::ptr_eq(&first, &state.body_lines(30)),
        "another width wraps again"
    );
    let wrapped = state.body_lines(40);
    sync(&mut app, &Arc::new(Client::stub()));
    let again = app.skills_browser.body_lines(40);
    assert!(!Rc::ptr_eq(&wrapped, &again), "any change wraps again");
}
