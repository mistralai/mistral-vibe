//! The chat-input mode indicator names a session-wide approval bypass the profile does not imply.

use vibe_rs::agents::{indicator_agent_name, indicator_safety};
use vibe_rs::server::{AgentSafety, AgentSummary};

fn agent(name: &str, safety: AgentSafety) -> AgentSummary {
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
    AgentSummary {
        name: name.into(),
        display_name,
        safety,
        ..AgentSummary::default()
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
        let profile = agent(name, safety);
        assert_eq!(indicator_agent_name(&profile, true), expected, "{name}");
        assert_eq!(
            indicator_safety(&profile, true),
            AgentSafety::Yolo,
            "{name}"
        );
    }
}

#[test]
fn without_a_bypass_the_indicator_is_just_the_agent() {
    for (name, safety) in [
        ("ask", AgentSafety::Neutral),
        ("audit", AgentSafety::Safe),
        ("auto-approve", AgentSafety::Yolo),
    ] {
        let profile = agent(name, safety);
        assert_eq!(
            indicator_agent_name(&profile, false),
            profile.display_name.to_lowercase()
        );
        assert_eq!(indicator_safety(&profile, false), safety);
    }
}
