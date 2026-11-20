use std::sync::Arc;

use crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};
use tokio::sync::mpsc;

use super::handle_input_event;
use crate::app::App;
use crate::server::Client;

fn send(app: &mut App, event: Event) {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel(1);
    handle_input_event(app, &client, &config_tx, Some(event));
}

#[test]
fn shift_tab_switches_the_skills_tabs_instead_of_cycling_agents() {
    let mut app = App::default();
    app.skills_browser.open = true;
    app.skills_browser.registry = true;
    let shift_tab = Event::Key(KeyEvent::new(KeyCode::BackTab, KeyModifiers::SHIFT));

    send(&mut app, shift_tab);

    assert_eq!(app.skills_browser.tab, crate::skills_browser::Tab::Shared);
    assert_eq!(app.agents.desired, None);
}
