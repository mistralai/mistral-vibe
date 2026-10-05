use super::*;

#[test]
fn strips_csi_osc_and_two_byte_escapes() {
    assert_eq!(clean_output("\x1b[31mred\x1b[0m plain"), "red plain");
    assert_eq!(clean_output("\x1b]0;title\x07body"), "body");
    assert_eq!(
        clean_output("\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\"),
        "link"
    );
    assert_eq!(clean_output("a\x1bcb"), "acb");
    assert_eq!(clean_output("\x1b[?25lhide"), "hide");
    // An unterminated OSC still consumes its `ESC ]` pair.
    assert_eq!(clean_output("\x1b]no term"), "no term");
    // A CSI without a final byte keeps its text, minus the stray ESC.
    assert_eq!(clean_output("\x1b[31"), "[31");
    // The connector-error payload of the `mcp_connector_error_ansi` scenario.
    assert_eq!(
        clean_output(
            "\x1b[31mconnector failed\x1b[0m\x1b[2K\r\x1b[32mconnectors unavailable\x1b[0m"
        ),
        "connectors unavailable"
    );
}

#[test]
fn collapses_carriage_return_redraws_to_the_last_write() {
    assert_eq!(clean_output("first\rsecond"), "second");
    assert_eq!(clean_output("a\rb\rc"), "c");
    assert_eq!(clean_output("kept\r"), "kept");
    assert_eq!(clean_output("kept\r\r"), "kept");
}

#[test]
fn normalises_crlf_to_lf() {
    assert_eq!(clean_output("one\r\ntwo"), "one\ntwo");
    assert_eq!(clean_output("one\r\ntwo\r\nthree"), "one\ntwo\nthree");
    assert_eq!(clean_output("x\r\r\ny"), "x\ny");
}

#[test]
fn drops_control_bytes_but_keeps_tabs() {
    assert_eq!(clean_output("a\tb\x00\x7f\x01"), "a\tb");
}
