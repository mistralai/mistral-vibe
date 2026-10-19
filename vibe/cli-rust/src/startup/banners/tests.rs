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
fn whats_new_body_env_override_none_cases() {
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

fn whats_new_app(body: &str, promo: bool) -> App {
    let whats_new = WhatsNew {
        id: "whats-new".to_owned(),
        body: body.to_owned(),
        promo,
    };
    let mut app = App::default();
    local::add_whats_new(
        &mut app.view.transcript,
        &whats_new.id,
        &whats_new.text(None),
        false,
    );
    app.session.whats_new = Some(whats_new);
    app
}

fn whats_new_text(app: &App) -> &str {
    app.view
        .transcript
        .entry_raw("whats-new")
        .and_then(|entry| entry.pointer("/content/0/text"))
        .and_then(Value::as_str)
        .unwrap()
}

fn offer(kind: &str) -> Value {
    json!({"planOffer": {"kind": kind, "url": "https://example.test/key"}})
}

#[test]
fn plan_offer_goes_above_the_promo_suffix() {
    let mut app = whats_new_app("# New", true);
    assert_eq!(
        whats_new_text(&app),
        format!("# New{}", promo_whats_new_suffix())
    );

    show_plan_offer(&mut app, &offer("switch_api_key"));

    assert_eq!(
        whats_new_text(&app),
        format!(
            "# New\n\n### Switch to your [Vibe Pro API key](https://example.test/key){}",
            promo_whats_new_suffix()
        )
    );
}

#[test]
fn a_later_offer_replaces_the_earlier_one() {
    let mut app = whats_new_app("# New", false);

    show_plan_offer(&mut app, &offer("switch_api_key"));
    show_plan_offer(&mut app, &offer("upgrade_to_pro"));

    assert_eq!(
        whats_new_text(&app),
        "# New\n\n### Unlock more with Vibe - [Upgrade to Vibe Pro](https://example.test/key)"
    );
}

#[test]
fn plan_offer_cta_needs_a_url_and_defaults_to_upgrade() {
    assert_eq!(
        plan_offer_cta(&json!({"planOffer": {"kind": "switch_api_key"}})),
        None
    );
    assert_eq!(
        plan_offer_cta(&json!({"planOffer": {"kind": "future_kind", "url": "u"}})).as_deref(),
        Some("### Unlock more with Vibe - [Upgrade to Vibe Pro](u)")
    );
}

#[test]
fn no_offer_or_no_whats_new_leaves_the_transcript_alone() {
    let mut app = whats_new_app("# New", false);
    show_plan_offer(&mut app, &json!({"planOffer": null}));
    assert_eq!(whats_new_text(&app), "# New");

    let mut app = App::default();
    show_plan_offer(&mut app, &offer("upgrade_to_pro"));
    assert_eq!(app.view.transcript.entry_count(), 0);
}
