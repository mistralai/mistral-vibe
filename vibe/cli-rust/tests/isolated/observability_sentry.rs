//! Sentry lifecycle: the `SENTRY_DSN` opt-in binds the startup window, and the
//! Ready config gate rebinds or drops that client. The truth table of
//! `bind_decision` plus one stateful walk over the guard.

use vibe_rs::observability::sentry::{
    bind_decision, init_pre_ready, init_sentry, is_bound, Decision,
};

/// Parseable but unreachable: the SDK tolerates it and sending fails quietly.
const BOGUS_DSN: &str = "http://key@127.0.0.1:9999/42";

/// The gate is unknowable before Ready, so only the env opt-in binds.
#[test]
fn the_bind_decision_truth_table() {
    assert_eq!(bind_decision(None, None), Decision::Hold);
    assert_eq!(bind_decision(Some(BOGUS_DSN), None), Decision::Bind);
    // Ready: the gate is authoritative, over the env opt-in as well.
    assert_eq!(bind_decision(None, Some(true)), Decision::Bind);
    assert_eq!(bind_decision(Some(BOGUS_DSN), Some(true)), Decision::Bind);
    assert_eq!(bind_decision(None, Some(false)), Decision::Drop);
    assert_eq!(bind_decision(Some(BOGUS_DSN), Some(false)), Decision::Drop);
}

/// Walks pre-ready bind, Ready drop, and Ready rebind in order. Owns
/// `SENTRY_DSN` and the guard; nothing else in this binary reads either.
#[test]
fn the_env_opt_in_binds_and_the_ready_gate_reconfigures() {
    // Default run: no env opt-in, compiled-in DSN `None` — stays silent.
    std::env::remove_var("SENTRY_DSN");
    assert!(!init_pre_ready(false, Default::default()));
    assert!(!is_bound());

    std::env::set_var("SENTRY_DSN", BOGUS_DSN);
    assert!(init_pre_ready(false, Default::default()));
    assert!(is_bound());

    // Ready with telemetry disabled drops the pre-ready client.
    assert!(!init_sentry(false, false, Default::default()));
    assert!(!is_bound());

    // Ready with telemetry enabled rebinds, and a second init is idempotent.
    assert!(init_sentry(true, false, Default::default()));
    assert!(is_bound());
    assert!(init_sentry(true, false, Default::default()));
    assert!(is_bound());

    std::env::remove_var("SENTRY_DSN");
}
