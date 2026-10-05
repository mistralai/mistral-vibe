//! Inline argument hints shown after a slash command and its space.

use vibe_rs::commands::argument_hint;

const LOOP: Option<&str> = Some("[schedule] [prompt]");
const MCP: Option<&str> = Some("[name] | add <url> | status | login <alias> | logout <alias>");

#[test]
fn hint_shows_only_between_the_space_and_the_first_argument() {
    for (prefix, body, hint) in [
        (None, "/loop ", LOOP),
        (None, "/LOOP ", LOOP),
        (Some('/'), "loop ", LOOP),
        (None, "/loop", None),
        (None, "/loop  ", None),
        (None, "/loop 5", None),
        (None, "/loop\t", None),
        (None, "loop ", None),
        (Some('!'), "loop ", None),
        (Some('/'), "/loop ", None),
    ] {
        assert_eq!(argument_hint(prefix, body), hint, "{prefix:?} {body:?}");
    }
}

#[test]
fn every_alias_of_a_command_shares_its_hint() {
    for body in ["/mcp ", "/connectors "] {
        assert_eq!(argument_hint(None, body), MCP, "{body:?}");
    }
    for body in ["/clear ", "/new "] {
        assert_eq!(argument_hint(None, body), Some("[prompt]"), "{body:?}");
    }
}

#[test]
fn commands_without_arguments_or_unknown_words_show_no_hint() {
    for body in ["/help ", "/status ", "/exit ", "/unknown ", "/ "] {
        assert_eq!(argument_hint(None, body), None, "{body:?}");
    }
}
