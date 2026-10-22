//! Headless-mode CLI flag parsing.
use clap::Parser;
use vibe_rs::cli::{Cli, HeadlessSession, OutputFormat};

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
    assert_eq!(
        cli.headless_options(None).unwrap_err(),
        "No prompt provided for programmatic mode"
    );
}

#[test]
fn empty_prompt_is_falsy_and_falls_back_to_stdin() {
    let cli = Cli::parse_from(["vibe-rs", "--prompt", ""]);
    assert!(cli.is_headless());
    assert_eq!(
        cli.resolve_prompt(Some("from stdin".into())).unwrap(),
        "from stdin"
    );
    assert_eq!(
        cli.headless_options(None).unwrap_err(),
        "No prompt provided for programmatic mode"
    );
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
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--agent", "ask"]);
    assert_eq!(
        cli.headless_options(None).unwrap().agent.as_deref(),
        Some("ask")
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
    // A repeatable flag: commas are kept verbatim, not split.
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
    // A repeatable flag: a comma is part of the path, not a separator.
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--add-dir", "/a,/b"]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.add_dir.len(), 1);
}

#[test]
fn teleport_is_rejected_until_headless_support_exists() {
    assert!(Cli::try_parse_from(["vibe-rs", "-p", "hi", "--teleport"]).is_err());
}

#[test]
fn teleport_takes_the_positional_prompt() {
    let cli = Cli::try_parse_from(["vibe-rs", "--teleport", "fix the tests"]).unwrap();
    assert!(cli.teleport);
    assert_eq!(cli.initial_prompt.as_deref(), Some("fix the tests"));
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
    // `echo hi | vibe ""`: the empty positional falls back to the piped
    // prompt.
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

#[test]
fn time_limit_flag_parsed() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--time-limit", "30.5"]);
    assert!(cli.is_headless());
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.time_limit, Some(30.5));
}

#[test]
fn output_dir_flag_parsed() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--output-dir", "/tmp/exports"]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(
        opts.output_dir.as_ref().map(|p| p.to_path_buf()),
        Some(std::path::PathBuf::from("/tmp/exports"))
    );
}

#[test]
fn prompt_file_conflicts_with_prompt_text() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--prompt-file", "/tmp/p.txt"]);
    assert_eq!(
        cli.headless_options(None).unwrap_err(),
        "Pass either -p TEXT or --prompt-file"
    );
}

#[test]
fn bare_prompt_flag_combines_with_prompt_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("prompt.md");
    std::fs::write(&path, "from the file").unwrap();
    let cli = Cli::parse_from(["vibe-rs", "-p", "--prompt-file", path.to_str().unwrap()]);
    assert_eq!(cli.headless_options(None).unwrap().prompt, "from the file");
}

#[test]
fn a_repeated_flag_keeps_its_last_value() {
    let cli = Cli::parse_from(["vibe-rs", "--workdir", "/a", "-p", "x", "--workdir", "/b"]);
    assert_eq!(cli.cwd.as_deref(), Some(std::path::Path::new("/b")));
}

#[test]
fn headless_resume_with_an_id_reopens_that_session() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "go on", "--resume", "abc"]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.session, HeadlessSession::Resume("abc".into()));
}

#[test]
fn headless_continue_reopens_the_latest_session() {
    let cli = Cli::parse_from(["vibe-rs", "-c", "-p", "go on"]);
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.session, HeadlessSession::Continue);
}

#[test]
fn headless_resume_without_an_id_is_a_usage_error() {
    let cli = Cli::parse_from(["vibe-rs", "-p", "go on", "--resume"]);
    assert_eq!(
        cli.headless_options(None).unwrap_err(),
        "--resume requires a session ID in programmatic mode"
    );
}

#[test]
fn continue_and_resume_still_conflict() {
    assert!(Cli::try_parse_from(["vibe-rs", "-c", "--resume", "abc"]).is_err());
}

#[test]
fn prompt_file_resolves_content() {
    let dir = std::env::temp_dir().join(format!("vibe-test-prompt-file-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("prompt.txt");
    std::fs::write(&path, "  hello from file  \n").unwrap();

    let cli = Cli::parse_from(["vibe-rs", "--prompt-file", path.to_str().unwrap()]);
    assert!(cli.is_headless());
    let opts = cli.headless_options(None).unwrap();
    assert_eq!(opts.prompt, "hello from file");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn prompt_file_missing_file_is_a_usage_error() {
    let cli = Cli::parse_from(["vibe-rs", "--prompt-file", "/nonexistent/path/prompt.txt"]);
    assert!(cli.is_headless());
    let err = cli.headless_options(None).unwrap_err();
    assert!(
        err.starts_with("Cannot read the prompt file /nonexistent/path/prompt.txt: "),
        "{err}"
    );
}

#[test]
fn prompt_file_empty_file_falls_back_to_stdin() {
    let dir = std::env::temp_dir().join(format!("vibe-test-empty-prompt-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("empty.txt");
    std::fs::write(&path, "  \n  ").unwrap();

    let cli = Cli::parse_from(["vibe-rs", "--prompt-file", path.to_str().unwrap()]);
    // Empty file content falls back to stdin.
    assert_eq!(
        cli.resolve_prompt(Some("from stdin".into())).unwrap(),
        "from stdin"
    );

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn headless_paths_resolve_against_invocation_cwd() {
    use std::path::Path;

    // --output-dir/--prompt-file resolve right after parse, before
    // --workdir changes directory, so relative paths name what the caller
    // meant rather than landing inside the workdir.
    let mut cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--output-dir", "rel-out"]);
    cli.resolve_headless_paths(Path::new("/invocation/cwd"));
    assert_eq!(
        cli.output_dir.as_deref(),
        Some(Path::new("/invocation/cwd/rel-out"))
    );

    let mut cli = Cli::parse_from(["vibe-rs", "--prompt-file", "prompt.txt"]);
    cli.resolve_headless_paths(Path::new("/invocation/cwd"));
    assert_eq!(
        cli.prompt_file.as_deref(),
        Some(Path::new("/invocation/cwd/prompt.txt"))
    );

    // Absolute paths are untouched.
    let mut cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--output-dir", "/abs/out"]);
    cli.resolve_headless_paths(Path::new("/invocation/cwd"));
    assert_eq!(cli.output_dir.as_deref(), Some(Path::new("/abs/out")));
}

#[test]
fn headless_paths_leave_other_flags_alone() {
    use std::path::Path;

    let mut cli = Cli::parse_from(["vibe-rs", "-p", "hi"]);
    cli.resolve_headless_paths(Path::new("/invocation/cwd"));
    assert!(cli.output_dir.is_none());
    assert!(cli.prompt_file.is_none());
}

#[test]
fn validate_add_dirs_accepts_directories_and_rejects_missing_paths() {
    let dir = std::env::temp_dir();
    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--add-dir", dir.to_str().unwrap()]);
    assert!(cli.validate_add_dirs().is_ok());

    let cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--add-dir", "/nonexistent/add-dir"]);
    assert_eq!(
        cli.validate_add_dirs().unwrap_err(),
        "--add-dir path does not exist or is not a directory: /nonexistent/add-dir"
    );
}

#[test]
fn for_cli_builds_the_run_right_after_parse() {
    use std::path::Path;

    use vibe_rs::headless_run::HeadlessRun;

    // The run the arguments ask for, built
    // as soon as they are parsed, or None for interactive mode.
    let cli = Cli::parse_from([
        "vibe-rs",
        "-p",
        "hi",
        "--output-dir",
        "/tmp/out",
        "--max-turns",
        "2",
        "--time-limit",
        "30",
    ]);
    let run = HeadlessRun::for_cli(&cli).unwrap();
    assert_eq!(run.output_dir.as_deref(), Some(Path::new("/tmp/out")));
    assert_eq!(run.stop.limits().max_turns, Some(2));
    assert_eq!(run.stop.limits().time_limit_s, Some(30.0));
    assert_eq!(run.stop.requested_outcome(), None);

    let cli = Cli::parse_from(["vibe-rs"]);
    assert!(HeadlessRun::for_cli(&cli).is_none());
}

#[test]
fn prompt_given_skips_stdin_only_for_text_or_a_non_empty_prompt_file() {
    let dir = std::env::temp_dir().join(format!("vibe-test-prompt-given-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let full = dir.join("full.txt");
    let empty = dir.join("empty.txt");
    std::fs::write(&full, "task").unwrap();
    std::fs::write(&empty, " \n").unwrap();
    let given = |args: &[&str]| Cli::parse_from([&["vibe-rs"][..], args].concat()).prompt_given();

    assert!(given(&["-p", "--prompt-file", full.to_str().unwrap()]));
    assert!(given(&["-p", "hi"]));
    assert!(given(&["--prompt-file", "/nonexistent/prompt.txt"]));
    assert!(!given(&["--prompt-file", empty.to_str().unwrap()]));
    assert!(!given(&["-p"]));
    assert!(!given(&[]));

    std::fs::remove_dir_all(&dir).ok();
}
