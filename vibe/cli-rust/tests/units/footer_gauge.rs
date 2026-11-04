//! The footer's context and usage gauges: fill steps, their track, PAYG, how they narrow, and when the usage shows.

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::post_ready::AccountReads;
use vibe_rs::ui::footer_gauge::{bar, level, Usage};
use vibe_rs::ui::theme;
use vibe_rs::usage_gauge;

use crate::support::{bottom_bar, bottom_bar_row, column, row};

fn app(usage: Value) -> App {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.startup_config.active_model = "mistral-medium-3.5".into();
    app.session.tokens = (23_000, 600_000);
    let account = json!({ "status": "ready", "budget": usage });
    let model = app.session.startup_config.active_model.clone();
    let identity = Value::Null;
    app.whoami.store(model, AccountReads { identity, account });
    app
}

#[test]
fn a_bar_fills_by_eighths_lights_any_usage_and_is_full_only_when_spent() {
    assert_eq!(bar(0.0, 6).0, "      ");
    assert_eq!(bar(0.1, 6).0, "▏     ");
    assert_eq!(bar(40.0, 6).0, "██▍   ");
    assert_eq!(bar(50.0, 6).0, "███   ");
    assert_eq!(bar(99.6, 6).0, "█████▉", "not spent yet");
    assert_eq!(bar(100.0, 6).0, "██████");
    assert_eq!(bar(250.0, 6).0, "██████", "past the allowance stays full");
    assert_eq!(bar(50.0, 0).0, "", "no track, no panic");
}

#[test]
fn a_level_rises_from_the_bottom_and_is_empty_only_at_zero() {
    let levels: Vec<String> = [0.0, 1.0, 25.0, 50.0, 75.0, 99.0, 100.0]
        .into_iter()
        .map(|percent| level(percent).0)
        .collect();
    assert_eq!(levels, [" ", "▁", "▂", "▄", "▆", "▇", "█"]);
}

#[test]
fn the_usage_reads_from_the_account_budget() {
    let account = |budget: Value| json!({ "budget": budget });
    let usage = |percent, payg| Some(Usage { percent, payg });

    assert_eq!(usage_gauge::usage(&json!({ "status": "ready" })), None);
    assert_eq!(usage_gauge::usage(&account(Value::Null)), None);
    let partial = account(json!({ "usagePercentage": 40.0 }));
    assert_eq!(usage_gauge::usage(&partial), usage(40.0, false));
    let spent = account(json!({ "usagePercentage": 100, "paygEnabled": true }));
    assert_eq!(usage_gauge::usage(&spent), usage(100.0, true));
}

#[test]
fn another_model_hides_the_usage_read_for_the_previous_credential() {
    let mut app = app(json!({ "usagePercentage": 40.0 }));
    app.session.startup_config.active_model = "local-model".into();

    assert_eq!(usage_gauge::footer_usage(&app), None);
    assert!(!bottom_bar_row(&mut app, 120).contains("usage"));
}

#[test]
fn a_wide_row_shows_the_usage_then_the_context() {
    let mut app = app(json!({ "usagePercentage": 40.0 }));

    let row = bottom_bar_row(&mut app, 120);

    let tail = "   usage ██▍    40% · ctx ▎      23k/600k";
    assert!(row.ends_with(tail), "{row:?}");
}

#[test]
fn without_a_budget_no_usage_gauge_is_painted() {
    let mut app = app(Value::Null);

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.ends_with("   ctx ▎      23k/600k"), "{row:?}");
    assert!(!row.contains("usage"));
}

#[test]
fn pay_as_you_go_reads_by_the_gauge_and_a_spent_allowance_reads_full() {
    let mut payg = app(json!({ "usagePercentage": 40.0, "paygEnabled": true }));
    let mut billed = app(json!({ "usagePercentage": 104.0, "paygEnabled": true }));
    let mut spent = app(json!({ "usagePercentage": 100.0, "paygEnabled": false }));
    let mut nearly = app(json!({ "usagePercentage": 99.7, "paygEnabled": false }));
    let usage = |app: &mut App| {
        let row = bottom_bar_row(app, 120);
        row[row.find("usage").expect("usage")..row.find(" · ctx").expect("ctx")].to_owned()
    };

    assert_eq!(usage(&mut payg), "usage ██▍    40% · PAYG");
    assert_eq!(usage(&mut billed), "usage ██████ 104% · PAYG");
    assert_eq!(usage(&mut spent), "usage ██████ 100%");
    assert_eq!(usage(&mut nearly), "usage █████▉ 99%");
    assert!(!bottom_bar_row(&mut spent, 50).contains("paused"));
}

#[test]
fn narrowing_turns_bars_into_levels_then_drops_their_numbers() {
    let mut app = app(json!({ "usagePercentage": 40.0 }));
    let mut row = |width| bottom_bar_row(&mut app, width).trim_end().to_owned();

    let wide = "/tmp/project ⧉ usage ██▍    40% · ctx ▎      23k/600k";
    let width = wide.chars().count() as u16;
    assert_eq!(row(width), wide);
    // The gauges give way first, context then usage; the cwd keeps its path.
    assert_eq!(
        row(width - 1),
        "/tmp/project ⧉           usage ██▍    40% · ctx ▁ 4%"
    );
    assert_eq!(row(40), "/tmp/project ⧉    usage ▃ 40% · ctx ▁ 4%");
    assert_eq!(row(35), "…/project ⧉  usage ▃ 40% · ctx ▁ 4%");
    assert_eq!(row(30), "/tmp/project ⧉ usage ▃ · ctx ▁");
}

/// The usage fill's color at `percent`, with or without pay-as-you-go.
fn usage_fill(percent: f64, payg: bool) -> Option<ratatui::style::Color> {
    let mut app = app(json!({ "usagePercentage": percent, "paygEnabled": payg }));
    let buffer = bottom_bar(&mut app, 120);
    let at = column(&row(&buffer, 0), "usage ") + "usage ".len() as u16;
    buffer[(at, 0)].style().fg
}

#[test]
fn without_pay_as_you_go_the_usage_fill_warns_then_alarms() {
    assert_eq!(usage_fill(74.0, false), Some(theme::gauge_fill()));
    assert_eq!(usage_fill(75.0, false), Some(theme::warning()));
    assert_eq!(usage_fill(90.0, false), Some(theme::error()));
    assert_eq!(usage_fill(100.0, false), Some(theme::error()));
}

#[test]
fn with_pay_as_you_go_the_usage_fill_keeps_its_color() {
    assert_eq!(usage_fill(95.0, true), Some(theme::gauge_fill()));
    assert_eq!(usage_fill(120.0, true), Some(theme::gauge_fill()));
}

#[test]
fn the_fills_paint_on_their_track_and_the_labels_stay_muted() {
    let mut app = app(json!({ "usagePercentage": 95.0 }));
    let buffer = bottom_bar(&mut app, 120);
    let row = row(&buffer, 0);
    let at = |text: &str| buffer[(column(&row, text), 0)].style();
    let usage = column(&row, "usage ") + "usage ".len() as u16;
    let ctx = column(&row, "ctx ") + "ctx ".len() as u16;
    let style = |x: u16| buffer[(x, 0)].style();

    assert_eq!(style(usage).bg, Some(theme::gauge_track()));
    assert_eq!(
        style(ctx).fg,
        Some(theme::gauge_fill()),
        "the context never warns"
    );
    assert_eq!(style(ctx + 5).bg, Some(theme::gauge_track()), "the track");
    assert_eq!(at("usage"), at("/tmp/project"));
    assert_eq!(at("95%"), at("/tmp/project"));
}
