//! `/leanstall` and `/unleanstall`: wire types, short-circuits, round-trips, and input gating.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::agents;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::leanstall::{self, Event};
use vibe_rs::commands::{submission, CommandEvent};
use vibe_rs::config;
use vibe_rs::server::request_error::describe;
use vibe_rs::server::{
    AgentSummary, AgentType, AgentsListResponse, Client, HistoryEntry, MessageContent,
    RequestFailure, RpcError,
};

fn agent(name: &str) -> AgentSummary {
    AgentSummary {
        name: name.to_owned(),
        display_name: name.to_owned(),
        ..AgentSummary::default()
    }
}

#[test]
fn agents_list_response_reads_the_active_agent_and_the_list() {
    let response: AgentsListResponse = serde_json::from_value(json!({
        "active": {"name": "lean", "displayName": "Lean", "agentType": "subagent"},
        "agents": [
            {"name": "default", "displayName": "default"},
            {"name": "lean", "displayName": "lean"},
        ],
    }))
    .unwrap();
    assert_eq!(
        response.active,
        AgentSummary {
            name: "lean".to_owned(),
            display_name: "Lean".to_owned(),
            agent_type: AgentType::Subagent,
            ..AgentSummary::default()
        }
    );
    assert_eq!(response.agents, vec![agent("default"), agent("lean")]);
}

fn app_with(agents: &[&str]) -> App {
    let mut app = App::default();
    app.session.session_id = Some("session".to_owned());
    app.agents.all = agents.iter().map(|name| agent(name)).collect();
    app.agents.active = agent("default");
    app
}

fn rows(app: &App) -> Vec<(String, String)> {
    app.view
        .transcript
        .lines()
        .filter_map(|entry| match entry.entry {
            HistoryEntry::Message(message) => Some((
                message.role.clone(),
                message
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        MessageContent::Text { text } => Some(text.as_str()),
                        _ => None,
                    })
                    .collect(),
            )),
            _ => None,
        })
        .collect()
}

fn row(role: &str, text: &str) -> Vec<(String, String)> {
    vec![(role.to_owned(), text.to_owned())]
}

fn pending_commits(app: &App) -> u32 {
    app.pending_commits.load(Ordering::Relaxed)
}

#[test]
fn install_when_lean_is_present_answers_locally() {
    let mut app = app_with(&["default", "lean"]);
    leanstall::install_lean(&mut app, &Arc::new(Client::stub()));
    assert_eq!(
        rows(&app),
        row("command_result", "Lean agent is already installed.")
    );
    assert_eq!(pending_commits(&app), 0, "no request is started");
    assert!(!app.agents.installing);
}

#[test]
fn uninstall_when_lean_is_absent_answers_locally() {
    let mut app = app_with(&["default"]);
    leanstall::uninstall_lean(&mut app, &Arc::new(Client::stub()));
    assert_eq!(
        rows(&app),
        row("command_result", "Lean agent is not installed.")
    );
    assert_eq!(pending_commits(&app), 0, "no request is started");
    assert!(!app.agents.installing);
}

#[tokio::test]
async fn install_and_uninstall_round_trip_their_method_for_lean() {
    for (installed, present, method) in [
        (true, &["default"][..], "agents/install"),
        (false, &["default", "lean"][..], "agents/uninstall"),
    ] {
        let (client, mut frames) = Client::stub_answering();
        let client = Arc::new(client);
        let (tx, mut rx) = mpsc::channel(4);
        let mut app = app_with(present);
        app.command_tx = Some(tx);
        if installed {
            leanstall::install_lean(&mut app, &client);
        } else {
            leanstall::uninstall_lean(&mut app, &client);
        }
        assert!(app.agents.installing);
        let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
            .await
            .expect("the request is sent")
            .expect("the stub answers");
        assert_eq!(frame["method"], method);
        assert_eq!(
            frame["params"],
            json!({"sessionId": "session", "agentName": "lean"})
        );
        let Some(CommandEvent::Leanstall(event)) = rx.recv().await else {
            panic!("the answer reaches the reducer as a Leanstall event");
        };
        assert_eq!(event.installed, installed);
        assert!(
            event.result.is_err(),
            "the stub's null is not an agent list"
        );
        assert_eq!(pending_commits(&app), 1, "the reducer releases the commit");
        leanstall::apply_event(&mut app, *event);
        assert!(!app.agents.installing);
        assert_eq!(app.agents.all.len(), present.len());
        assert_eq!(rows(&app)[0].0, "command_error");
    }
}

#[tokio::test]
async fn input_waits_while_an_install_is_in_flight() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    for value in ["/unleanstall", "hello"] {
        let mut app = app_with(&["default", "lean"]);
        app.session.status = Status::Ready;
        app.agents.installing = true;
        app.chat_input.load_full_text(value.to_owned());
        submission::submit(&mut app, &client, &config_tx);
        assert_eq!(
            app.chat_input.full_text(),
            value,
            "{value} stays in the composer"
        );
        assert!(rows(&app).is_empty());
    }
}

fn replay_after_startup_switch(command: &str) -> App {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = app_with(&["default"]);
    app.session.status = Status::Ready;
    app.agents.switch_active = true;
    app.pending_commands.push(command.to_owned());
    submission::flush_pending(&mut app, &client, &config_tx);
    app
}

#[tokio::test]
async fn a_deferred_leanstall_does_not_race_the_startup_switch() {
    let app = replay_after_startup_switch("/leanstall");
    assert!(!app.agents.installing);
    assert_eq!(pending_commits(&app), 0, "no request is started");
    assert_eq!(
        rows(&app).last(),
        Some(&("command_error".to_owned(), leanstall::SWITCHING.to_owned()))
    );
}

#[tokio::test]
async fn other_deferred_commands_still_run_after_a_startup_switch() {
    let app = replay_after_startup_switch("/theme");
    assert!(app.theme_picker.open);
    assert!(app.chat_input.full_text().is_empty());
}

#[test]
fn shift_tab_waits_while_an_install_is_in_flight() {
    let mut app = app_with(&["default", "lean"]);
    app.agents.installing = true;
    agents::cycle(&mut app, &Arc::new(Client::stub()));
    assert_eq!(app.agents.desired, None);
}

#[test]
fn describe_prefers_the_server_message() {
    let rpc = RpcError {
        code: json!("conflict"),
        message: "Session is busy".to_owned(),
        data: None,
    };
    assert_eq!(
        describe(&anyhow::Error::new(RequestFailure::Rpc(rpc))),
        "Session is busy"
    );
    assert_eq!(
        describe(&anyhow::Error::new(RequestFailure::AppServerClosed)),
        RequestFailure::AppServerClosed.to_string()
    );
}

#[test]
fn an_answer_replaces_the_agents_and_reports_its_direction() {
    for (installed, notice) in [
        (true, "Lean agent installed."),
        (false, "Lean agent uninstalled."),
    ] {
        let mut app = app_with(&["default"]);
        let response = AgentsListResponse {
            active: agent("ask"),
            agents: vec![agent("ask"), agent("lean")],
        };
        leanstall::apply_event(
            &mut app,
            Event {
                installed,
                result: Ok(response.clone()),
            },
        );
        assert_eq!(app.agents.all, response.agents);
        assert_eq!(app.agents.active, response.active);
        assert_eq!(rows(&app), row("command_result", notice));
    }
}

#[test]
fn a_failure_shows_the_bare_server_message_and_keeps_the_agents() {
    let mut app = app_with(&["default"]);
    leanstall::apply_event(
        &mut app,
        Event {
            installed: true,
            result: Err("Session is busy".to_owned()),
        },
    );
    assert_eq!(app.agents.all, vec![agent("default")]);
    assert_eq!(app.agents.active, agent("default"));
    assert_eq!(rows(&app), row("command_error", "Session is busy"));
}
