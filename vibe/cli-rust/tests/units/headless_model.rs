//! `--model`: sent with `session/start` for this session only.
use clap::Parser;
use serde_json::{json, Value};
use vibe_rs::cli::Cli;
use vibe_rs::headless::{agent_config, start_params};

fn start_payload(args: &[&str]) -> Value {
    let options = Cli::parse_from(args).headless_options(None).unwrap();
    let config = agent_config(&options, None, None).unwrap();
    serde_json::to_value(start_params(&options, config, 200)).unwrap()
}

#[test]
fn the_model_is_picked_for_this_session_only() {
    let payload = start_payload(&["vibe-rs", "-p", "hi", "--model", "devstral-small"]);

    assert_eq!(payload["model"], json!("devstral-small"));
    assert_eq!(payload["sessionOnly"], json!(true));
}

#[test]
fn without_the_flag_no_model_is_sent() {
    let payload = start_payload(&["vibe-rs", "-p", "hi"]);

    assert!(payload.get("model").is_none());
    assert!(payload.get("sessionOnly").is_none());
}

#[test]
fn a_saved_session_is_a_usage_error() {
    for resume in [&["-c"][..], &["--resume", "abc"][..]] {
        let mut args = vec!["vibe-rs", "-p", "hi", "--model", "devstral-small"];
        args.extend_from_slice(resume);

        let err = Cli::parse_from(args).headless_options(None).unwrap_err();

        assert_eq!(err, "--model only applies to a new session");
    }
}
