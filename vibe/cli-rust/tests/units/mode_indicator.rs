//! The chat-input mode indicator names a session-wide approval bypass the policy does not imply, and a non-default agent.

use vibe_rs::agents::{indicator_agent_name, indicator_policy_name, indicator_safety};
use vibe_rs::app::App;
use vibe_rs::server::{AgentSafety, AgentSummary, ApprovalPolicySummary};

fn policy(name: &str, safety: AgentSafety) -> ApprovalPolicySummary {
    let display_name = name
        .split('-')
        .map(|word| {
            let mut chars = word.chars();
            chars
                .next()
                .map(|first| first.to_uppercase().chain(chars).collect::<String>())
                .unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ");
    ApprovalPolicySummary {
        name: name.into(),
        display_name,
        safety,
        ..ApprovalPolicySummary::default()
    }
}

#[test]
fn a_bypass_the_profile_does_not_imply_is_named_in_the_indicator() {
    for (name, safety, expected) in [
        (
            "accept-edits",
            AgentSafety::Destructive,
            "accept edits · auto-approve",
        ),
        ("audit", AgentSafety::Safe, "audit · auto-approve"),
        ("auto-approve", AgentSafety::Yolo, "auto approve"),
    ] {
        let profile = policy(name, safety);
        assert_eq!(indicator_policy_name(&profile, true), expected, "{name}");
        assert_eq!(
            indicator_safety(&profile, true),
            AgentSafety::Yolo,
            "{name}"
        );
    }
}

#[test]
fn without_a_bypass_the_indicator_is_just_the_policy() {
    for (name, safety) in [
        ("ask", AgentSafety::Neutral),
        ("audit", AgentSafety::Safe),
        ("auto-approve", AgentSafety::Yolo),
    ] {
        let profile = policy(name, safety);
        assert_eq!(
            indicator_policy_name(&profile, false),
            profile.display_name.to_lowercase()
        );
        assert_eq!(indicator_safety(&profile, false), safety);
    }
}

#[test]
fn only_a_non_default_agent_is_named() {
    let mut app = App::default();
    assert_eq!(indicator_agent_name(&app), None);
    for (name, expected) in [("default", None), ("task-lead", Some("task lead"))] {
        app.agents.active = AgentSummary {
            name: name.into(),
            display_name: if name == "default" {
                "Default"
            } else {
                "Task Lead"
            }
            .into(),
            ..AgentSummary::default()
        };
        assert_eq!(indicator_agent_name(&app).as_deref(), expected, "{name}");
    }
}
