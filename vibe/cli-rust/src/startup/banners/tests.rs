use super::*;
use serde_json::json;

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

/// Store `account` as the active model's read, then sync the plan offer to it.
fn store_active_read(app: &mut App, account: &Value) {
    app.session.startup_config.active_model = "model".to_owned();
    app.whoami.store(
        "model".to_owned(),
        crate::post_ready::AccountReads {
            identity: Value::Null,
            account: account.clone(),
        },
    );
    sync_plan_offer(app);
}

fn whats_new_app(body: &str, promo: bool) -> App {
    let whats_new = WhatsNew {
        id: "whats-new".to_owned(),
        body: body.to_owned(),
        promo,
        offer_id: None,
    };
    let mut app = App::default();
    local::add_whats_new(&mut app.view.transcript, &whats_new.id, &whats_new.text());
    app.session.whats_new = Some(whats_new);
    app
}

fn entry_text<'a>(app: &'a App, id: &str) -> &'a str {
    app.view
        .transcript
        .entry_raw(id)
        .and_then(|entry| entry.pointer("/content/0/text"))
        .and_then(Value::as_str)
        .unwrap()
}

fn whats_new_text(app: &App) -> &str {
    entry_text(app, "whats-new")
}

/// The upgrade box's text, if mounted.
fn upgrade_box(app: &App) -> Option<&str> {
    let id = app.session.whats_new.as_ref()?.offer_id.as_deref()?;
    Some(entry_text(app, id))
}

fn offer(kind: &str) -> Value {
    json!({"planOffer": {"kind": kind, "url": "https://example.test/key"}})
}

const UPGRADE: &str = "Unlock more with Vibe - [Upgrade to Vibe Pro](https://example.test/key)";

#[test]
fn an_upgrade_offer_gets_its_own_box_under_whats_new() {
    let mut app = whats_new_app("# New", true);
    let body = format!("# New{}", promo_whats_new_suffix());

    store_active_read(&mut app, &offer("upgrade_to_pro"));

    assert_eq!(whats_new_text(&app), body);
    assert_eq!(upgrade_box(&app), Some(UPGRADE));
    let ids: Vec<_> = app.view.transcript.lines().map(|entry| entry.id).collect();
    assert_eq!(ids.len(), 2);
    assert_eq!(ids[0], "whats-new");
}

#[test]
fn dismissing_whats_new_drops_the_upgrade_box() {
    let mut app = whats_new_app("# New", false);
    store_active_read(&mut app, &offer("upgrade_to_pro"));

    dismiss_whats_new(&mut app);

    assert_eq!(app.view.transcript.entry_count(), 0);
}

#[test]
fn a_switch_key_offer_never_goes_under_whats_new() {
    let mut app = whats_new_app("# New", false);

    store_active_read(&mut app, &offer("switch_api_key"));

    assert_eq!(whats_new_text(&app), "# New");
    assert_eq!(upgrade_box(&app), None);
    assert_eq!(app.view.transcript.entry_count(), 1);
}

/// An app whose account read for the active model holds `account`.
fn read_app(account: Value) -> App {
    let mut app = App::default();
    store_active_read(&mut app, &account);
    app
}

#[test]
fn the_banner_shows_the_active_reads_switch_offer() {
    let app = read_app(offer("switch_api_key"));

    assert_eq!(
        switch_key_offer(&app).as_deref(),
        Some(
            "You have Vibe Pro, but this API key does not use it.\n\n\
             To use your plan, quit and run `vibe --setup`, then choose **Launch browser** \
             and sign in. Or copy your [Vibe Pro API key](https://example.test/key), \
             choose **Use an API key** and paste it."
        )
    );
}

#[test]
fn the_banner_drops_the_switch_offer_once_the_model_or_read_changes() {
    let mut app = read_app(offer("switch_api_key"));
    app.session.startup_config.active_model = "other[high]".to_owned();
    assert_eq!(switch_key_offer(&app), None);

    let app = read_app(json!({"status": "unavailable", "planOffer": null}));
    assert_eq!(switch_key_offer(&app), None);

    let app = read_app(offer("upgrade_to_pro"));
    assert_eq!(switch_key_offer(&app), None);
}

#[test]
fn a_later_read_replaces_or_drops_the_upgrade_box() {
    let mut app = whats_new_app("# New", false);

    store_active_read(&mut app, &offer("upgrade_to_pro"));
    store_active_read(&mut app, &offer("upgrade_to_pro"));
    assert_eq!(app.view.transcript.entry_count(), 2);
    assert_eq!(upgrade_box(&app), Some(UPGRADE));

    store_active_read(&mut app, &json!({"planOffer": null}));
    assert_eq!(upgrade_box(&app), None);
    assert_eq!(app.view.transcript.entry_count(), 1);
    assert_eq!(whats_new_text(&app), "# New");
}

#[test]
fn the_upgrade_box_follows_the_active_model() {
    let mut app = whats_new_app("# New", false);
    store_active_read(&mut app, &offer("upgrade_to_pro"));
    assert_eq!(upgrade_box(&app), Some(UPGRADE));

    app.session.startup_config.active_model = "other[high]".to_owned();
    crate::usage_gauge::apply(
        &mut app,
        crate::usage_gauge::AccountRead {
            model: "other".to_owned(),
            reads: None,
        },
    );

    assert_eq!(upgrade_box(&app), None);
    assert_eq!(app.view.transcript.entry_count(), 1);
}

#[test]
fn plan_offer_needs_a_url_and_defaults_to_upgrade() {
    assert!(plan_offer(&json!({"planOffer": {"kind": "switch_api_key"}})).is_none());
    let offer = plan_offer(&json!({"planOffer": {"kind": "future_kind", "url": "u"}})).unwrap();
    assert!(!offer.switch_key);
    assert_eq!(
        offer.text,
        "Unlock more with Vibe - [Upgrade to Vibe Pro](u)"
    );
}

#[test]
fn no_offer_leaves_the_transcript_alone() {
    let mut app = whats_new_app("# New", false);
    store_active_read(&mut app, &json!({"planOffer": null}));
    assert_eq!(app.view.transcript.entry_count(), 1);

    let mut app = App::default();
    store_active_read(&mut app, &offer("upgrade_to_pro"));
    assert_eq!(app.view.transcript.entry_count(), 0);
}
