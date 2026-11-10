//! The app-server child gets this binary's DSN, so one launch reports to one Sentry project.

use crate::onboarding_common::fake_server;

use serde_json::json;
use vibe_rs::observability::sentry::CRASH_REPORT_DSN_ENV;

#[test]
#[ignore]
fn server_echoes_its_crash_report_dsn() {
    fake_server::serve_scripted(|_method, _msg| {
        Ok(json!({"dsn": std::env::var(CRASH_REPORT_DSN_ENV).ok()}))
    });
}

#[tokio::test]
async fn the_app_server_reports_with_the_cli_dsn() {
    std::env::set_var("SENTRY_DSN", "https://key@sentry.example/42");
    let (client, _child) =
        fake_server::spawn(&test_name!("server_echoes_its_crash_report_dsn")).await;

    let answer = client.request("test/env", json!({})).await.unwrap();

    assert_eq!(answer["dsn"], "https://key@sentry.example/42");
}
