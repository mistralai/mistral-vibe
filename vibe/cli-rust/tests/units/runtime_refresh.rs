//! Runtime refreshes preserve live completion and agent choices.

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::{event, CommandEvent};
use vibe_rs::server::{AgentSafety, AgentSummary};
use vibe_rs::utils::startup_cache::StartupConfig;
use vibe_rs::{agents, event_handler};

fn agent(name: &str) -> AgentSummary {
    AgentSummary {
        name: name.to_owned(),
        display_name: name.to_owned(),
        safety: AgentSafety::Neutral,
        ..Default::default()
    }
}

#[test]
fn reload_keeps_the_runtime_wrapper_when_refreshing_skills() {
    let response = json!({
        "runtime": {
            "skills": [{
                "name": "project-skill",
                "description": "Project skill",
                "userInvocable": true,
            }],
        },
    });
    let CommandEvent::Runtime(runtime, _) = event::reload_result(response) else {
        panic!("a reload response with runtime state must refresh the runtime");
    };
    let mut app = App::default();
    app.chat_input.load_full_text("/".into());

    event_handler::apply_runtime_value(&mut app, &runtime);

    assert!(app
        .completion
        .entries
        .iter()
        .any(|entry| entry.label == "/project-skill" && entry.description == "Project skill"));
}

#[test]
fn runtime_refresh_does_not_reset_the_painted_agent_to_the_default() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.agents.active = agent("ask");
    let config = StartupConfig {
        default_agent: "plan".into(),
        agents: vec![agent("ask"), agent("plan")],
        ..Default::default()
    };

    agents::show_startup_agents(&mut app, &config);

    assert_eq!(app.agents.active.name, "ask");
}
