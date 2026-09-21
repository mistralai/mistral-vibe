//! Startup first-frame cache projection and freshness contracts.

use serde_json::Value;

use vibe_rs::server::AgentType;
use vibe_rs::utils::startup_cache::{StartupConfig, MAX_AGE_SECONDS};

const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

fn runtime(safety: &str) -> Value {
    serde_json::json!({
        "runtime": {
            "config": {
                "theme": "ansi-dark",
                "activeModel": {"displayName": "Medium", "thinking": "off"},
                "defaultAgent": "plan",
            },
            "activeAgent": {"name": "ask", "displayName": "Ask", "safety": "neutral"},
            "agents": [
                {"name": "ask", "displayName": "Ask", "safety": "neutral"},
                {"name": "plan", "displayName": "Plan", "safety": safety},
                {"name": "explore", "displayName": "Explore", "agentType": "subagent"},
            ],
        }
    })
}

fn config_with(server_version: &str, cached_at: i64) -> StartupConfig {
    let mut config = StartupConfig::from_runtime(server_version, &runtime("safe")).unwrap();
    config.cached_at = cached_at;
    config
}

#[test]
fn projects_the_default_agent_and_the_cycleable_list() {
    let config = StartupConfig::from_runtime("1.0.0", &runtime("safe")).unwrap();
    assert_eq!(config.default_agent, "plan");
    assert_eq!(config.agents.len(), 3);
    assert_eq!(config.agents[2].agent_type, AgentType::Subagent);
}

#[test]
fn an_older_runtime_without_default_agent_uses_the_builtin_default() {
    let mut runtime = runtime("safe");
    runtime["runtime"]["config"]
        .as_object_mut()
        .unwrap()
        .remove("defaultAgent");
    let config = StartupConfig::from_runtime("1.0.0", &runtime).unwrap();
    assert_eq!(config.default_agent, "accept-edits");
}

#[test]
fn with_no_cache_the_builtins_are_cycleable() {
    let config = StartupConfig::default();
    assert_eq!(config.default_agent, "accept-edits");
    assert_eq!(config.agents.len(), 4);
}

#[test]
fn round_trips_through_the_cache_file_format() {
    let config = StartupConfig::from_runtime("1.0.0", &runtime("destructive")).unwrap();
    let raw = serde_json::to_string(&config).unwrap();
    assert!(raw.contains("\"defaultAgent\":\"plan\""));
    assert!(raw.contains("\"cachedAt\":"));
    assert!(!raw.contains("activeAgent"));
    assert_eq!(serde_json::from_str::<StartupConfig>(&raw).unwrap(), config);
}

#[test]
fn a_same_release_cache_within_the_ttl_is_fresh() {
    let config = config_with(CLIENT_VERSION, 1000);
    assert!(config.is_fresh(CLIENT_VERSION, 1000));
}

#[test]
fn a_cache_from_an_older_release_is_rejected() {
    let config = config_with("2.24.3", 1000);
    assert!(!config.is_fresh("2.25.1", 1000));
}

#[test]
fn a_cache_from_a_newer_release_is_rejected() {
    let config = config_with("2.26.0", 1000);
    assert!(!config.is_fresh("2.25.1", 1000));
}

#[test]
fn patch_level_drift_between_releases_stays_fresh() {
    let config = config_with("2.25.4", 1000);
    assert!(config.is_fresh("2.25.3", 1000));
}

#[test]
fn a_cache_older_than_the_ttl_is_rejected() {
    let now = 1_000_000;
    let config = config_with(CLIENT_VERSION, now - MAX_AGE_SECONDS - 1);
    assert!(!config.is_fresh(CLIENT_VERSION, now));
}

#[test]
fn a_cache_exactly_at_the_ttl_boundary_is_fresh() {
    let now = 1_000_000;
    let config = config_with(CLIENT_VERSION, now - MAX_AGE_SECONDS);
    assert!(config.is_fresh(CLIENT_VERSION, now));
}

#[test]
fn negative_clock_skew_counts_as_fresh() {
    let config = config_with(CLIENT_VERSION, 2000);
    assert!(config.is_fresh(CLIENT_VERSION, 1000));
}

#[test]
fn an_unparsable_cached_version_is_rejected() {
    let config = config_with("stable", 1000);
    assert!(!config.is_fresh(CLIENT_VERSION, 1000));
}

#[test]
fn an_unparsable_client_version_rejects_everything() {
    let config = config_with(CLIENT_VERSION, 1000);
    assert!(!config.is_fresh("stable", 1000));
}

#[test]
fn an_extreme_past_stamp_is_rejected_without_panicking() {
    let config = config_with(CLIENT_VERSION, i64::MIN);
    assert!(!config.is_fresh(CLIENT_VERSION, 1_000_000));
}

#[test]
fn a_stamp_exactly_max_age_ahead_is_fresh() {
    let now = 1_000_000;
    let config = config_with(CLIENT_VERSION, now + MAX_AGE_SECONDS);
    assert!(config.is_fresh(CLIENT_VERSION, now));
}

#[test]
fn an_extreme_future_stamp_is_rejected() {
    let config = config_with(CLIENT_VERSION, i64::MAX);
    assert!(!config.is_fresh(CLIENT_VERSION, 1_000_000));
}

#[test]
fn defaults_are_never_fresh() {
    let config = StartupConfig::default();
    assert_eq!(config.cached_at, 0);
    assert!(!config.is_fresh(CLIENT_VERSION, 1_000_000_000));
}

#[test]
fn from_runtime_stamps_the_write_time() {
    let before = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let config = StartupConfig::from_runtime(CLIENT_VERSION, &runtime("safe")).unwrap();
    let after = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    assert!(config.cached_at >= before && config.cached_at <= after);
    assert!(config.is_fresh(CLIENT_VERSION, after));
}
