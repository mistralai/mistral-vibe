//! Shift+Tab agent switches: submit guard, delayed prompt spinner, failure recovery and chasing.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crossterm::event::KeyCode;
use ratatui::layout::Rect;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::agents::{self, Event};
use vibe_rs::app::{App, Status};
use vibe_rs::config;
use vibe_rs::input::handle_key;
use vibe_rs::server::Client;
use vibe_rs::ui_event::{self, UiEvent};

use crate::support::{key, render, row};

const PRIMARY: [&str; 3] = ["default", "plan", "accept-edits"];

fn runtime(active: &str) -> Value {
    let mut agents: Vec<Value> = PRIMARY
        .iter()
        .map(|name| json!({"name": name, "displayName": name, "agentType": "agent"}))
        .collect();
    agents.push(json!({"name": "explore", "displayName": "explore", "agentType": "subagent"}));
    json!({"runtime": {"activeAgent": {"name": active, "displayName": active}, "agents": agents}})
}

struct Harness {
    app: App,
    client: Arc<Client>,
    frames: Option<mpsc::UnboundedReceiver<Value>>,
    events: mpsc::Receiver<UiEvent>,
    config_tx: mpsc::Sender<config::Loaded>,
    _config_rx: mpsc::Receiver<config::Loaded>,
}

impl Harness {
    /// A ready session on `default`; `answering` picks a server that accepts switches.
    fn new(answering: bool) -> Self {
        let mut app = App::default();
        agents::apply_runtime(&mut app, &runtime("default"));
        app.session.session_id = Some("session".to_owned());
        app.set_status(Status::Ready);
        let (tx, events) = mpsc::channel(8);
        app.events = Some(tx);
        let (client, frames) = if answering {
            let (client, frames) = Client::stub_answering();
            (client, Some(frames))
        } else {
            (Client::stub(), None)
        };
        let (config_tx, _config_rx) = mpsc::channel(1);
        Self {
            app,
            client: Arc::new(client),
            frames,
            events,
            config_tx,
            _config_rx,
        }
    }

    fn press(&mut self, code: KeyCode) {
        handle_key(&mut self.app, &self.client, &self.config_tx, key(code));
    }

    fn type_text(&mut self, text: &str) {
        for character in text.chars() {
            self.press(KeyCode::Char(character));
        }
    }

    /// The agent the next `session/agent/update` frame on the wire asks for.
    async fn requested_agent(&mut self) -> String {
        let frames = self.frames.as_mut().expect("an answering server");
        let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
            .await
            .expect("the switch is sent")
            .expect("the stub answers");
        assert_eq!(frame["method"], "session/agent/update");
        assert_eq!(frame["params"]["sessionId"], "session");
        frame["params"]["agentName"].as_str().unwrap().to_owned()
    }

    /// The oldest switch answer in flight.
    async fn next_event(&mut self) -> Event {
        let event = tokio::time::timeout(Duration::from_secs(5), self.events.recv()).await;
        let Ok(Some(UiEvent::Agents(event))) = event else {
            panic!("the switch settles");
        };
        event
    }

    /// Settle the oldest switch in flight; a successful one lands on its target.
    async fn settle(&mut self) -> String {
        let mut event = self.next_event().await;
        if event.runtime.is_ok() {
            event.runtime = Ok(runtime(&event.target));
        }
        let target = event.target.clone();
        ui_event::apply(&mut self.app, &self.client, event.into());
        target
    }

    fn in_flight(&self) -> u32 {
        self.app.pending_commits.load(Ordering::Relaxed)
    }

    /// The composer's first content row, which starts with the prompt marker.
    fn prompt_row(&self) -> String {
        let width = 40;
        let buffer = render(width, 4, |frame| {
            vibe_rs::ui::chat_input::draw(&self.app, frame, Rect::new(0, 0, width, 4));
        });
        row(&buffer, 1)
    }
}

#[tokio::test]
async fn enter_is_swallowed_while_a_switch_is_in_flight_and_submits_once_it_settles() {
    let mut harness = Harness::new(true);
    harness.type_text("hello while switching");
    harness.press(KeyCode::BackTab);
    assert_eq!(harness.requested_agent().await, "plan");
    assert!(agents::switching(&harness.app));
    // The guard holds from the first frame, before the delayed spinner appears.
    assert!(agents::spinner_glyph(&harness.app).is_none());
    assert!(harness.prompt_row().starts_with("> hello while switching"));

    harness.press(KeyCode::Enter);
    assert_eq!(harness.app.chat_input.input, "hello while switching");
    assert!(harness.app.queue.items.is_empty());
    assert!(harness.app.view.transcript.is_empty());

    assert_eq!(harness.settle().await, "plan");
    assert!(!agents::switching(&harness.app));
    assert_eq!(harness.app.agents.active.name, "plan");
    harness.press(KeyCode::Enter);
    assert_eq!(harness.app.chat_input.input, "");
}

#[tokio::test]
async fn a_slow_switch_swaps_the_prompt_marker_for_a_spinner_until_it_settles() {
    let mut harness = Harness::new(true);
    harness.press(KeyCode::BackTab);
    assert_eq!(harness.requested_agent().await, "plan");
    // The spinner is only armed: the marker stays until the delay fires.
    assert!(harness.app.agents.spinner_at.is_some());
    assert!(!harness.app.agents.switching_indicator);
    assert!(harness.prompt_row().starts_with("> "));

    agents::show_switch_spinner(&mut harness.app);
    assert_eq!(agents::spinner_glyph(&harness.app), Some('⠋'));
    assert!(harness.prompt_row().starts_with("⠋ "));
    // The label already paints the target while the server works.
    assert_eq!(agents::displayed(&harness.app).name, "plan");

    assert_eq!(harness.settle().await, "plan");
    assert!(!harness.app.agents.switching_indicator);
    assert!(harness.app.agents.spinner_at.is_none());
    assert!(harness.prompt_row().starts_with("> "));
    assert_eq!(harness.app.agents.active.name, "plan");
    assert_eq!(harness.in_flight(), 0);
}

#[tokio::test]
async fn a_failed_switch_clears_the_in_flight_state_and_restores_the_prompt() {
    let mut harness = Harness::new(false);
    harness.type_text("draft");
    harness.press(KeyCode::BackTab);
    assert!(agents::switching(&harness.app));
    agents::show_switch_spinner(&mut harness.app);
    assert!(harness.prompt_row().starts_with("⠋ draft"));

    let event = harness.next_event().await;
    assert!(event.runtime.is_err());
    ui_event::apply(&mut harness.app, &harness.client, event.into());
    let toast = harness
        .app
        .overlays
        .toasts
        .back()
        .expect("the failure is shown");
    assert!(toast.text.starts_with("Failed to switch agent: "));

    assert!(!agents::switching(&harness.app));
    assert!(!harness.app.agents.switching_indicator);
    assert!(harness.app.agents.spinner_at.is_none());
    assert!(harness.app.agents.desired.is_none());
    assert_eq!(agents::displayed(&harness.app).name, "default");
    assert!(harness.prompt_row().starts_with("> draft"));
    assert_eq!(harness.in_flight(), 0);
    // A due spinner timer after the failure must not bring the spinner back.
    agents::show_switch_spinner(&mut harness.app);
    assert!(agents::spinner_glyph(&harness.app).is_none());
}

#[tokio::test]
async fn after_a_failed_switch_the_next_cycle_steps_past_the_refused_target() {
    let mut harness = Harness::new(false);
    harness.press(KeyCode::BackTab);
    let event = harness.next_event().await;
    ui_event::apply(&mut harness.app, &harness.client, event.into());

    let (client, frames) = Client::stub_answering();
    harness.client = Arc::new(client);
    harness.frames = Some(frames);
    harness.press(KeyCode::BackTab);
    assert_eq!(harness.requested_agent().await, "accept-edits");
    harness.settle().await;
    assert_eq!(harness.app.agents.active.name, "accept-edits");
}

#[tokio::test]
async fn an_external_switch_rebases_the_next_cycle_on_the_new_active_agent() {
    let mut harness = Harness::new(true);
    harness.press(KeyCode::BackTab);
    assert_eq!(harness.requested_agent().await, "plan");
    harness.settle().await;

    agents::apply_runtime(&mut harness.app, &runtime("accept-edits"));
    assert_eq!(harness.app.agents.active.name, "accept-edits");
    harness.press(KeyCode::BackTab);
    assert_eq!(harness.requested_agent().await, "default");
}

#[tokio::test]
async fn shift_tab_keeps_switching_agents_while_a_turn_runs() {
    let mut harness = Harness::new(true);
    harness.app.set_status(Status::Generating {
        since: Instant::now(),
    });
    for expected in ["plan", "accept-edits"] {
        harness.press(KeyCode::BackTab);
        assert_eq!(harness.requested_agent().await, expected);
        harness.settle().await;
        assert_eq!(harness.app.agents.active.name, expected);
    }
    assert!(matches!(
        harness.app.session.status,
        Status::Generating { .. }
    ));
}

#[tokio::test]
async fn a_press_during_a_switch_is_chased_after_it_settles_and_releases_the_guard() {
    let mut harness = Harness::new(true);
    harness.press(KeyCode::BackTab);
    assert_eq!(harness.requested_agent().await, "plan");
    agents::show_switch_spinner(&mut harness.app);
    harness.press(KeyCode::BackTab);
    // Coalesced: one round-trip in flight, the label already on the latest target.
    assert_eq!(harness.in_flight(), 1);
    assert_eq!(agents::displayed(&harness.app).name, "accept-edits");

    assert_eq!(harness.settle().await, "plan");
    assert_eq!(harness.requested_agent().await, "accept-edits");
    assert!(agents::switching(&harness.app));
    assert_eq!(agents::displayed(&harness.app).name, "accept-edits");
    assert!(harness.app.agents.spinner_at.is_some());

    assert_eq!(harness.settle().await, "accept-edits");
    assert_eq!(harness.app.agents.active.name, "accept-edits");
    assert!(!agents::switching(&harness.app));
    assert!(!harness.app.agents.switching_indicator);
    assert!(harness.app.agents.spinner_at.is_none());
    assert!(harness.prompt_row().starts_with("> "));
    assert_eq!(harness.in_flight(), 0);
}
