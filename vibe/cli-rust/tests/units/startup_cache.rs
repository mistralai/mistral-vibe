//! Startup first-frame cache projection and freshness contracts.

use serde_json::Value;

use vibe_rs::server::{AgentSafety, AgentType};
use vibe_rs::utils::startup_cache::{StartupConfig, MAX_AGE_SECONDS};

const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

fn runtime(safety: &str) -> Value {
    serde_json::json!({
        "runtime": {
            "config": {
                "theme": "ansi-dark",
                "activeModel": {"displayName": "Medium", "thinking": "off"},
                "defaultAgent": "lean",
                "defaultApprovalPolicy": "audit",
            },
            "activeAgent": {"name": "default", "displayName": "Default"},
            "agents": [
                {"name": "default", "displayName": "Default"},
                {"name": "lean", "displayName": "Lean"},
                {"name": "explore", "displayName": "Explore", "agentType": "subagent"},
            ],
            "activeApprovalPolicy": {"name": "ask", "displayName": "Ask", "safety": "neutral"},
            "approvalPolicies": [
                {"name": "ask", "displayName": "Ask", "safety": "neutral"},
                {"name": "audit", "displayName": "Audit", "safety": safety},
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
fn projects_the_defaults_the_agents_and_the_cycleable_policies() {
    let config = StartupConfig::from_runtime("1.0.0", &runtime("safe")).unwrap();
    assert_eq!(config.default_agent, "lean");
    assert_eq!(config.agents.len(), 3);
    assert_eq!(config.agents[2].agent_type, AgentType::Subagent);
    assert_eq!(config.default_approval_policy, "audit");
    assert_eq!(config.approval_policies.len(), 2);
    assert_eq!(config.approval_policies[1].safety, AgentSafety::Safe);
}

#[test]
fn an_older_runtime_without_defaults_uses_the_builtin_defaults() {
    let mut runtime = runtime("safe");
    let config = runtime["runtime"]["config"].as_object_mut().unwrap();
    config.remove("defaultAgent");
    config.remove("defaultApprovalPolicy");
    let config = StartupConfig::from_runtime("1.0.0", &runtime).unwrap();
    assert_eq!(config.default_agent, "default");
    assert_eq!(config.default_approval_policy, "accept-edits");
}

#[test]
fn image_support_follows_the_backend_promise_with_a_model_fallback() {
    let mut runtime = runtime("safe");
    let config = &mut runtime["runtime"]["config"];
    config["activeModel"]["supportsImages"] = false.into();
    config["imagesSupported"] = true.into();
    assert!(
        StartupConfig::from_runtime("1.0.0", &runtime)
            .unwrap()
            .images_supported
    );

    let config = &mut runtime["runtime"]["config"];
    config.as_object_mut().unwrap().remove("imagesSupported");
    config["activeModel"]["supportsImages"] = true.into();
    assert!(
        StartupConfig::from_runtime("1.0.0", &runtime)
            .unwrap()
            .images_supported
    );
}

#[test]
fn with_no_cache_the_builtins_are_cycleable() {
    let config = StartupConfig::default();
    assert_eq!(config.default_agent, "default");
    assert_eq!(config.agents.len(), 1);
    assert_eq!(config.default_approval_policy, "accept-edits");
    assert_eq!(config.approval_policies.len(), 4);
    assert_eq!(config.approval_policies[2].name, "smart-approve");
    assert_eq!(config.approval_policies[2].safety, AgentSafety::Smart);
}

#[test]
fn round_trips_through_the_cache_file_format() {
    let config = StartupConfig::from_runtime("1.0.0", &runtime("destructive")).unwrap();
    let raw = serde_json::to_string(&config).unwrap();
    assert!(raw.contains("\"defaultApprovalPolicy\":\"audit\""));
    assert!(raw.contains("\"cachedAt\":"));
    assert!(!raw.contains("activeAgent"));
    assert_eq!(serde_json::from_str::<StartupConfig>(&raw).unwrap(), config);
}

#[test]
fn projects_mouse_capture_from_the_runtime_config() {
    assert!(config_with(CLIENT_VERSION, 1000).enable_mouse_capture);
    let mut disabled = runtime("safe");
    disabled["runtime"]["config"]["enableMouseCapture"] = false.into();
    let config = StartupConfig::from_runtime("1.0.0", &disabled).unwrap();
    assert!(!config.enable_mouse_capture);
}

#[test]
fn an_older_cache_without_mouse_capture_keeps_capturing() {
    let mut cache = serde_json::to_value(config_with(CLIENT_VERSION, 1000)).unwrap();
    cache.as_object_mut().unwrap().remove("enableMouseCapture");
    let config: StartupConfig = serde_json::from_value(cache).unwrap();
    assert!(config.enable_mouse_capture);
}

#[test]
fn an_older_cache_without_cursor_blink_keeps_blinking_enabled() {
    let mut cache = serde_json::to_value(config_with(CLIENT_VERSION, 1000)).unwrap();
    cache.as_object_mut().unwrap().remove("cursorBlink");
    let config: StartupConfig = serde_json::from_value(cache).unwrap();
    assert!(config.cursor_blink);
    assert!(config.is_fresh(CLIENT_VERSION, 1000));
}

#[test]
fn the_selection_animation_flag_is_cached_and_older_caches_keep_it_on() {
    let mut runtime = runtime("safe");
    runtime["runtime"]["config"]["disableSelectionAnimation"] = true.into();
    let config = StartupConfig::from_runtime(CLIENT_VERSION, &runtime).unwrap();
    assert!(config.disable_selection_animation);
    let mut cache = serde_json::to_value(config).unwrap();
    cache
        .as_object_mut()
        .unwrap()
        .remove("disableSelectionAnimation");
    let config: StartupConfig = serde_json::from_value(cache).unwrap();
    assert!(!config.disable_selection_animation);
}

#[test]
fn a_cache_from_an_older_release_is_rejected() {
    let config = config_with("2.24.3", 1000);
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

#[test]
fn file_autocomplete_defaults_to_enabled_when_the_runtime_omits_it() {
    let config = StartupConfig::from_runtime("1.0.0", &runtime("safe")).unwrap();
    assert!(config.enable_file_autocomplete);
    assert!(config.file_watcher_for_autocomplete);
}

#[test]
fn dangerous_directory_warning_follows_the_runtime() {
    let mut runtime = runtime("safe");
    let shown = StartupConfig::from_runtime("1.0.0", &runtime).unwrap();
    assert!(shown.show_dangerous_directory_warning);
    runtime["runtime"]["config"]["showDangerousDirectoryWarning"] = false.into();
    let hidden = StartupConfig::from_runtime("1.0.0", &runtime).unwrap();
    assert!(!hidden.show_dangerous_directory_warning);
}

#[test]
fn runtime_flags_project_into_the_startup_config() {
    let mut runtime = runtime("safe");
    let config = &mut runtime["runtime"]["config"];
    config["enableFileAutocomplete"] = false.into();
    config["fileWatcherForAutocomplete"] = false.into();
    let config = StartupConfig::from_runtime("1.0.0", &runtime).unwrap();
    assert!(!config.enable_file_autocomplete);
    assert!(!config.file_watcher_for_autocomplete);
}
