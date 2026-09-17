//! Headless-mode CLI flag parsing, mirroring Python `parse_arguments`.
use clap::Parser;
use vibe_rs::cli::{Cli, OutputFormat};

#[test]
fn no_prompt_is_interactive() {
    let cli = Cli::parse_from(["vibe-rs"]);
    assert!(!cli.is_headless());
}

#[test]
fn prompt_flag_enables_headless() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hello"]);
    assert!(cli.is_headless());
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.prompt, "hello");
}

#[test]
fn long_prompt_flag_enables_headless() {
    let cli = Cli::parse_from(["vibe-rs", "--prompt", "hello"]);
    assert!(cli.is_headless());
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.prompt, "hello");
}

#[test]
fn prompt_with_stdin_fallback() {
    let cli = Cli::parse_from(["vibe-rs", "-p"]);
    assert!(cli.is_headless());
    let opts = cli.headless_options(Some("from stdin".into())).unwrap();
    assert_eq!(opts.prompt, "from stdin");
}

#[test]
fn prompt_none_without_stdin_is_no_prompt_error() {
    let cli = Cli::parse_from(["vibe-rs", "-p"]);
    assert!(cli.is_headless());
    assert!(cli.headless_options(None).is_none());
}

#[test]
fn empty_prompt_is_falsy_and_falls_back_to_stdin() {
    let cli = Cli::parse_from(["vibe-rs", "--prompt", ""]);
    assert!(cli.is_headless());
    assert_eq!(
        cli.resolve_prompt(Some("from stdin".into())).as_deref(),
        Some("from stdin")
    );
    assert!(cli.headless_options(None).is_none());
}

#[test]
fn output_format_defaults_to_text() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi"]);
    assert_eq!(cli.output, OutputFormat::Text);
}

#[test]
fn output_format_json() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--output", "json"]);
    assert_eq!(cli.output, OutputFormat::Json);
}

#[test]
fn output_format_streaming() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--output", "streaming"]);
    assert_eq!(cli.output, OutputFormat::Streaming);
}

#[test]
fn budget_flags_parsed() {
    let cli = Cli::parse_from([
        "vibe-rs",
        "-p",
        "hi",
        "--max-turns",
        "5",
        "--max-price",
        "1.5",
        "--max-tokens",
        "10000",
    ]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.max_turns, Some(5));
    assert_eq!(opts.max_price, Some(1.5));
    assert_eq!(opts.max_tokens, Some(10000));
}

#[test]
fn auto_approve_and_yolo_flags() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--yolo"]);
    assert!(cli.headless_options(None).unwrap().auto_approve);
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--auto-approve"]);
    assert!(cli.headless_options(None).unwrap().auto_approve);
}

#[test]
fn agent_flag_parsed() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--agent", "plan"]);
    assert_eq!(
        cli.headless_options(None).unwrap().agent.as_deref(),
        Some("plan")
    );
}

#[test]
fn enabled_tools_append_repeated() {
    let cli = Cli::parse_from([
        "vibe-rs",
        "-p",
        "hi",
        "--enabled-tools",
        "bash",
        "--enabled-tools",
        "read",
    ]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.enabled_tools, vec!["bash", "read"]);
}

#[test]
fn tools_commas_are_literal_not_separators() {
    // Python uses action="append": commas are kept verbatim, not split.
    let cli = Cli::parse_from([
        "vibe-rs",
        "-p",
        "hi",
        "--enabled-tools",
        "bash,read",
        "--disabled-tools",
        "re:web.*",
    ]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.enabled_tools, vec!["bash,read"]);
    assert_eq!(opts.disabled_tools, vec!["re:web.*"]);
}

#[test]
fn trust_flag_parsed() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--trust"]);
    assert!(cli.headless_options(None).unwrap().trust);
}

#[test]
fn add_dir_append_repeated() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--add-dir", "/a", "--add-dir", "/b"]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.add_dir.len(), 2);
}

#[test]
fn add_dir_comma_is_a_single_literal_path() {
    // Python uses action="append": a comma is part of the path, not a separator.
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--add-dir", "/a,/b"]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.add_dir.len(), 1);
}

#[test]
fn continue_and_resume_are_rejected_in_headless() {
    for args in [
        &["vibe-rs", "-p", "hi", "--continue"][..],
        &["vibe-rs", "-p", "hi", "--resume", "session-id"][..],
    ] {
        assert!(Cli::try_parse_from(args).is_err());
    }
}

#[test]
fn teleport_is_rejected_until_headless_support_exists() {
    assert!(Cli::try_parse_from(["vibe-rs", "-p", "hi", "--teleport"]).is_err());
}

#[test]
fn positional_prompt_is_initial_prompt_not_headless() {
    let cli = Cli::parse_from(["vibe-rs", "hello"]);
    assert!(!cli.is_headless());
    assert_eq!(cli.initial_prompt.as_deref(), Some("hello"));
}

#[test]
fn interactive_positional_prompt_wins_over_stdin() {
    let cli = Cli::parse_from(["vibe-rs", "hello"]);
    assert_eq!(
        cli.interactive_initial_prompt(Some("from stdin".into())),
        Some("hello".into())
    );
}

#[test]
fn interactive_empty_positional_falls_back_to_stdin() {
    // `echo hi | vibe ""`: Python `initial_prompt or stdin_prompt` treats the
    // empty positional as falsy and sends the piped prompt.
    let cli = Cli::parse_from(["vibe-rs", ""]);
    assert_eq!(
        cli.interactive_initial_prompt(Some("from stdin".into())),
        Some("from stdin".into())
    );
}

#[test]
fn interactive_no_positional_uses_stdin() {
    let cli = Cli::parse_from(["vibe-rs"]);
    assert_eq!(
        cli.interactive_initial_prompt(Some("from stdin".into())),
        Some("from stdin".into())
    );
}

#[test]
fn interactive_no_prompt_anywhere_is_none() {
    let cli = Cli::parse_from(["vibe-rs"]);
    assert_eq!(cli.interactive_initial_prompt(None), None);
}
