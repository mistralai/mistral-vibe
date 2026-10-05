//! `--worktree` flag resolution: flag to `WorktreeInput` mapping, trust
//! composition, wire shapes, the `-c`/`--resume` conflict, and tracking the
//! session's worktree from the `worktree` transcript effect.

use clap::Parser;
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::cli::Cli;
use vibe_rs::server::{AgentConfig, PublicSession, PublicSessionState, WorktreeInput};
use vibe_rs::startup::resume_params;
use vibe_rs::worktree::{track, track_entry, track_patch, track_state, WorktreeInfo};

fn demo_worktree(created: bool) -> WorktreeInfo {
    WorktreeInfo {
        name: "demo".into(),
        branch: "demo".into(),
        path: "/wt/demo".into(),
        created,
    }
}

fn state_with(history: Vec<Value>, cwd: Option<&str>) -> PublicSessionState {
    PublicSessionState {
        event_id: 1,
        session: PublicSession {
            id: "s1".into(),
            title: None,
            cwd: cwd.map(str::to_owned),
            worktree: None,
            token_usage: None,
        },
        history: Some(history),
        history_before_cursor: None,
        turns: None,
        child_sessions: Vec::new(),
        turn_queue: None,
        retrying: None,
    }
}

#[test]
fn named_worktree_creates_with_branch_defaulting_to_name() {
    let cli = Cli::parse_from(["vibe", "--worktree", "feature"]);
    let request = cli.worktree_request(None).unwrap();
    assert_eq!(
        request,
        WorktreeInput::Create {
            branch: "feature".into(),
            name: "feature".into()
        }
    );
    let json = serde_json::to_value(&request).unwrap();
    assert_eq!(
        json,
        json!({"kind": "create", "branch": "feature", "name": "feature"})
    );
}

#[test]
fn bare_worktree_carries_the_auto_prompt() {
    let cli = Cli::parse_from(["vibe", "--worktree"]);
    let request = cli.worktree_request(Some("say hi".into())).unwrap();
    assert_eq!(
        request,
        WorktreeInput::Auto {
            prompt: Some("say hi".into())
        }
    );
    let json = serde_json::to_value(&request).unwrap();
    assert_eq!(json, json!({"kind": "auto", "prompt": "say hi"}));
}

#[test]
fn bare_worktree_without_prompt_omits_the_field() {
    let cli = Cli::parse_from(["vibe", "--worktree"]);
    let request = cli.worktree_request(None).unwrap();
    assert_eq!(request, WorktreeInput::Auto { prompt: None });
    let json = serde_json::to_value(&request).unwrap();
    assert_eq!(json, json!({"kind": "auto"}));
}

#[test]
fn no_flag_yields_no_request() {
    let cli = Cli::parse_from(["vibe"]);
    assert_eq!(cli.worktree_request(None), None);
}

#[test]
fn empty_name_is_falsy_like_python() {
    // Python gates on `if args.worktree:` and `bool(args.trust or
    // args.worktree)`, so --worktree "" means no worktree and no trust.
    let cli = Cli::parse_from(["vibe", "--worktree", ""]);
    assert_eq!(cli.worktree_request(None), None);
    let config = cli
        .interactive_agent_config(Some("/work".into()), cli.worktree_request(None))
        .unwrap();
    assert!(!config.trust_workspace);
    assert_eq!(config.worktree, None);
    let options = Cli::parse_from(["vibe", "-p", "hi", "--worktree", ""])
        .headless_options(None)
        .unwrap();
    assert!(!options.trust);
    assert_eq!(options.worktree, None);
}

#[test]
fn interactive_worktree_grants_trust_and_rides_agent_config() {
    let cli = Cli::parse_from(["vibe", "--worktree", "demo"]);
    let config = cli
        .interactive_agent_config(
            Some("/work".into()),
            cli.worktree_request(Some("ignored".into())),
        )
        .unwrap();
    assert!(config.trust_workspace);
    assert_eq!(
        config.worktree,
        Some(WorktreeInput::Create {
            branch: "demo".into(),
            name: "demo".into()
        })
    );
    // The wire field is skipped when unset, as in Python `WorktreeInput | None`.
    let bare = Cli::parse_from(["vibe"])
        .interactive_agent_config(Some("/work".into()), None)
        .unwrap();
    let json = serde_json::to_value(&bare).unwrap();
    assert!(json.get("worktree").is_none());
    assert!(!bare.trust_workspace);
}

#[test]
fn interactive_bare_worktree_uses_the_initial_prompt() {
    // clap (like Python argparse) greedily takes the token after --worktree as
    // its NAME, so the prompt must precede the bare flag.
    let cli = Cli::parse_from(["vibe", "say hi", "--worktree"]);
    let auto = cli.interactive_initial_prompt(None);
    let config = cli
        .interactive_agent_config(None, cli.worktree_request(auto))
        .unwrap();
    assert_eq!(
        config.worktree,
        Some(WorktreeInput::Auto {
            prompt: Some("say hi".into())
        })
    );
}

#[test]
fn headless_worktree_grants_trust_and_carries_prompt() {
    let options = Cli::parse_from(["vibe", "-p", "do the thing", "--worktree"])
        .headless_options(None)
        .unwrap();
    assert!(options.trust);
    assert_eq!(
        options.worktree,
        Some(WorktreeInput::Auto {
            prompt: Some("do the thing".into())
        })
    );
    let config = vibe_rs::headless::agent_config(&options, Some("/work".into())).unwrap();
    assert!(config.trust_workspace);
    assert!(config.headless);
    assert_eq!(
        config.worktree,
        Some(WorktreeInput::Auto {
            prompt: Some("do the thing".into())
        })
    );
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(json["worktree"]["prompt"], "do the thing");
}

#[test]
fn headless_named_worktree_grants_trust() {
    let options = Cli::parse_from(["vibe", "-p", "hi", "--worktree", "demo"])
        .headless_options(None)
        .unwrap();
    assert!(options.trust);
    assert_eq!(
        options.worktree,
        Some(WorktreeInput::Create {
            branch: "demo".into(),
            name: "demo".into()
        })
    );
}

#[test]
fn worktree_combines_with_continue_and_resume() {
    // Python prepares the worktree first and scopes the resume to it, so the
    // combination parses; the handshake sequences it after the settle.
    assert!(Cli::try_parse_from(["vibe", "--worktree", "demo", "-c"]).is_ok());
    assert!(Cli::try_parse_from(["vibe", "--worktree", "demo", "--resume", "s1"]).is_ok());
    assert!(Cli::try_parse_from(["vibe", "--worktree", "--resume"]).is_ok());
}

#[test]
fn resume_params_strip_the_worktree_field() {
    let cli = Cli::parse_from(["vibe", "--worktree", "demo"]);
    let config = cli
        .interactive_agent_config(Some("/work".into()), cli.worktree_request(None))
        .unwrap();
    let params = resume_params("session-1", &config);
    // The server rejects `worktree` on resume, so the rebind omits it.
    assert!(params["agentConfig"].get("worktree").is_none());
    assert_eq!(params["agentConfig"]["trustWorkspace"], true);
    // Trust survives, as in Python's SessionOptions.
    let plain = Cli::parse_from(["vibe"])
        .interactive_agent_config(Some("/work".into()), None)
        .unwrap();
    let params = resume_params("session-1", &plain);
    assert!(params["agentConfig"].get("worktree").is_none());
}

#[test]
fn worktree_input_wire_shapes_match_python_models() {
    let create = serde_json::to_value(WorktreeInput::Create {
        branch: "b".into(),
        name: "n".into(),
    })
    .unwrap();
    assert_eq!(
        create,
        json!({"kind": "create", "branch": "b", "name": "n"})
    );
    let auto = serde_json::to_value(WorktreeInput::Auto { prompt: None }).unwrap();
    assert_eq!(auto, json!({"kind": "auto"}));
    let config = serde_json::to_value(AgentConfig {
        worktree: Some(WorktreeInput::Create {
            branch: "demo".into(),
            name: "demo".into(),
        }),
        ..Default::default()
    })
    .unwrap();
    assert_eq!(config["worktree"]["kind"], "create");
}

/// A `worktree` effect entry shaped like the server's `WorktreeEffect`
/// (`_worktree_effects.py`): detail.input carries name/branch/path, and the
/// settled verb is Created or Reused.
fn worktree_effect(state_verb: Option<&str>, call_settled_verb: Option<&str>) -> Value {
    let input = json!({"name": "demo", "branch": "demo", "path": "/wt/demo"});
    let state = match state_verb {
        verb @ Some(_) => json!({
            "status": "completed",
            "display": {"success": true, "verb": verb, "message": "demo on demo"}
        }),
        None => json!({"status": "in_progress"}),
    };
    json!({
        "type": "effect",
        "detail": {
            "kind": "worktree",
            "toolName": "worktree",
            "input": input,
            "display": {
                "verb": "Creating",
                "message": "demo",
                "settledVerb": call_settled_verb,
                "statusText": "Creating worktree"
            }
        },
        "state": state
    })
}

#[test]
fn created_effect_tracks_the_worktree() {
    let entry = worktree_effect(Some("Created"), Some("Created"));
    let info = WorktreeInfo::from_entry(&entry).unwrap();
    assert_eq!(
        info,
        WorktreeInfo {
            name: "demo".into(),
            branch: "demo".into(),
            path: "/wt/demo".into(),
            created: true
        }
    );
}

#[test]
fn reused_effect_reports_created_false() {
    let entry = worktree_effect(Some("Reused"), Some("Reused"));
    let info = WorktreeInfo::from_entry(&entry).unwrap();
    assert!(!info.created);
}

#[test]
fn running_effect_without_input_is_not_tracked() {
    // WorktreeProgress has no input; nothing settles until the worktree exists.
    let entry = json!({
        "type": "effect",
        "detail": {
            "kind": "worktree",
            "toolName": "worktree",
            "display": {
                "verb": "Creating",
                "message": "workspace",
                "settledVerb": "Created",
                "statusText": "Creating worktree"
            }
        },
        "state": {"status": "in_progress"}
    });
    assert!(WorktreeInfo::from_entry(&entry).is_none());
}

#[test]
fn other_effects_are_ignored() {
    let entry = json!({
        "type": "effect",
        "detail": {"kind": "file_edit", "toolName": "edit", "input": {"path": "/x"}},
        "state": {"status": "completed"}
    });
    assert!(WorktreeInfo::from_entry(&entry).is_none());
}

#[test]
fn latest_state_history_worktree_wins() {
    let state = vibe_rs::server::PublicSessionState {
        event_id: 1,
        session: vibe_rs::server::PublicSession {
            id: "s1".into(),
            title: None,
            cwd: None,
            worktree: None,
            token_usage: None,
        },
        history: Some(vec![
            json!({"type": "message", "role": "user", "content": []}),
            worktree_effect(Some("Reused"), Some("Reused")),
        ]),
        history_before_cursor: None,
        turns: None,
        child_sessions: Vec::new(),
        turn_queue: None,
        retrying: None,
    };
    let info = WorktreeInfo::from_state(&state).unwrap();
    assert!(!info.created);
}

#[test]
fn tracking_follows_the_session_move() {
    let mut app = App::default();
    track(
        &mut app,
        WorktreeInfo {
            name: "demo".into(),
            branch: "demo".into(),
            path: "/wt/demo".into(),
            created: true,
        },
    );
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo"));
    assert!(app
        .session
        .worktree
        .as_ref()
        .is_some_and(|w| w.created && w.path == "/wt/demo"));
    // Idempotent: the same info does not rewrite the cwd.
    let moved = app.session.cwd.clone();
    track(
        &mut app,
        WorktreeInfo {
            name: "demo".into(),
            branch: "demo".into(),
            path: "/wt/demo".into(),
            created: true,
        },
    );
    assert_eq!(app.session.cwd, moved);
}

#[test]
fn failed_worktree_effect_is_not_tracked() {
    let entry = json!({
        "type": "effect",
        "detail": {
            "kind": "worktree",
            "toolName": "worktree",
            "input": {"name": "demo", "branch": "demo", "path": "/wt/demo"},
            "display": {"verb": "Creating", "settledVerb": "Created"}
        },
        "state": {
            "status": "failed",
            "display": {"success": false, "verb": "Failed", "message": "boom"}
        }
    });
    assert!(WorktreeInfo::from_entry(&entry).is_none());
}

#[test]
fn track_state_adopts_the_worktree_and_its_cwd() {
    let mut app = App::default();
    // A snapshot after the move carries the session's directory (root plus the
    // subdirectory it runs in), which refines the root-only effect path.
    let state = state_with(
        vec![worktree_effect(Some("Created"), Some("Created"))],
        Some("/wt/demo/vibe"),
    );
    track_state(&mut app, &state);
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo/vibe"));
    assert_eq!(app.session.worktree, Some(demo_worktree(true)));
    // A snapshot whose session has no cwd yet keeps the tracked root, so a
    // pre-move snapshot never drags the footer back to the project dir.
    let mut fresh = App::default();
    let early = state_with(
        vec![worktree_effect(Some("Created"), Some("Created"))],
        None,
    );
    track_state(&mut fresh, &early);
    assert_eq!(fresh.session.cwd.as_deref(), Some("/wt/demo"));
    assert_eq!(fresh.session.worktree, Some(demo_worktree(true)));
}

#[test]
fn track_state_without_a_worktree_keeps_the_client_cwd() {
    let mut app = App::default();
    track(&mut app, demo_worktree(true));
    // A page miss never moves the cwd and never drops the tracked worktree:
    // the client's cwd follows only the worktree move, like Python's chdir.
    let state = state_with(Vec::new(), Some("/plain/session"));
    track_state(&mut app, &state);
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo"));
    assert_eq!(app.session.worktree, Some(demo_worktree(true)));
}

#[test]
fn track_state_never_retargets_this_runs_prepared_worktree() {
    // The exit cleanup's pin is this run's announce, not the session's
    // tracking: a resumed session's restored effect carries the original
    // run's created verdict, so adopting it must not point the cleanup at
    // another session's worktree.
    let mut app = App::default();
    let mine = WorktreeInfo {
        name: "mine".into(),
        branch: "mine".into(),
        path: "/wt/mine".into(),
        created: true,
    };
    app.session.prepared_worktree = Some(mine.clone());
    let state = state_with(
        vec![worktree_effect(Some("Created"), Some("Created"))],
        Some("/wt/demo"),
    );
    track_state(&mut app, &state);
    assert_eq!(app.session.worktree, Some(demo_worktree(true)));
    assert_eq!(app.session.prepared_worktree, Some(mine));
}

#[test]
fn track_entry_reads_the_notification_payload() {
    let mut app = App::default();
    track_entry(
        &mut app,
        &json!({"entry": worktree_effect(Some("Reused"), Some("Reused"))}),
    );
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo"));
    assert_eq!(app.session.worktree, Some(demo_worktree(false)));
}

#[test]
fn worktree_remove_params_omit_unset_fields_for_old_servers() {
    let probe = serde_json::to_value(vibe_rs::server::WorktreeRemoveParams {
        cwd: "/wt/demo".into(),
        force: false,
        delete_branch: None,
        inspect: false,
    })
    .unwrap();
    assert_eq!(probe, json!({"cwd": "/wt/demo"}));
    let forced = serde_json::to_value(vibe_rs::server::WorktreeRemoveParams {
        cwd: "/wt/demo".into(),
        force: true,
        delete_branch: Some(false),
        inspect: false,
    })
    .unwrap();
    assert_eq!(
        forced,
        json!({"cwd": "/wt/demo", "force": true, "deleteBranch": false})
    );
    // The inspect probe rides the same param object, still additive.
    let inspecting = serde_json::to_value(vibe_rs::server::WorktreeRemoveParams {
        cwd: "/wt/demo".into(),
        force: false,
        delete_branch: None,
        inspect: true,
    })
    .unwrap();
    assert_eq!(inspecting, json!({"cwd": "/wt/demo", "inspect": true}));
}

#[test]
fn worktree_remove_response_parses_leniently() {
    let response: vibe_rs::server::WorktreeRemoveResponse = serde_json::from_value(json!({
        "outcome": "kept_dirty",
        "reasons": ["uncommitted changes"],
        "branchCreated": true,
        "branchDeleted": false
    }))
    .unwrap();
    assert_eq!(response.outcome, "kept_dirty");
    assert_eq!(response.reasons, ["uncommitted changes"]);
    assert_eq!(response.branch_created, Some(true));
    assert!(!response.branch_deleted);
    // Every field absent: old server, defaults only.
    let empty: vibe_rs::server::WorktreeRemoveResponse =
        serde_json::from_value(json!({"outcome": "removed"})).unwrap();
    assert_eq!(empty.outcome, "removed");
    assert_eq!(empty.branch_created, None);
    assert!(empty.reasons.is_empty());
}

#[test]
fn yes_answers_match_python() {
    use vibe_rs::worktree_exit::is_yes;
    // The caller trims and lowercases, like Python's input().strip().lower().
    for answer in ["y", "yes", "remove"] {
        assert!(is_yes(answer, false), "{answer}");
    }
    for answer in ["n", "", "no", "maybe", "ye"] {
        assert!(!is_yes(answer, false), "{answer}");
    }
    // The branch prompt accepts delete instead of remove.
    assert!(is_yes("delete", true));
    assert!(!is_yes("remove", true));
    assert!(!is_yes("delete", false));
}

#[test]
fn forced_delete_branch_follows_the_prompt_or_server_default() {
    use vibe_rs::worktree_exit::forced_delete_branch;
    // Vibe created the branch (or the server did not say): server default.
    assert_eq!(forced_delete_branch(Some(true), true), None);
    assert_eq!(forced_delete_branch(None, true), None);
    // Attached branch: the prompt's answer decides.
    assert_eq!(forced_delete_branch(Some(false), true), Some(true));
    assert_eq!(forced_delete_branch(Some(false), false), Some(false));
}

#[test]
fn forced_progress_line_waits_for_the_removal_it_announces() {
    use vibe_rs::worktree_exit::forced_progress_line;
    let info = demo_worktree(true);
    assert_eq!(
        forced_progress_line(&info, "removed"),
        Some("Removing worktree: /wt/demo".to_owned())
    );
    // The holder recheck lives in the forced RPC: kept outcomes never
    // promise a removal that did not run.
    assert_eq!(forced_progress_line(&info, "kept_in_use"), None);
    assert_eq!(forced_progress_line(&info, "kept_error"), None);
}

#[test]
fn outcome_lines_render_python_wording() {
    use vibe_rs::worktree_exit::{outcome_lines, removal_reason, DIM, YELLOW};
    let info = demo_worktree(true);
    let lines = outcome_lines(
        &info,
        &serde_json::from_value::<vibe_rs::server::WorktreeRemoveResponse>(json!({
            "outcome": "removed",
            "branchDeleted": true
        }))
        .unwrap(),
    );
    assert_eq!(lines, [(DIM, "Removed worktree: /wt/demo".to_owned())]);
    // A kept attached branch names the branch, like Python.
    let lines = outcome_lines(
        &info,
        &serde_json::from_value(json!({"outcome": "removed", "branchDeleted": false})).unwrap(),
    );
    assert_eq!(
        lines,
        [
            (DIM, "Removed worktree: /wt/demo".to_owned()),
            (DIM, "Kept branch: demo".to_owned())
        ]
    );
    let lines = outcome_lines(
        &info,
        &serde_json::from_value(json!({"outcome": "kept_in_use"})).unwrap(),
    );
    assert_eq!(
        lines,
        [(
            DIM,
            "Keeping worktree /wt/demo: in use by other session(s)".to_owned()
        )]
    );
    // The additive holder count says how many, like Python.
    let lines = outcome_lines(
        &info,
        &serde_json::from_value(json!({"outcome": "kept_in_use", "holders": 2})).unwrap(),
    );
    assert_eq!(
        lines,
        [(
            DIM,
            "Keeping worktree /wt/demo: in use by 2 other session(s)".to_owned()
        )]
    );
    let dirty = serde_json::from_value::<vibe_rs::server::WorktreeRemoveResponse>(json!({
        "outcome": "kept_dirty",
        "reasons": ["uncommitted changes"]
    }))
    .unwrap();
    assert_eq!(
        outcome_lines(&info, &dirty),
        [(
            YELLOW,
            "Could not remove worktree: uncommitted changes".to_owned()
        )]
    );
    // Without reasons the outcome itself is the message.
    let silent = serde_json::from_value::<vibe_rs::server::WorktreeRemoveResponse>(json!({
        "outcome": "kept_error"
    }))
    .unwrap();
    assert_eq!(removal_reason(&silent), "kept error");
    let none = serde_json::from_value::<vibe_rs::server::WorktreeRemoveResponse>(json!({
        "outcome": "not_found"
    }))
    .unwrap();
    assert!(outcome_lines(&info, &none).is_empty());
}

#[test]
fn session_state_worktree_tracks_without_an_effect() {
    let mut app = App::default();
    let session = PublicSession {
        id: "s1".into(),
        title: None,
        cwd: Some("/orig".into()),
        // The additive session-state field is the only pre-turn source: the
        // transcript effect only lands with the deferred first turn.
        worktree: Some(Box::new(vibe_rs::server::PublicSessionWorktree {
            name: "demo".into(),
            branch: "demo".into(),
            path: "/wt/demo".into(),
            created: true,
        })),
        token_usage: None,
    };
    let info = WorktreeInfo::from_session(&session).unwrap();
    assert_eq!(info, demo_worktree(true));
    track(&mut app, info);
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo"));
}

#[test]
fn session_updated_patch_moves_the_session() {
    use vibe_rs::worktree::track_patch;
    let mut app = App::default();
    track_patch(
        &mut app,
        &json!([
            {"op": "replace", "path": "/cwd", "value": "/wt/demo"},
            {
                "op": "replace",
                "path": "/worktree",
                "value": {"name": "demo", "branch": "demo", "path": "/wt/demo", "created": true}
            },
        ]),
    );
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo"));
    assert_eq!(app.session.worktree, Some(demo_worktree(true)));
    // A reused worktree reports created false and stays tracked.
    track_patch(
        &mut app,
        &json!([
            {"op": "replace", "path": "/worktree",
             "value": {"name": "demo", "branch": "demo", "path": "/wt/demo", "created": false}},
        ]),
    );
    assert_eq!(app.session.worktree, Some(demo_worktree(false)));
    // Unknown ops and a null worktree never move the session.
    track_patch(
        &mut app,
        &json!([
            {"op": "replace", "path": "/title", "value": "renamed"},
            {"op": "replace", "path": "/worktree", "value": null},
        ]),
    );
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo"));
    assert_eq!(app.session.worktree, Some(demo_worktree(false)));
}

#[test]
fn settle_cwd_reads_the_move_patch() {
    use vibe_rs::worktree_gate::settle_cwd;
    // The settle: /cwd and /worktree replaces ride one session/updated.
    let settled = json!({
        "patch": [
            {"op": "replace", "path": "/cwd", "value": "/wt/demo/sub"},
            {"op": "replace", "path": "/worktree",
             "value": {"name": "demo", "branch": "demo", "path": "/wt/demo", "created": true}},
        ]
    });
    assert_eq!(settle_cwd(&settled), Some("/wt/demo/sub".to_owned()));
    // A worktree null (or absent) is not a settle, even with a /cwd op.
    let not_moved = json!({
        "patch": [
            {"op": "replace", "path": "/cwd", "value": "/other"},
            {"op": "replace", "path": "/worktree", "value": null},
        ]
    });
    assert_eq!(settle_cwd(&not_moved), None);
    assert_eq!(settle_cwd(&json!({"patch": []})), None);
}

#[test]
fn failed_message_reads_the_pushed_failure_entry() {
    use vibe_rs::worktree_gate::failed_message;
    let failed = json!({
        "entry": {
            "detail": {"kind": "worktree", "toolName": "worktree"},
            "state": {
                "status": "failed",
                "error": {"code": "WorktreeError", "message": "boom"},
                "display": {"success": false, "verb": "Failed", "message": "Worktree creation failed"},
            }
        }
    });
    assert_eq!(
        failed_message(&failed),
        Some("boom".to_owned()),
        "the gate prints the underlying error, like Python's `Error: ...`"
    );
    // A completed effect or a foreign kind is not a failure.
    let completed = json!({
        "entry": {
            "detail": {"kind": "worktree"},
            "state": {"status": "completed"}
        }
    });
    assert_eq!(failed_message(&completed), None);
    let foreign = json!({
        "entry": {
            "detail": {"kind": "session_title_updated"},
            "state": {"status": "failed", "error": {"message": "boom"}}
        }
    });
    assert_eq!(failed_message(&foreign), None);
}

/// `absorb_until_settled` returns the settle's cwd and worktree, buffering
/// every notification it consumed for replay.
#[tokio::test]
async fn the_absorb_returns_the_settle_and_buffers_the_stream() {
    use std::time::Duration;
    use tokio::sync::mpsc;
    use vibe_rs::server::Notification;
    use vibe_rs::worktree_gate::absorb_until_settled;

    let (notif_tx, mut notifications) = mpsc::channel::<Notification>(4);
    // An unrelated notification first: it rides the absorbed buffer too.
    notif_tx
        .send(Notification {
            method: "session/updated".into(),
            params: serde_json::json!({"patch": []}),
        })
        .await
        .unwrap();
    tokio::spawn(async move {
        notif_tx
            .send(Notification {
                method: "session/updated".into(),
                params: serde_json::json!({
                    "patch": [
                        {"op": "replace", "path": "/cwd", "value": "/wt/demo/vibe"},
                        {"op": "replace", "path": "/worktree",
                         "value": {"name": "demo", "branch": "demo", "path": "/wt/demo", "created": true}},
                    ]
                }),
            })
            .await
            .unwrap();
    });

    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    let (settled, waited) = match absorb_until_settled(&mut notifications, deadline).await {
        Ok(result) => result,
        Err(message) => panic!("expected a settle, got failure: {message}"),
    };
    assert_eq!(settled.cwd.as_deref(), Some("/wt/demo/vibe"));
    let worktree = settled.worktree.expect("the announce carried the worktree");
    assert_eq!(worktree.path, "/wt/demo");
    assert!(worktree.created);
    assert_eq!(waited.notifications.len(), 2, "both notifications replay");
}

/// Nothing settles: the bound ends the absorb, without a settle.
#[tokio::test]
async fn the_absorb_waits_out_the_bound_when_nothing_settles() {
    use std::time::Duration;
    use tokio::sync::mpsc;
    use vibe_rs::server::Notification;
    use vibe_rs::worktree_gate::absorb_until_settled;

    let (_notif_tx, mut notifications) = mpsc::channel::<Notification>(4);
    let deadline = tokio::time::Instant::now() + Duration::from_millis(100);
    let (settled, _waited) = match absorb_until_settled(&mut notifications, deadline).await {
        Ok(result) => result,
        Err(message) => panic!("expected the bound to end the absorb: {message}"),
    };
    assert!(settled.cwd.is_none());
    assert!(settled.worktree.is_none());
}

#[test]
fn the_announce_cwd_wins_over_the_worktree_root() {
    let mut app = App::default();
    track_patch(
        &mut app,
        &json!([
            {"op": "replace", "path": "/cwd", "value": "/wt/demo/vibe"},
            {"op": "replace", "path": "/worktree",
             "value": {"name": "demo", "branch": "demo", "path": "/wt/demo", "created": true}},
        ]),
    );
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo/vibe"));
    assert_eq!(
        app.session.worktree.as_ref().map(|w| w.path.as_str()),
        Some("/wt/demo")
    );
}

/// A snapshot after the move carries the session's real cwd, so tracking from
/// it lands on the subdirectory too, not the worktree root.
#[test]
fn a_snapshot_refines_the_cwd_to_the_session_directory() {
    let mut app = App::default();
    let state = state_with(
        vec![worktree_effect(Some("Created"), Some("Created"))],
        Some("/wt/demo/vibe"),
    );
    track_state(&mut app, &state);
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo/vibe"));
    assert_eq!(
        app.session.worktree.as_ref().map(|w| w.path.as_str()),
        Some("/wt/demo")
    );
    // A snapshot without a cwd leaves the tracked root in place.
    track_state(&mut app, &state_with(vec![], None));
    assert_eq!(app.session.cwd.as_deref(), Some("/wt/demo/vibe"));
}

/// A pushed preparation failure fails the absorb with the server's message,
/// like Python's `Error: ...` exit 1.
#[tokio::test]
async fn a_late_failure_entry_fails_the_absorb() {
    use std::time::Duration;
    use tokio::sync::mpsc;
    use vibe_rs::server::Notification;
    use vibe_rs::worktree_gate::absorb_until_settled;

    let (notif_tx, mut notifications) = mpsc::channel::<Notification>(4);
    tokio::spawn(async move {
        notif_tx
            .send(Notification {
                method: "history/entryAdded".into(),
                params: serde_json::json!({
                    "entry": {
                        "detail": {"kind": "worktree"},
                        "state": {
                            "status": "failed",
                            "error": {"code": "WorktreeError", "message": "boom"},
                        }
                    }
                }),
            })
            .await
            .unwrap();
    });

    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    let error = match absorb_until_settled(&mut notifications, deadline).await {
        Err(message) => message,
        Ok(_) => panic!("expected the pushed failure to fail the absorb"),
    };
    assert_eq!(error, "boom");
}
