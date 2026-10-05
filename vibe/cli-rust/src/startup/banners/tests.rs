use super::*;
use serde_json::json;

#[test]
fn custom_tool_names_pick_is_custom_entries() {
    let runtime = json!({"runtime": {"tools": [
        {"name": "skill", "isCustom": false},
        {"name": "legacy_lint", "isCustom": true},
        {"name": "anon_custom"},
    ]}});
    assert_eq!(custom_tool_names(&runtime), vec!["legacy_lint".to_owned()]);
    assert_eq!(custom_tool_names(&json!({})), Vec::<String>::new());
}

#[test]
fn untrusted_warning_names_every_ignored_folder() {
    let dirs = vec![
        "/home/user/repo/.vibe".to_owned(),
        "/home/user/other/.vibe".to_owned(),
    ];
    let text = warning_text(&dirs, "/home/user/.vibe/trusted_folders.toml");
    assert!(text.starts_with("⚠ Untrusted local config folders are being ignored:\n\n"));
    assert!(text.contains("\n\n  • /home/user/repo/.vibe\n  • /home/user/other/.vibe\n\n"));
    assert!(text.ends_with(
            "If you want them loaded, remove them from \"untrusted\" in /home/user/.vibe/trusted_folders.toml, or ask Vibe to do it."
        ));
}

#[test]
fn an_empty_dir_list_never_warns() {
    assert_eq!(
        untrusted_warning(&json!({"dirs": [], "settingsPath": "p"})),
        None
    );
}

#[test]
fn whats_new_body_env_override_mirrors_python_none_cases() {
    let dir = std::env::temp_dir().join(format!("vibe-rs-whatsnew-body-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let fixture = dir.join("whats_new.fixture");

    std::fs::write(&fixture, "  # What's new in v9.9.9-fixture  \n").unwrap();
    std::env::set_var(WHATS_NEW_FILE_ENV, &fixture);
    assert_eq!(
        whats_new_body().as_deref(),
        Some("# What's new in v9.9.9-fixture")
    );

    std::fs::write(&fixture, "   \n\t  \n").unwrap();
    assert_eq!(whats_new_body(), None);

    std::env::set_var(WHATS_NEW_FILE_ENV, dir.join("missing.fixture"));
    assert_eq!(whats_new_body(), None);

    std::env::remove_var(WHATS_NEW_FILE_ENV);
    assert_eq!(
        whats_new_body().as_deref(),
        non_empty(WHATS_NEW_MD).as_deref()
    );

    let _ = std::fs::remove_dir_all(&dir);
}
