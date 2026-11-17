//! The `vibe_storage_usage` flag: `/storage` is offered, and runs when typed, only while the runtime config turns it on.

use serde_json::json;
use vibe_rs::commands::{builtin, entries, parse, runs, Feature};

#[test]
fn storage_is_hidden_and_typed_as_a_prompt_until_the_flag_is_on() {
    let listed = |on: bool| {
        let feature = move |f: Feature| f != Feature::StorageUsage || on;
        entries(&[], feature)
            .iter()
            .any(|entry| entry.label == "/storage")
            || builtin(feature)
                .iter()
                .any(|(label, _)| label.contains("/storage"))
    };
    assert_eq!(parse("/storage"), Some("/storage"));
    assert!(!listed(false));
    assert!(!runs("/storage", |f| f != Feature::StorageUsage));
    assert!(listed(true));
    assert!(runs("/storage", |_| true));
    assert!(runs("/pr", |_| false));
}

#[test]
fn the_flag_reads_the_runtime_config_and_defaults_off() {
    let mut app = vibe_rs::app::App::default();
    let off = json!({"runtime": {"config": {}}});
    vibe_rs::terminal_notifier::configure(&mut app, &off);
    assert!(!Feature::StorageUsage.is_on(&app));
    let on = json!({"runtime": {"config": {"experimentalEnableStorageUsage": true}}});
    vibe_rs::terminal_notifier::configure(&mut app, &on);
    assert!(Feature::StorageUsage.is_on(&app));
}
