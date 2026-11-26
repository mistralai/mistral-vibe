//! The usage gauge's account reads: keyed on the model without its thinking level, stored under the model asked for, and kept through a billing miss.

use std::time::Instant;

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::post_ready::AccountReads;
use vibe_rs::ui::footer_gauge::Usage;
use vibe_rs::usage_gauge::{self, AccountRead};

fn reads(budget: Value) -> AccountReads {
    AccountReads {
        identity: Value::Null,
        account: json!({ "status": "ready", "plan": { "kind": "chat" }, "budget": budget }),
    }
}

fn on(model: &str) -> App {
    let mut app = App::default();
    app.session.startup_config.active_model = model.into();
    app
}

fn forty() -> Option<Usage> {
    Some(Usage {
        percent: 40.0,
        payg: false,
    })
}

#[test]
fn the_account_is_keyed_on_the_model_without_its_thinking_level() {
    let mut app = on("mistral-medium-3.5[medium]");
    assert_eq!(usage_gauge::account_model(&app), "mistral-medium-3.5");
    app.whoami.store(
        "mistral-medium-3.5".into(),
        reads(json!({ "usagePercentage": 40.0 })),
    );

    app.session.startup_config.active_model = "mistral-medium-3.5[high]".into();

    assert_eq!(usage_gauge::footer_usage(&app), forty(), "the gauge stays");
    assert!(app.whoami.asked_for("mistral-medium-3.5"), "no re-read");
}

#[test]
fn an_answer_lands_under_the_model_it_was_asked_for_not_the_one_switched_to() {
    let mut app = on("mistral-medium-3.5[medium]");
    app.whoami.ask("mistral-medium-3.5".into());
    app.session.startup_config.active_model = "local-model[off]".into();

    app.whoami.store(
        "mistral-medium-3.5".into(),
        reads(json!({ "usagePercentage": 40.0 })),
    );

    assert_eq!(usage_gauge::footer_usage(&app), None);
    assert!(app.whoami.hit("local-model").is_none());
}

#[test]
fn an_answer_for_a_superseded_ask_is_dropped() {
    let mut app = on("local-model[off]");
    app.whoami.ask("mistral-medium-3.5".into());
    app.whoami.ask("local-model".into());

    app.whoami.store(
        "mistral-medium-3.5".into(),
        reads(json!({ "usagePercentage": 40.0 })),
    );

    assert!(app.whoami.hit("mistral-medium-3.5").is_none());
}

#[test]
fn a_re_read_without_a_budget_keeps_the_last_known_usage() {
    let mut app = on("mistral-medium-3.5[medium]");
    app.whoami.store(
        "mistral-medium-3.5".into(),
        reads(json!({ "usagePercentage": 40.0 })),
    );

    usage_gauge::apply(
        &mut app,
        AccountRead {
            model: "mistral-medium-3.5".into(),
            reads: Some(reads(Value::Null)),
        },
    );

    assert_eq!(usage_gauge::footer_usage(&app), forty());
}

#[test]
fn a_re_read_with_a_budget_replaces_it() {
    let mut app = on("mistral-medium-3.5[medium]");
    app.whoami.store(
        "mistral-medium-3.5".into(),
        reads(json!({ "usagePercentage": 10.0 })),
    );

    usage_gauge::apply(
        &mut app,
        AccountRead {
            model: "mistral-medium-3.5".into(),
            reads: Some(reads(json!({ "usagePercentage": 40.0 }))),
        },
    );

    assert_eq!(usage_gauge::footer_usage(&app), forty());
}

#[test]
fn a_failed_read_for_a_new_model_is_asked_again_after_a_short_delay() {
    let mut app = on("mistral-medium-3.5[medium]");
    app.whoami.store(
        "mistral-medium-3.5".into(),
        reads(json!({ "usagePercentage": 40.0 })),
    );
    app.session.startup_config.active_model = "mistral-large-3[medium]".into();
    app.whoami
        .mark_read(Instant::now(), "mistral-large-3".into());

    usage_gauge::apply(
        &mut app,
        AccountRead {
            model: "mistral-large-3".into(),
            reads: None,
        },
    );

    assert!(
        !app.whoami.asked_for("mistral-large-3"),
        "the next frame may ask again"
    );
    assert!(app.whoami.failed_at().is_some(), "after a short delay");
    assert!(
        app.whoami.hit("mistral-medium-3.5").is_some(),
        "the last reads stay"
    );
}

#[test]
fn a_failed_re_read_of_a_read_model_keeps_its_ask() {
    let mut app = on("mistral-medium-3.5[medium]");
    app.whoami.store(
        "mistral-medium-3.5".into(),
        reads(json!({ "usagePercentage": 40.0 })),
    );

    usage_gauge::apply(
        &mut app,
        AccountRead {
            model: "mistral-medium-3.5".into(),
            reads: None,
        },
    );

    assert!(app.whoami.asked_for("mistral-medium-3.5"));
    assert_eq!(usage_gauge::footer_usage(&app), forty());
}
