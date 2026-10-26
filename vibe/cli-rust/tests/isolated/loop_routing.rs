//! Model-facing loop prompts retain their text and built-in discovery.

use vibe_rs::commands::{argument_hint, entries, is_side_channel, parse};
use vibe_rs::input_modes::{classify, ClassifiedInput};
use vibe_rs::skill::Skill;

#[test]
fn every_loop_form_is_a_verbatim_prompt_not_a_local_command_or_skill() {
    let skills = [Skill::new("loop", "A conflicting skill")];
    for text in [
        "/loop",
        "/loop list",
        "/loop LS",
        "/loop cancel",
        "/loop cancel Build-Watch",
        "/loop cancel all",
        "/loop rm AbC",
        "/loop stop",
        "/loop delete all",
        "/loop 90s check build status",
        "/loop invalid prompt",
        "/loop 30s",
        "/LOOP  1h30m check | build\nand tests",
        " /loop\tlist ",
    ] {
        assert_eq!(
            classify(text, &skills),
            ClassifiedInput::Prompt {
                text: text.to_owned()
            },
            "{text:?}"
        );
        assert_eq!(parse(text), Some("/loop"));
    }
}

#[test]
fn loop_remains_a_discoverable_builtin_not_a_side_channel() {
    assert_eq!(
        entries(&[])
            .iter()
            .filter(|entry| entry.label == "/loop")
            .count(),
        1
    );
    let description = entries(&[])
        .into_iter()
        .find(|entry| entry.label == "/loop")
        .unwrap()
        .description;
    assert!(description.contains("/loop [schedule] [prompt]"));
    assert!(!is_side_channel("/loop"));
}

#[test]
fn loop_argument_hint_matches_the_loop_description() {
    let hint = argument_hint(Some('/'), "loop ").unwrap();
    let description = entries(&[])
        .into_iter()
        .find(|entry| entry.label == "/loop")
        .unwrap()
        .description;
    assert!(description.ends_with(&format!("/loop {hint}")));
}

#[test]
fn unrelated_local_commands_and_skills_keep_their_routes() {
    assert_eq!(
        classify("/help", &[]),
        ClassifiedInput::SlashCommand { command: "/help" }
    );
    assert_eq!(
        classify("/loop-review", &[Skill::new("loop-review", "")]),
        ClassifiedInput::Skill {
            command: "/loop-review".to_owned(),
            name: "loop-review".to_owned()
        }
    );
}
