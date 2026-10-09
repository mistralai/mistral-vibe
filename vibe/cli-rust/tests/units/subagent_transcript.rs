//! Child transcript cache: tail merge, LRU, parent instruction, ready states.

use ratatui::layout::Rect;
use serde_json::{json, Value};
use vibe_rs::mouse::MouseTarget;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::subagents::transcripts::merge_history_tail;
use vibe_rs::subagents::{SubagentTranscripts, READY_MESSAGE};

fn entry(id: &str, text: &str) -> Value {
    json!({
        "id": id,
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "generationStatus": "completed",
        "sessionId": "child",
        "createdAt": 1_787_593_260_000u64,
        "updatedAt": 1_787_593_260_000u64,
        "turnId": "t1",
        "relatedEntryId": null,
    })
}

fn user_entry(id: &str, text: &str, source: &str) -> Value {
    json!({
        "id": id,
        "type": "message",
        "role": "user",
        "source": source,
        "content": [{"type": "text", "text": text}],
        "generationStatus": "completed",
        "sessionId": "child",
        "createdAt": 1_787_593_260_000u64,
        "updatedAt": 1_787_593_260_000u64,
        "turnId": "t1",
        "relatedEntryId": null,
    })
}

/// Two calls, the fewest that still fold into a tool group.
fn tool_group() -> Vec<Value> {
    ["e1", "e2"]
        .map(|id| {
            json!({
                "id": id,
                "type": "effect",
                "detail": {"kind": "shell", "display": {"verb": "Running"}},
                "state": {"status": "completed"},
            })
        })
        .to_vec()
}

fn child(id: &str, status: SessionStatus) -> PublicChildSession {
    PublicChildSession {
        id: id.into(),
        name: "explore-1".into(),
        agent_type: "explore".into(),
        status,
        created_at: 1,
        updated_at: 2,
        ..Default::default()
    }
}

fn ids(history: &[Value]) -> Vec<&str> {
    history
        .iter()
        .map(|entry| entry.get("id").and_then(Value::as_str).unwrap_or_default())
        .collect()
}

#[test]
fn tail_merge_keeps_the_cached_prefix_up_to_the_first_shared_id() {
    let history = vec![
        entry("a", "old a"),
        entry("b", "old b"),
        entry("c", "old c"),
    ];
    let latest = vec![entry("b", "new b"), entry("d", "new d")];
    let merged = merge_history_tail(&history, &latest);
    assert_eq!(ids(&merged), ["a", "b", "d"]);
    assert_eq!(
        merged[1].pointer("/content/0/text").and_then(Value::as_str),
        Some("new b")
    );
}

#[test]
fn tail_merge_without_overlap_appends_the_whole_tail() {
    let history = vec![entry("a", "old"), entry("b", "old")];
    let latest = vec![entry("x", "new"), entry("y", "new")];
    assert_eq!(
        ids(&merge_history_tail(&history, &latest)),
        ["a", "b", "x", "y"]
    );
    assert!(
        merge_history_tail(&history, &[]).is_empty(),
        "Python clears on an empty tail"
    );
}

#[test]
fn a_complete_history_replaces_the_cached_one_wholesale() {
    let mut cache = SubagentTranscripts::default();
    cache.replace_history("child", vec![entry("a", "old")], true, false);
    assert!(cache.child("child").unwrap().history_complete);
    assert!(cache.select(Some("child")));
    // Leaving for another child drops the cached completeness (Python `select`).
    cache.select(Some("other"));
    assert!(!cache.child("child").unwrap().history_complete);
    cache.replace_history("child", vec![entry("b", "new")], false, false);
    assert_eq!(
        ids(cache.child("child").unwrap().history.as_ref().unwrap()),
        ["b"]
    );
}

#[test]
fn an_identical_tail_merge_is_a_no_op() {
    let mut cache = SubagentTranscripts::default();
    let history = vec![entry("a", "same"), entry("b", "same")];
    assert!(
        cache.replace_history("child", history, true, false),
        "first apply rebuilds"
    );
    let before = cache.child("child").unwrap().history.clone().unwrap();
    assert!(!cache.replace_history(
        "child",
        vec![entry("a", "same"), entry("b", "same")],
        true,
        false
    ));
    assert_eq!(
        cache.child("child").unwrap().history.as_ref().unwrap(),
        &before
    );
}

#[test]
fn the_parent_instruction_is_prepended_once() {
    let mut cache = SubagentTranscripts::default();
    let spawn = json!({
        "id": "spawn",
        "createdAt": 5,
        "updatedAt": 6,
        "type": "effect",
        "detail": {
            "kind": "subagent",
            "toolName": "subagent.spawn",
            "childSessionId": "child",
            "input": {"task": "go investigate"},
        },
    });
    let mut app = vibe_rs::app::App::default();
    vibe_rs::subagents::remember_parent_instruction_from_entry(&mut app, &spawn);
    cache.remember_instruction(
        app.subagents
            .transcripts
            .parent_instructions
            .get("child")
            .unwrap()
            .clone(),
    );
    // The fetched history already carries a turn_start user message: it wins.
    cache.replace_history(
        "child",
        vec![
            user_entry("real", "go investigate", "turn_start"),
            entry("a", "work"),
        ],
        true,
        false,
    );
    assert_eq!(
        ids(cache.child("child").unwrap().history.as_ref().unwrap()),
        ["real", "a"]
    );
    // Without it, the synthesized one is prepended.
    cache.parent_instructions.insert(
        "child2".into(),
        json!({"id": "parent-instruction:child2", "sessionId": "child2", "type": "message", "role": "user", "source": "turn_start", "content": [{"type": "text", "text": "go investigate"}]}),
    );
    cache.replace_history("child2", vec![entry("a", "work")], true, false);
    assert_eq!(
        ids(cache.child("child2").unwrap().history.as_ref().unwrap()),
        ["parent-instruction:child2", "a"]
    );
}

#[test]
fn the_lru_caps_at_five_and_keeps_the_viewed_child() {
    let mut cache = SubagentTranscripts::default();
    for index in 0..6 {
        cache.prepare(&format!("child-{index}"));
        cache.select(Some(&format!("child-{index}")));
    }
    assert_eq!(cache.len(), 5, "the sixth prepare evicted the oldest");
    cache.select(Some("child-3"));
    for index in 0..4 {
        cache.prepare(&format!("extra-{index}"));
    }
    assert!(
        cache.child("child-3").is_some(),
        "the viewed child is never evicted"
    );
    assert!(cache.child("child-1").is_none());
    assert_eq!(cache.len(), 5);
}

#[test]
fn ready_message_lands_once_per_active_period() {
    let mut cache = SubagentTranscripts::default();
    let session = child("child", SessionStatus::Running);
    assert!(!cache.announce_ready_transition(&session));
    let idle = child("child", SessionStatus::Idle);
    assert!(cache.announce_ready_transition(&idle));
    assert!(!cache.announce_ready_transition(&idle), "only once");
    // A new active period retires the parked row; completing it appends a
    // fresh one, so exactly one shows and only while the child is idle.
    assert!(!cache.announce_ready_transition(&session));
    assert_eq!(
        cache
            .child("child")
            .unwrap()
            .local_user_messages
            .iter()
            .filter(|(message, _)| message == READY_MESSAGE)
            .count(),
        0,
        "the parked row retires while the child runs again"
    );
    assert!(cache.announce_ready_transition(&idle));
    assert_eq!(
        cache
            .child("child")
            .unwrap()
            .local_user_messages
            .iter()
            .filter(|(message, _)| message == READY_MESSAGE)
            .count(),
        1
    );
    // A failed child never announces, and nothing mounts for it.
    let failed = child("other", SessionStatus::Failed);
    assert!(!cache.announce_ready_transition(&failed));
    assert!(cache.child("other").is_none());
}

#[test]
fn a_same_length_rebuild_bumps_the_revision() {
    let mut cache = SubagentTranscripts::default();
    cache.replace_history("child", vec![entry("a", "short")], true, false);
    let before = cache.child("child").unwrap().transcript.revision();
    // Same entry count, grown text: the render cache must not reuse the old
    // heights keyed by the same revision.
    cache.replace_history("child", vec![entry("a", "grown text")], true, false);
    let after = cache.child("child").unwrap().transcript.revision();
    assert!(
        after > before,
        "revisions must stay monotonic across rebuilds"
    );
}

#[test]
fn toggling_tools_expands_the_viewed_children_groups() {
    let mut app = vibe_rs::app::App::default();
    app.subagents
        .transcripts
        .replace_history("a", tool_group(), true, false);
    let ids = app
        .subagents
        .transcripts
        .child("a")
        .unwrap()
        .transcript
        .expandable_ids();
    assert!(
        !ids.is_empty(),
        "the fixture must carry an expandable group"
    );
    vibe_rs::subagents::show_subagent_chat(&mut app, "a");
    app.toggle_tools();
    assert!(ids.iter().all(|id| app.view.expanded.contains(id)));
}

#[test]
fn local_messages_survive_a_history_rebuild() {
    let mut cache = SubagentTranscripts::default();
    cache.append_local_user_message("child", "stay put", vibe_rs::subagents::Severity::Error);
    cache.replace_history("child", vec![entry("a", "work")], true, false);
    let stored = cache.child("child").unwrap();
    assert_eq!(stored.local_user_messages.len(), 1);
    let id = "subagent-local-child-0";
    assert!(stored.transcript.contains(id));
    // Python `UserMessage(severity=ERROR)`: the severity rides the entry role.
    assert_eq!(
        stored.transcript.entry_raw(id).unwrap().get("role"),
        Some(&serde_json::json!("subagent_error"))
    );
}

#[test]
fn placeholders_follow_the_fetch_state() {
    let mut cache = SubagentTranscripts::default();
    cache.prepare("child");
    assert_eq!(
        cache.child("child").unwrap().placeholder(),
        Some("Loading subagent transcript…")
    );
    cache.show_error("child");
    assert_eq!(
        cache.child("child").unwrap().placeholder(),
        Some("This subagent is closed. Its transcript is no longer available.")
    );
    cache.replace_history("child", vec![], true, false);
    assert_eq!(
        cache.child("child").unwrap().placeholder(),
        Some("No transcript yet.")
    );
    cache.replace_history("child", vec![entry("a", "work")], true, false);
    assert_eq!(cache.child("child").unwrap().placeholder(), None);
}

#[test]
fn needs_complete_pages_only_without_a_shared_tail_id() {
    let known: std::collections::HashSet<String> =
        ["a", "b"].iter().map(|id| (*id).to_owned()).collect();
    assert!(
        vibe_rs::subagents::fetch::needs_complete(&known, false, &[]),
        "an incomplete cache pages"
    );
    assert!(
        !vibe_rs::subagents::fetch::needs_complete(&known, true, &[entry("b", "tail")]),
        "shared id"
    );
    assert!(
        vibe_rs::subagents::fetch::needs_complete(&known, true, &[entry("z", "tail")]),
        "no shared id"
    );
    assert!(
        !vibe_rs::subagents::fetch::needs_complete(&known, true, &[]),
        "empty tail"
    );
}

#[test]
fn clicking_a_group_header_expands_the_viewed_children_group() {
    let mut app = vibe_rs::app::App::default();
    app.subagents
        .transcripts
        .replace_history("a", tool_group(), true, false);
    let group_key = app
        .subagents
        .transcripts
        .child("a")
        .unwrap()
        .transcript
        .expandable_ids()
        .into_iter()
        .find(|id| id.starts_with("tool-group:"))
        .expect("the fixture must carry a tool group");
    vibe_rs::subagents::show_subagent_chat(&mut app, "a");
    // The render-time hitmap carries the child's group key; the event-time
    // toggle must resolve it against the child, not the main transcript.
    app.view.entry_hitmap = vec![(2, 5, None, group_key.clone())];
    vibe_rs::mouse::toggle_effect_at(&mut app, 3);
    assert!(app.view.expanded.contains(&group_key));
}

#[test]
fn toggling_tools_collapses_the_viewed_children_group_cache() {
    let mut app = vibe_rs::app::App::default();
    let effect = json!({
        "id": "e1",
        "type": "effect",
        "detail": {"kind": "shell", "display": {"verb": "Running"}},
        "state": {"status": "completed"},
    });
    app.subagents
        .transcripts
        .replace_history("a", vec![effect], true, false);
    vibe_rs::subagents::show_subagent_chat(&mut app, "a");
    {
        let child = app.subagents.transcripts.child_mut("a").unwrap();
        child.cache.ensure_context(40, 40, 0, false);
        child.cache.store_layout(
            child.transcript.revision(),
            40,
            3,
            vec![vibe_rs::utils::transcript_cache::LayoutEntry {
                index: 0,
                top: 0,
                height: 1,
                prewrapped: false,
            }],
            None,
        );
    }
    app.toggle_tools();
    app.toggle_tools();
    let child = app.subagents.transcripts.child("a").unwrap();
    assert!(
        child
            .cache
            .layout(child.transcript.revision(), 40)
            .is_none(),
        "a collapse must not keep the cached expanded layout"
    );
}

#[test]
fn clicking_the_composer_takes_focus_from_the_subagent_list() {
    let mut app = vibe_rs::app::App::default();
    let client = std::sync::Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _rx) = tokio::sync::mpsc::channel(1);
    vibe_rs::mouse::register_region(&mut app, Rect::new(0, 10, 40, 3), MouseTarget::Composer);
    app.subagents.list.focused = true;
    let click = |kind| crossterm::event::MouseEvent {
        kind,
        column: 5,
        row: 11,
        modifiers: crossterm::event::KeyModifiers::NONE,
    };
    vibe_rs::mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(crossterm::event::MouseEventKind::Down(
            crossterm::event::MouseButton::Left,
        )),
    );
    assert!(!app.subagents.list.focused, "the composer takes focus");
    assert!(app.view.app_focus);
}

#[test]
fn hovering_a_child_group_header_asks_for_the_hand() {
    let mut app = vibe_rs::app::App::default();
    app.subagents
        .transcripts
        .replace_history("a", tool_group(), true, false);
    let group_key = app
        .subagents
        .transcripts
        .child("a")
        .unwrap()
        .transcript
        .expandable_ids()
        .into_iter()
        .find(|id| id.starts_with("tool-group:"))
        .expect("the fixture must carry a tool group");
    vibe_rs::subagents::show_subagent_chat(&mut app, "a");
    vibe_rs::mouse::register_region(&mut app, Rect::new(0, 0, 40, 10), MouseTarget::Transcript);
    app.view.selection_region.area = Rect::new(0, 0, 40, 10);
    app.view.entry_hitmap = vec![(2, 5, None, group_key)];
    app.view.mouse_position = Some((5, 3));
    assert_eq!(
        vibe_rs::pointer::shape(&app),
        vibe_rs::pointer::Shape::Pointer
    );
}

#[test]
fn copying_a_child_view_selection_extracts_the_child_transcript() {
    let mut app = vibe_rs::app::App::default();
    app.view.transcript.add(&json!({
        "entry": {"id": "m1", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "main row"}], "generationStatus": "completed", "createdAt": 1_787_593_260_000u64, "updatedAt": 1_787_593_260_000u64, "turnId": "t1", "relatedEntryId": null}
    }));
    app.subagents
        .transcripts
        .replace_history("a", vec![entry("c1", "child row")], true, false);
    vibe_rs::subagents::show_subagent_chat(&mut app, "a");
    {
        let child = app.subagents.transcripts.child_mut("a").unwrap();
        child.cache.ensure_context(40, 40, 0, false);
        child.cache.store_layout(
            child.transcript.revision(),
            40,
            5,
            vec![vibe_rs::utils::transcript_cache::LayoutEntry {
                index: 0,
                top: 0,
                height: 5,
                prewrapped: false,
            }],
            None,
        );
    }
    app.view.selection_region.area = Rect::new(0, 0, 40, 10);
    app.view.selection_region.document = true;
    app.selection.region = Some(vibe_rs::app::Selection {
        owner: vibe_rs::selection::region::RegionId::Main,
        anchor: (0, 0),
        head: (39, 30),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: vibe_rs::selection::ScrollTarget::Transcript,
        table_cell: None,
        text: String::new(),
    });
    let copied = vibe_rs::selection::region::extract_document(&app).unwrap();
    assert!(copied.contains("child row"));
    assert!(!copied.contains("main row"));
}

#[test]
fn retiring_the_ready_row_keeps_later_local_rows() {
    let mut cache = SubagentTranscripts::default();
    let running = child("child", SessionStatus::Running);
    let idle = child("child", SessionStatus::Idle);
    assert!(!cache.announce_ready_transition(&running));
    assert!(cache.announce_ready_transition(&idle));
    cache.append_local_user_message(
        "child",
        "read-only scold",
        vibe_rs::subagents::Severity::Error,
    );
    // The child starts a new task: the ready row retires, and the later
    // row must re-render under its shifted positional id, or the next
    // append would overwrite it.
    assert!(!cache.announce_ready_transition(&running));
    cache.append_local_user_message("child", "second note", vibe_rs::subagents::Severity::Error);
    let stored = cache.child("child").unwrap();
    assert_eq!(stored.local_user_messages.len(), 2);
    for id in ["subagent-local-child-0", "subagent-local-child-1"] {
        assert!(stored.transcript.contains(id), "{id} must stay a live row");
    }
    assert_eq!(
        stored
            .transcript
            .entry_raw("subagent-local-child-0")
            .unwrap()
            .pointer("/content/0/text")
            .and_then(Value::as_str),
        Some("read-only scold")
    );
    assert_eq!(
        stored
            .transcript
            .entry_raw("subagent-local-child-1")
            .unwrap()
            .pointer("/content/0/text")
            .and_then(Value::as_str),
        Some("second note")
    );
}

#[test]
fn streaming_updates_do_not_postpone_the_refresh_deadline() {
    let mut app = vibe_rs::app::App::default();
    app.subagents.viewed_subagent_id = Some("child".into());
    vibe_rs::subagents::schedule_refresh(&mut app);
    let deadline = app
        .subagents
        .refresh_at
        .expect("the first update arms the 50ms sleep");
    assert!(!app.subagents.refresh_requested);
    vibe_rs::subagents::schedule_refresh(&mut app);
    assert_eq!(app.subagents.refresh_at, Some(deadline));
    assert!(app.subagents.refresh_requested);
}

#[test]
fn an_in_flight_read_blocks_a_second_until_it_lands() {
    let mut app = vibe_rs::app::App::default();
    app.subagents.viewed_subagent_id = Some("child".into());
    app.subagents.refresh_in_flight = true;
    vibe_rs::subagents::schedule_refresh(&mut app);
    assert!(
        app.subagents.refresh_at.is_none(),
        "a streaming child must not start a second read over the one in flight"
    );
    assert!(app.subagents.refresh_requested);
    vibe_rs::subagents::apply_event(
        &mut app,
        vibe_rs::subagents::Event::Fetched {
            session_id: "child".into(),
            history: vec![entry("a", "tail")],
            complete: true,
        },
    );
    assert!(!app.subagents.refresh_in_flight);
    assert!(app.subagents.refresh_at.is_some());
    assert!(!app.subagents.refresh_requested);
    assert_eq!(
        app.subagents
            .transcripts
            .child("child")
            .unwrap()
            .history
            .as_ref()
            .map(Vec::len),
        Some(1)
    );
}

#[test]
fn a_quiet_read_does_not_schedule_another() {
    let mut app = vibe_rs::app::App::default();
    app.subagents.viewed_subagent_id = Some("child".into());
    app.subagents.refresh_in_flight = true;
    vibe_rs::subagents::apply_event(
        &mut app,
        vibe_rs::subagents::Event::Fetched {
            session_id: "child".into(),
            history: vec![],
            complete: true,
        },
    );
    assert!(app.subagents.refresh_at.is_none());
    assert!(!app.subagents.refresh_in_flight);
    assert!(!app.subagents.refresh_requested);
}

#[test]
fn reset_holds_the_drain_until_the_outstanding_read_lands() {
    let mut app = vibe_rs::app::App::default();
    app.subagents.viewed_subagent_id = Some("child".into());
    app.subagents.refresh_in_flight = true;
    app.subagents.refresh_requested = true;
    app.subagents.refresh_at = Some(std::time::Instant::now());
    vibe_rs::subagents::reset_views(&mut app);
    assert!(app.subagents.refresh_at.is_none());
    assert!(!app.subagents.refresh_requested);
    assert!(app.subagents.refresh_in_flight);
    app.subagents.viewed_subagent_id = Some("child".into());
    vibe_rs::subagents::schedule_refresh(&mut app);
    assert!(
        app.subagents.refresh_at.is_none(),
        "the late read still owns the drain"
    );
}
