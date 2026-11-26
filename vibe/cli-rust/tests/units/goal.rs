//! `/goal` formatting, budget parsing, patch tracking, notification gating and error notices.

use serde_json::json;
use vibe_rs::app::{App, Severity};
use vibe_rs::goal::{self, format};
use vibe_rs::server::{GoalSetParams, GoalStatus, PublicGoal, RpcError};

fn goal_json(status: &str, reason: Option<&str>) -> serde_json::Value {
    json!({
        "goalId": "goal-1", "revision": 3, "objectiveRevision": 1,
        "objective": "Ship it", "status": status, "statusReason": reason,
        "tokenBudget": 50000, "tokensUsed": 12500, "timeUsedSeconds": 240,
        "createdAt": 1, "updatedAt": 2,
    })
}

fn goal(status: &str) -> PublicGoal {
    serde_json::from_value(goal_json(status, None)).unwrap()
}

fn patched(app: &mut App, op: serde_json::Value) {
    goal::track_patch(app, &json!({"sessionId": "s", "patch": [op]}));
}

#[test]
fn tokens_and_durations_are_compact() {
    assert_eq!(format::tokens(950), "950");
    assert_eq!(format::tokens(12_500), "12.5K");
    assert_eq!(format::tokens(1_200_000), "1.2M");
    assert_eq!(format::duration(45), "45s");
    assert_eq!(format::duration(240), "4m");
    assert_eq!(format::duration(3900), "1h05m");
}

#[test]
fn the_row_detail_follows_the_status() {
    assert_eq!(format::row_detail(&goal("active")), "12.5K / 50K · 4m");
    let mut unbounded = goal("active");
    unbounded.token_budget = None;
    assert_eq!(format::row_detail(&unbounded), "12.5K · 4m");
    assert_eq!(format::row_detail(&goal("paused")), "paused");
    assert_eq!(format::row_detail(&goal("blocked")), "blocked");
    assert_eq!(
        format::row_detail(&goal("budget_limited")),
        "budget reached"
    );
    assert_eq!(format::row_detail(&goal("usage_limited")), "usage limit");
    assert_eq!(
        format::row_detail(&goal("complete")),
        "complete · 12.5K · 4m"
    );
}

#[test]
fn budgets_parse_with_suffixes_and_round_trip() {
    assert_eq!(format::parse_budget(""), Ok(None));
    assert_eq!(format::parse_budget("200k"), Ok(Some(200_000)));
    assert_eq!(format::parse_budget("1.5M"), Ok(Some(1_500_000)));
    assert_eq!(format::parse_budget(" 50000 "), Ok(Some(50_000)));
    for invalid in ["0", "abc", "1.5", "k", "-5", "1e3"] {
        assert!(format::parse_budget(invalid).is_err(), "{invalid}");
    }
    for budget in [200_000, 1_500_000, 2_000_000, 12_345] {
        let text = format::budget_text(Some(budget));
        assert_eq!(format::parse_budget(&text), Ok(Some(budget)), "{text}");
    }
}

#[test]
fn an_unknown_status_or_malformed_goal_never_fails_the_session() {
    let goal: PublicGoal = serde_json::from_value(goal_json("future", None)).unwrap();
    assert_eq!(goal.status, GoalStatus::Unknown);
    let session: vibe_rs::server::PublicSession =
        serde_json::from_value(json!({"id": "s", "goal": {"goalId": 1}})).unwrap();
    assert!(session.goal.is_none());
}

#[test]
fn edits_send_the_budget_and_omit_it_otherwise() {
    let edit = GoalSetParams {
        session_id: "s".into(),
        expected_revision: Some(3),
        token_budget: Some(None),
        ..Default::default()
    };
    let value = serde_json::to_value(edit).unwrap();
    assert_eq!(
        value,
        json!({"sessionId": "s", "expectedRevision": 3, "tokenBudget": null})
    );
    let pause = GoalSetParams {
        session_id: "s".into(),
        status: Some("paused"),
        ..Default::default()
    };
    let value = serde_json::to_value(pause).unwrap();
    assert_eq!(value, json!({"sessionId": "s", "status": "paused"}));
}

#[test]
fn session_patches_set_replace_and_clear_the_goal() {
    let mut app = App::default();
    app.session.session_id = Some("s".into());
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("active", None)}),
    );
    assert_eq!(goal::status(&app), Some(GoalStatus::Active));
    patched(
        &mut app,
        json!({"op": "replace", "path": "/goal/status", "value": "paused"}),
    );
    assert_eq!(goal::status(&app), Some(GoalStatus::Paused));
    patched(&mut app, json!({"op": "remove", "path": "/goal"}));
    assert_eq!(goal::status(&app), None);
}

#[test]
fn another_sessions_patch_is_ignored() {
    let mut app = App::default();
    app.session.session_id = Some("mine".into());
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("active", None)}),
    );
    assert_eq!(goal::status(&app), None);
}

#[test]
fn an_active_goal_silences_the_turn_end_and_a_stop_notifies_once() {
    let mut app = App::default();
    app.session.session_id = Some("s".into());
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("active", None)}),
    );
    assert!(goal::quiet_turn_end(&app));
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("complete", None)}),
    );
    assert!(
        goal::quiet_turn_end(&app),
        "the goal notification replaces the turn's"
    );
    goal::turn_started(&mut app, Some("t-1".into()), false);
    assert!(!goal::quiet_turn_end(&app));
}

#[test]
fn a_user_prompt_during_an_active_goal_still_notifies_its_end() {
    let mut app = App::default();
    app.session.session_id = Some("s".into());
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("active", None)}),
    );

    goal::turn_started(&mut app, Some("t-1".into()), true);
    assert!(
        !goal::quiet_turn_end(&app),
        "the user's own answer notifies"
    );

    goal::turn_started(&mut app, Some("t-2".into()), false);
    assert!(
        goal::quiet_turn_end(&app),
        "a hidden continuation stays silent"
    );
}

#[test]
fn a_snapshot_resets_who_the_running_turn_belongs_to() {
    let mut app = App::default();
    app.session.session_id = Some("s".into());
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("active", None)}),
    );
    goal::turn_started(&mut app, Some("t-1".into()), true);

    let continuation = [json!({"id": "t-2", "status": "in_progress"})];
    goal::sync_running_turn(&mut app, "s", Some(&continuation));
    assert!(
        goal::quiet_turn_end(&app),
        "a snapshot's hidden continuation stays silent"
    );

    let prompt = [json!({"id": "t-3", "status": "in_progress", "queueItemId": "q-1"})];
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("complete", None)}),
    );
    assert!(
        goal::quiet_turn_end(&app),
        "the previous turn's goal notified"
    );
    patched(
        &mut app,
        json!({"op": "add", "path": "/goal", "value": goal_json("active", None)}),
    );
    goal::sync_running_turn(&mut app, "s", Some(&prompt));
    assert!(
        !goal::quiet_turn_end(&app),
        "a snapshot's user prompt notifies"
    );
}

#[test]
fn only_an_attach_offers_to_resume_a_suspended_goal() {
    let mut app = App::default();
    let suspended: PublicGoal =
        serde_json::from_value(goal_json("paused", Some("suspended"))).unwrap();
    goal::adopt(&mut app, Some(Box::new(suspended.clone())), false);
    assert!(!app.goal.resume_prompt);
    goal::adopt(&mut app, Some(Box::new(suspended)), true);
    assert!(app.goal.resume_prompt);
    goal::adopt(&mut app, Some(Box::new(goal("active"))), false);
    assert!(!app.goal.resume_prompt);
}

#[test]
fn failures_map_to_notices() {
    let notice = |code: &str, message: &str| {
        let error: RpcError =
            serde_json::from_value(json!({"code": code, "message": message})).unwrap();
        goal::request::failure_notice(&error)
    };
    assert_eq!(
        notice("conflict", "stale"),
        (
            Severity::Warning,
            "The goal changed elsewhere; showing the latest.".into()
        )
    );
    assert_eq!(
        notice("method_not_found", "goal/set"),
        (
            Severity::Warning,
            "This app server does not support goals.".into()
        )
    );
    assert_eq!(
        notice("invalid_params", "Too long"),
        (Severity::Error, "Too long".into())
    );
}
