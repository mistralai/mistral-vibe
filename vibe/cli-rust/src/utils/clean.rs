//! Terminal-safe text for transcript payloads (Python `clean_output`).

/// Sanitize captured output for display (Python `clean_output`): collapse each
/// `\r`-redrawn line to its final write, strip ANSI escapes, drop control bytes
/// except tab.
pub fn clean_output(content: &str) -> String {
    content
        .replace("\r\n", "\n")
        .split('\n')
        .map(|line| {
            let written = line.trim_end_matches('\r');
            let last_write = written.rsplit_once('\r').map_or(written, |(_, last)| last);
            strip_control(&strip_ansi(last_write))
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Python `_ANSI_ESCAPE`: drop CSI, OSC, and other ESC-prefixed sequences.
fn strip_ansi(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut at = 0;
    while at < chars.len() {
        if chars[at] != '\x1b' {
            out.push(chars[at]);
            at += 1;
            continue;
        }
        match chars.get(at + 1) {
            // OSC: through BEL or the ESC `\` string terminator; unterminated,
            // the two-byte form still eats the `ESC ]` pair.
            Some(']') => match osc_end(&chars, at + 2) {
                Some(end) => at = end,
                None => at += 2,
            },
            // CSI: parameter bytes, intermediate bytes, then one final byte.
            Some('[') => {
                let params = skip_range(&chars, at + 2, '\u{30}', '\u{3f}');
                let intermediates = skip_range(&chars, params, '\u{20}', '\u{2f}');
                if matches!(chars.get(intermediates), Some(&c) if ('\u{40}'..='\u{7e}').contains(&c))
                {
                    at = intermediates + 1;
                } else {
                    out.push('\x1b');
                    at += 1;
                }
            }
            // Two-byte ESC sequences; `[` (0x5b) is excluded by the Python class.
            Some(&c)
                if ('\u{40}'..='\u{5a}').contains(&c) || ('\u{5c}'..='\u{5f}').contains(&c) =>
            {
                at += 2;
            }
            _ => {
                out.push('\x1b');
                at += 1;
            }
        }
    }
    out
}

/// Index just past an OSC body starting at `at`, or `None` when unterminated.
fn osc_end(chars: &[char], mut at: usize) -> Option<usize> {
    loop {
        match chars.get(at) {
            Some('\x07') => return Some(at + 1),
            Some('\x1b') => return (chars.get(at + 1) == Some(&'\\')).then_some(at + 2),
            Some(_) => at += 1,
            None => return None,
        }
    }
}

/// First index at `at` or later whose char falls outside `lo..=hi`.
fn skip_range(chars: &[char], mut at: usize, lo: char, hi: char) -> usize {
    while matches!(chars.get(at), Some(&c) if (lo..=hi).contains(&c)) {
        at += 1;
    }
    at
}

/// Python `_CONTROL_BYTES`: control bytes except tab, dropped.
fn strip_control(text: &str) -> String {
    text.chars()
        .filter(|c| !matches!(c, '\u{0}'..='\u{8}' | '\u{b}'..='\u{1f}' | '\u{7f}'))
        .collect()
}

#[cfg(test)]
mod tests {
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
}
