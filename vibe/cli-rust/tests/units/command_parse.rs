//! Slash-command resolution: aliases, bare exit synonyms, arguments and side-channel routing.

use vibe_rs::commands::{argument_hint, is_side_channel, parse, waits_for_jobs};
use vibe_rs::input_modes::{classify, ClassifiedInput};

#[test]
fn aliases_resolve_to_their_canonical_command() {
    for (input, canonical) in [
        ("/help", "/help"),
        ("/new", "/clear"),
        ("/clean", "/clear"),
        ("/continue", "/resume"),
        ("/connectors", "/mcp"),
        (":q", "/exit"),
        (":quit", "/exit"),
    ] {
        assert_eq!(parse(input), Some(canonical), "{input}");
    }
}

#[test]
fn command_words_are_case_insensitive_and_trimmed() {
    assert_eq!(parse("/HELP"), Some("/help"));
    assert_eq!(parse("  /Model  "), Some("/model"));
    assert_eq!(parse("/Connectors"), Some("/mcp"));
}

#[test]
fn unknown_words_are_not_commands() {
    assert_eq!(parse("/definitely-not-a-command"), None);
    assert_eq!(parse("hello"), None);
    assert_eq!(parse(""), None);
}

#[test]
fn slash_commands_keep_their_arguments() {
    assert_eq!(parse("/clear seed the next conversation"), Some("/clear"));
    assert_eq!(parse("/new seed"), Some("/clear"));
    assert_eq!(parse("/exit now"), Some("/exit"));
    assert_eq!(argument_hint(Some('/'), "clear "), Some("[prompt]"));
    assert_eq!(argument_hint(Some('/'), "new "), Some("[prompt]"));
}

#[test]
fn bare_exit_synonyms_exit_only_as_the_whole_input() {
    for input in ["exit", "quit", "EXIT", "Quit", " exit ", ":Q"] {
        assert_eq!(parse(input), Some("/exit"), "{input:?}");
        assert_eq!(
            classify(input, &[]),
            ClassifiedInput::SlashCommand { command: "/exit" },
            "{input:?}"
        );
    }
    for input in [
        "exit the function early",
        "quit your job",
        "exit\nplease refactor this module",
    ] {
        assert_eq!(parse(input), None, "{input:?}");
        assert_eq!(
            classify(input, &[]),
            ClassifiedInput::Prompt {
                text: input.to_owned()
            },
            "{input:?}"
        );
    }
}

#[test]
fn commands_a_turn_never_reads_run_while_busy_and_the_others_wait_for_idle() {
    for command in [
        "/help",
        "/status",
        "/whoami",
        "/copy",
        "/log",
        "/debug",
        "/rename",
        "/data-retention",
        "/exit",
        "/theme",
        "/log-level",
        "/voice",
        "/proxy-setup",
        "/remote-project",
    ] {
        assert!(is_side_channel(command), "{command} runs while busy");
    }
    for command in [
        "/config",
        "/model",
        "/thinking",
        "/resume",
        "/clear",
        "/compact",
        "/rewind",
        "/reload",
        "/teleport",
        "/mcp",
        "/connectors",
        "/plugins",
        "/reload-plugins",
    ] {
        assert!(!is_side_channel(command), "{command} waits for idle");
    }
}

#[test]
fn side_channel_settings_wait_for_running_jobs() {
    for command in [
        "/theme",
        "/log-level",
        "/voice",
        "/proxy-setup",
        "/remote-project",
    ] {
        assert!(waits_for_jobs(command), "{command} waits for jobs");
    }
    for command in ["/help", "/exit", "/status"] {
        assert!(!waits_for_jobs(command), "{command} runs during jobs");
    }
}
