//! Inline argument hints shown after a slash command and its space.

use vibe_rs::commands::argument_hint;

const LOOP: Option<&str> = Some("[schedule] [prompt]");
const MCP: Option<&str> = Some("[name] | add <url> | status | login <alias> | logout <alias>");

#[test]
fn hint_shows_only_between_the_space_and_the_first_argument() {
    for (prefix, body, hint) in [
        (Some('/'), "loop ", LOOP),
        (Some('/'), "LOOP ", LOOP),
        (Some('/'), "loop", None),
        (Some('/'), "loop  ", None),
        (Some('/'), "loop 5", None),
        (Some('/'), "loop\t", None),
        (Some('/'), "/loop ", None),
        (Some('!'), "loop ", None),
        (None, "loop ", None),
    ] {
        assert_eq!(argument_hint(prefix, body), hint, "{prefix:?} {body:?}");
    }
}

#[test]
fn a_literal_slash_in_a_prompt_shows_no_hint() {
    for body in ["/loop ", "/model ", "/mcp "] {
        assert_eq!(argument_hint(None, body), None, "{body:?}");
    }
}

#[test]
fn every_alias_of_a_command_shares_its_hint() {
    for body in ["mcp ", "connectors "] {
        assert_eq!(argument_hint(Some('/'), body), MCP, "{body:?}");
    }
    for body in ["clear ", "new "] {
        assert_eq!(argument_hint(Some('/'), body), Some("[prompt]"), "{body:?}");
    }
}

#[test]
fn commands_without_arguments_or_unknown_words_show_no_hint() {
    for body in ["help ", "status ", "exit ", "unknown ", " "] {
        assert_eq!(argument_hint(Some('/'), body), None, "{body:?}");
    }
}
