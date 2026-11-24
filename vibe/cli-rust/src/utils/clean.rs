//! Terminal-safe text for transcript payloads.

/// Sanitize captured output for display: collapse each
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

/// Drop every ECMA-48 escape sequence, 7-bit (`ESC x`) or 8-bit (C1).
fn strip_ansi(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut at = 0;
    while at < chars.len() {
        // A C1 control is the 8-bit form of `ESC` plus `c - 0x40`.
        let (kind, body) = match (chars[at], chars.get(at + 1)) {
            ('\x1b', Some(&next)) => (next, at + 2),
            (c @ '\u{80}'..='\u{9f}', _) => (char::from(c as u8 - 0x40), at + 1),
            (c, _) => {
                out.push(c);
                at += 1;
                continue;
            }
        };
        // A sequence without its final byte drops only its introducer.
        let end = match kind {
            '[' => final_after(
                &chars,
                skip_range(&chars, body, '\u{30}', '\u{3f}'),
                '\u{40}',
            ),
            '\u{20}'..='\u{2f}' => final_after(&chars, body, '\u{30}'),
            ']' | 'P' | 'X' | '^' | '_' => Some(string_end(&chars, body).unwrap_or(body)),
            '\u{30}'..='\u{7e}' => Some(body),
            _ => None,
        };
        at = end.unwrap_or(at + 1);
    }
    out
}

/// Index just past the final byte (`lo..=~`) that ends intermediates from `at`.
fn final_after(chars: &[char], at: usize, lo: char) -> Option<usize> {
    let end = skip_range(chars, at, '\u{20}', '\u{2f}');
    matches!(chars.get(end), Some(&c) if (lo..='~').contains(&c)).then_some(end + 1)
}

/// Index just past a string body (OSC, DCS, SOS, PM, APC) starting at `at`, or `None` when unterminated.
fn string_end(chars: &[char], mut at: usize) -> Option<usize> {
    loop {
        match chars.get(at) {
            Some('\x07' | '\u{9c}') => return Some(at + 1),
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

/// Control bytes except tab, dropped.
fn strip_control(text: &str) -> String {
    text.chars()
        .filter(|&c| c == '\t' || !c.is_control())
        .collect()
}

#[cfg(test)]
mod tests;
