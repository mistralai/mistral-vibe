//! Session-exit summary formatting and the usage-baseline delta (Python `session_exit.py`).

use vibe_rs::server::TokenUsage;
use vibe_rs::session_exit::{
    format_session_usage, orange_span, session_resume_message, usage_since_baseline,
    SessionExitSummary,
};

/// rich's `[bold dark_orange]` span on a 256-color-or-better terminal.
const ORANGE: &str = "\x1b[1;38;5;208m";
/// The same span downgraded by rich on a 16-color terminal.
const ORANGE_16: &str = "\x1b[1;91m";
/// What rich keeps of the span under NO_COLOR: the bold, never the color.
const BOLD: &str = "\x1b[1m";
const RESET: &str = "\x1b[0m";

fn summary(session_id: Option<&str>, usage: TokenUsage) -> SessionExitSummary {
    SessionExitSummary {
        session_id: session_id.map(str::to_owned),
        usage,
    }
}

#[test]
fn usage_line_matches_python_with_thousands_separators() {
    assert_eq!(
        format_session_usage(TokenUsage {
            input_tokens: 1234567,
            output_tokens: 890,
        }),
        "Total tokens used this session: input=1,234,567 output=890 (total=1,235,457)"
    );
    assert_eq!(
        format_session_usage(TokenUsage::default()),
        "Total tokens used this session: input=0 output=0 (total=0)"
    );
}

#[test]
fn without_a_session_id_nothing_prints() {
    assert_eq!(session_resume_message(None, Some(ORANGE)), "");
    assert_eq!(
        session_resume_message(Some(&summary(None, TokenUsage::default())), Some(ORANGE)),
        ""
    );
}

#[test]
fn resume_lines_carry_richs_sgr_spans() {
    assert_eq!(
        session_resume_message(
            Some(&summary(
                Some("abcd1234efgh5678"),
                TokenUsage {
                    input_tokens: 1000,
                    output_tokens: 250,
                }
            )),
            Some(ORANGE)
        ),
        "\nTotal tokens used this session: input=1,000 output=250 (total=1,250)\n\n\
         To continue this session, run: \u{1b}[1;38;5;208mvibe --continue\u{1b}[0m\n\
         Or: \u{1b}[1;38;5;208mvibe --resume abcd1234\u{1b}[0m\n"
    );
}

#[test]
fn without_color_the_lines_are_plain() {
    assert_eq!(
        session_resume_message(
            Some(&summary(Some("abcd1234efgh"), TokenUsage::default())),
            None
        ),
        "\nTotal tokens used this session: input=0 output=0 (total=0)\n\n\
         To continue this session, run: vibe --continue\n\
         Or: vibe --resume abcd1234\n"
    );
}

#[test]
fn a_short_session_id_stays_whole() {
    assert!(session_resume_message(
        Some(&summary(Some("abc"), TokenUsage::default())),
        Some(ORANGE)
    )
    .ends_with(&format!("Or: {ORANGE}vibe --resume abc{RESET}\n")));
}

#[test]
fn baseline_delta_clamps_each_component_at_zero() {
    let delta = usage_since_baseline(
        TokenUsage {
            input_tokens: 100,
            output_tokens: 50,
        },
        TokenUsage {
            input_tokens: 150,
            output_tokens: 30,
        },
    );
    assert_eq!(
        delta,
        TokenUsage {
            input_tokens: 0,
            output_tokens: 20,
        }
    );
    assert_eq!(
        usage_since_baseline(TokenUsage::default(), TokenUsage::default()),
        TokenUsage::default()
    );
}

/// One env-mutating test so the parallel tests in this binary never race on it.
#[test]
fn orange_span_matches_richs_color_tiers() {
    // A truecolor COLORTERM overrides every tier, so pin it down first.
    std::env::remove_var("COLORTERM");
    std::env::set_var("TERM", "xterm-256color");
    // NO_COLOR on a piped stdout: plain, the tier a NO_COLOR-first check misses.
    std::env::set_var("NO_COLOR", "1");
    assert_eq!(orange_span(false), None);
    // NO_COLOR on a TTY: rich keeps the bold and drops the color.
    assert_eq!(orange_span(true), Some(BOLD));
    std::env::remove_var("NO_COLOR");
    // Without NO_COLOR the depth-appropriate escape returns.
    assert_eq!(orange_span(false), None);
    assert_eq!(orange_span(true), Some(ORANGE));
    std::env::set_var("COLORTERM", "truecolor");
    assert_eq!(orange_span(true), Some(ORANGE));
    std::env::remove_var("COLORTERM");
    // A 16-color terminal gets rich's dark_orange-to-bright-red downgrade.
    std::env::set_var("TERM", "xterm");
    assert_eq!(orange_span(true), Some(ORANGE_16));
    // A dumb terminal is colorless at every tier, NO_COLOR or not.
    std::env::set_var("TERM", "dumb");
    assert_eq!(orange_span(true), None);
    std::env::set_var("NO_COLOR", "1");
    assert_eq!(orange_span(true), None);
    std::env::remove_var("NO_COLOR");
    std::env::set_var("TERM", "unknown");
    assert_eq!(orange_span(true), None);
    std::env::remove_var("TERM");
}
