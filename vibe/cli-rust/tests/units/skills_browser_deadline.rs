//! A `/skills` read that never answers fails once the shared request deadline is up, so the next fetch goes ahead.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use tokio::sync::mpsc;
use tokio::time::Instant;
use vibe_rs::commands::CommandEvent;
use vibe_rs::server::Client;
use vibe_rs::skills_browser;
use vibe_rs::skills_browser::model::Body;

use vibe_rs::skills_browser::{Event, Opening};

use crate::skills_browser_support::{
    catalog_row, highlight, next, opened, press, shared, shown_body, versions,
};

#[tokio::test(start_paused = true)]
async fn a_body_that_never_answers_fails_after_thirty_seconds_and_the_next_is_fetched() {
    let mut app = opened(Vec::new(), Some(shared()));
    app.session.session_id = Some("s".to_owned());
    let (events, mut delivered) = mpsc::channel(8);
    app.command_tx = Some(events);
    let (client, mut frames) = Client::stub_silent();
    let client = Arc::new(client);
    highlight(&mut app, &catalog_row("id-deploy"));
    app.skills_browser.opening = Some(Opening::Page {
        row: catalog_row("id-deploy"),
        label: String::new(),
    });
    let start = Instant::now();
    let page = Event::Page {
        row: catalog_row("id-deploy"),
        versions: Ok(versions()),
    };
    skills_browser::apply_event(&mut app, &client, page);
    let first = next(&mut frames).await;
    assert_eq!(first.0, "skills/detail");
    let late = tokio::time::timeout(Duration::from_secs(60), delivered.recv());
    let Some(CommandEvent::Skills(event)) = late.await.expect("the read gives up on its own")
    else {
        panic!("a /skills answer is delivered");
    };
    let waited = start.elapsed();
    assert!(
        waited >= Duration::from_secs(30) && waited < Duration::from_secs(31),
        "{waited:?}"
    );
    skills_browser::apply_event(&mut app, &client, *event);
    let failed = Body::Failed("skills/detail timed out".to_owned());
    assert_eq!(shown_body(&app), Some(failed));
    press(&mut app, &client, KeyCode::Enter);
    let second = next(&mut frames).await;
    assert_eq!(second.0, "skills/detail", "the next screen asks again");
}
