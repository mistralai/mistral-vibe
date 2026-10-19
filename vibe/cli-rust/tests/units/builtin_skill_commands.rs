//! The built-in Vibe skill names exactly the slash commands the CLI registers.

use std::collections::BTreeSet;

use vibe_rs::commands::{entries, parse};

const SKILL_PATH: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../plugins/builtins/vibe/skills/vibe/SKILL.md"
);

/// Skill-name placeholders in the prose, not commands.
const NON_COMMAND_TOKENS: [&str; 3] = ["/skill", "/skill-name", "/word"];
/// Registered on purpose without documentation: `/stress` is a rendering load test.
const UNDOCUMENTED_BY_DESIGN: [&str; 1] = ["/stress"];
/// macOS-only, so it is missing from the registry elsewhere.
const PLATFORM_COMMANDS: [&str; 1] = ["/paste-image"];

fn is_token_char(ch: char) -> bool {
    ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '-'
}

/// Backticked `/command` tokens, skipping paths such as `/v1/traces`.
fn slash_tokens(text: &str) -> BTreeSet<String> {
    let mut tokens = BTreeSet::new();
    for (start, _) in text.match_indices("`/") {
        let rest = &text[start + 1..];
        let end = 1 + rest[1..]
            .find(|ch: char| !is_token_char(ch))
            .unwrap_or(rest.len() - 1);
        let first = rest[1..].chars().next();
        let next = rest[end..].chars().next();
        if first.is_some_and(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit())
            && next != Some('/')
        {
            tokens.insert(rest[..end].to_owned());
        }
    }
    tokens
}

fn documented() -> BTreeSet<String> {
    let skill = std::fs::read_to_string(SKILL_PATH).expect("the built-in Vibe skill is readable");
    slash_tokens(&skill)
        .into_iter()
        .filter(|token| !NON_COMMAND_TOKENS.contains(&token.as_str()))
        .collect()
}

#[test]
fn the_token_scan_survives_arguments_and_skips_paths() {
    assert_eq!(
        slash_tokens("- `/loop <interval> <prompt>` - Schedule"),
        BTreeSet::from(["/loop".to_owned()])
    );
    assert!(slash_tokens("Vibe appends `/v1/traces`.").is_empty());
    assert!(parse("/terminal-setup").is_none());
}

#[test]
fn every_documented_command_is_registered() {
    let phantoms: Vec<String> = documented()
        .into_iter()
        .filter(|token| parse(token).is_none() && !PLATFORM_COMMANDS.contains(&token.as_str()))
        .collect();
    assert!(
        phantoms.is_empty(),
        "the built-in skill documents unregistered commands: {phantoms:?}"
    );
}

#[test]
fn every_registered_command_is_documented() {
    let documented = documented();
    let missing: Vec<String> = entries(&[])
        .into_iter()
        .map(|(label, _)| label)
        .filter(|label| {
            parse(label) == Some(label.as_str())
                && !documented.contains(label)
                && !UNDOCUMENTED_BY_DESIGN.contains(&label.as_str())
        })
        .collect();
    assert!(
        missing.is_empty(),
        "commands missing from the built-in skill: {missing:?}"
    );
}
