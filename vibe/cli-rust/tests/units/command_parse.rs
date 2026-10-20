//! Slash-command resolution: aliases, bare exit synonyms, arguments and side-channel routing.

use vibe_rs::commands::{argument_hint, is_side_channel, parse};
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
fn read_only_commands_run_while_busy_and_pickers_wait_for_idle() {
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
    ] {
        assert!(is_side_channel(command), "{command} runs while busy");
    }
    for command in [
        "/config",
        "/model",
        "/thinking",
        "/theme",
        "/voice",
        "/resume",
        "/clear",
        "/compact",
        "/rewind",
        "/mcp",
        "/reload",
    ] {
        assert!(!is_side_channel(command), "{command} waits for idle");
    }
}
