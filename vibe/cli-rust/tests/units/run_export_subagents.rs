//! The export's subagents: wire mapping, journal copies and the export field.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use vibe_rs::headless::subagent_report;
use vibe_rs::run_export::{
    copy_subagent_journals, export_subagents, RunSubagentStatus, RunUsage, SubagentReport,
    METADATA_FILENAME, SUBAGENTS_DIRNAME,
};
use vibe_rs::server::SubagentRun;

fn wire_subagent(id: &str, status: &str, session_dir: Option<&Path>) -> SubagentRun {
    serde_json::from_value(json!({
        "sessionId": id,
        "parentSessionId": "root",
        "rootSessionId": "root",
        "name": "reader",
        "agentType": "explore",
        "spawnActionId": "action-1",
        "spawnCallId": "program:root_call_0:1",
        "status": status,
        "sessionDir": session_dir,
        "model": "devstral-small",
        "usage": {"inputTokens": 1000, "outputTokens": 10, "cachedInputTokens": 400, "totalTokens": 1010},
        "costUsd": 0.00146,
        "pricingModel": "small",
        "pricingFallback": false,
        "steps": 2,
    }))
    .unwrap()
}

fn store(root: &Path, name: &str) -> PathBuf {
    let dir = root.join(name);
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("messages.jsonl"), "{}\n").unwrap();
    fs::write(
        dir.join(METADATA_FILENAME),
        json!({"session_id": name, "config": {"headers": {"Authorization": "Bearer secret"}}})
            .to_string(),
    )
    .unwrap();
    fs::write(dir.join("meta.json.tmp"), r#"{"config": {}}"#).unwrap();
    dir
}

fn report(id: &str, session_dir: Option<&Path>) -> SubagentReport {
    subagent_report(wire_subagent(id, "idle", session_dir))
}

#[test]
fn a_wire_subagent_maps_to_its_export_entry_with_the_servers_cost() {
    let report = subagent_report(wire_subagent("child-1", "idle", Some(Path::new("/s/c"))));

    let subagent = &report.subagent;
    assert_eq!(report.session_dir.as_deref(), Some(Path::new("/s/c")));
    assert_eq!(subagent.agent_name, "reader");
    assert_eq!(subagent.agent_type.as_deref(), Some("explore"));
    assert_eq!(subagent.parent_session_id, "root");
    assert_eq!(subagent.spawn_action_id, "action-1");
    assert_eq!(
        subagent.spawn_call_id.as_deref(),
        Some("program:root_call_0:1")
    );
    assert_eq!(subagent.status, RunSubagentStatus::Idle);
    assert_eq!(
        subagent.usage,
        Some(RunUsage {
            input_tokens: 1000,
            output_tokens: 10,
            cached_input_tokens: 400,
            total_tokens: 1010,
        })
    );
    assert_eq!(subagent.cost_usd, Some(0.00146));
    assert_eq!(subagent.model.as_deref(), Some("devstral-small"));
    assert_eq!(subagent.pricing_model.as_deref(), Some("small"));
    assert!(!subagent.pricing_fallback);
    assert_eq!(subagent.steps, Some(2));
    assert_eq!(subagent.journal_dir, None);
}

#[test]
fn a_subagent_priced_at_the_roots_rates_says_so() {
    let wire = json!({
        "sessionId": "c", "parentSessionId": "r", "rootSessionId": "r", "name": "n",
        "spawnActionId": "a", "status": "idle",
        "usage": {"inputTokens": 10, "outputTokens": 1, "cachedInputTokens": 0, "totalTokens": 11},
        "costUsd": 0.5, "pricingModel": "root-alias", "pricingFallback": true,
    });

    let report = subagent_report(serde_json::from_value(wire).unwrap());

    assert_eq!(report.subagent.model, None);
    assert_eq!(report.subagent.pricing_model.as_deref(), Some("root-alias"));
    assert!(report.subagent.pricing_fallback);
}

#[test]
fn every_wire_status_maps_and_an_unknown_one_reads_as_unknown() {
    for (wire, status) in [
        ("running", RunSubagentStatus::Running),
        ("idle", RunSubagentStatus::Idle),
        ("interrupted", RunSubagentStatus::Interrupted),
        ("failed", RunSubagentStatus::Failed),
        ("closed", RunSubagentStatus::Closed),
        ("unknown", RunSubagentStatus::Unknown),
        ("hibernating", RunSubagentStatus::Unknown),
    ] {
        let report = subagent_report(wire_subagent("c", wire, None));
        assert_eq!(report.subagent.status, status, "{wire}");
    }
}

#[test]
fn a_subagent_whose_store_did_not_answer_has_no_usage() {
    let wire = json!({
        "sessionId": "c", "parentSessionId": "r", "rootSessionId": "r", "name": "n",
        "agentType": null, "spawnActionId": "a", "status": "running",
    });
    let report = subagent_report(serde_json::from_value(wire).unwrap());

    assert_eq!(report.subagent.usage, None);
    assert_eq!(report.subagent.cost_usd, None);
    assert_eq!(report.subagent.model, None);
    assert_eq!(report.subagent.pricing_model, None);
    assert!(!report.subagent.pricing_fallback);
    assert_eq!(report.subagent.steps, None);
    assert_eq!(report.session_dir, None);
}

#[test]
fn copying_subagent_journals_stages_and_redacts_each_one() {
    let root = tempfile::tempdir().unwrap();
    let first = store(root.path(), "c1");
    let second = store(root.path(), "c2");
    let output_dir = root.path().join("out");

    let dirs = copy_subagent_journals(&[("c1".into(), first), ("c2".into(), second)], &output_dir)
        .unwrap();

    assert_eq!(
        dirs,
        vec![
            ("c1".to_owned(), "subagents/c1".to_owned()),
            ("c2".to_owned(), "subagents/c2".to_owned()),
        ]
    );
    for id in ["c1", "c2"] {
        let copy = output_dir.join(SUBAGENTS_DIRNAME).join(id);
        assert!(copy.join("messages.jsonl").is_file());
        assert!(!copy.join("meta.json.tmp").exists());
        let meta: Value =
            serde_json::from_str(&fs::read_to_string(copy.join(METADATA_FILENAME)).unwrap())
                .unwrap();
        assert_eq!(
            meta["config"]["headers"],
            json!({"Authorization": "<redacted>"})
        );
    }
    assert!(!output_dir.join(".subagents.tmp").exists());
    assert!(!output_dir.join(".subagents.old").exists());
}

#[test]
fn copying_subagent_journals_replaces_an_earlier_exports_and_none_removes_it() {
    let root = tempfile::tempdir().unwrap();
    let output_dir = root.path().join("out");
    let stale = output_dir.join(SUBAGENTS_DIRNAME).join("old-child");
    fs::create_dir_all(&stale).unwrap();
    let child = store(root.path(), "c1");

    copy_subagent_journals(&[("c1".into(), child)], &output_dir).unwrap();
    assert!(!stale.exists());
    assert!(output_dir.join(SUBAGENTS_DIRNAME).join("c1").is_dir());

    assert_eq!(copy_subagent_journals(&[], &output_dir).unwrap(), vec![]);
    assert!(!output_dir.join(SUBAGENTS_DIRNAME).exists());
}

#[test]
fn a_failed_subagent_copy_keeps_the_earlier_journals() {
    let root = tempfile::tempdir().unwrap();
    let output_dir = root.path().join("out");
    let earlier = output_dir.join(SUBAGENTS_DIRNAME).join("c0");
    fs::create_dir_all(&earlier).unwrap();
    let child = store(root.path(), "c1");

    let missing = root.path().join("gone");
    let copied =
        copy_subagent_journals(&[("c1".into(), child), ("c2".into(), missing)], &output_dir);

    assert!(copied.is_err());
    assert!(earlier.is_dir());
    assert!(!output_dir.join(".subagents.tmp").exists());
}

#[test]
fn the_export_names_each_copied_journal_and_leaves_storeless_ones_without() {
    let root = tempfile::tempdir().unwrap();
    let output_dir = root.path().join("out");
    let child = store(root.path(), "c1");
    let reports = [
        report("c1", Some(&child)),
        report("c2", None),
        report("c3", Some(&root.path().join("gone"))),
        report("../escape", Some(&child)),
    ];

    let subagents = export_subagents(&output_dir, &reports).unwrap();

    let journals: Vec<_> = subagents
        .iter()
        .map(|s| (s.session_id.as_str(), s.journal_dir.as_deref()))
        .collect();
    assert_eq!(
        journals,
        vec![
            ("c1", Some("subagents/c1")),
            ("c2", None),
            ("c3", None),
            ("../escape", None),
        ]
    );
    assert!(!root.path().join("escape").exists());
}

#[test]
fn a_run_without_subagents_leaves_them_out_and_clears_an_earlier_exports() {
    let root = tempfile::tempdir().unwrap();
    let output_dir = root.path().join("out");
    fs::create_dir_all(output_dir.join(SUBAGENTS_DIRNAME).join("old")).unwrap();

    assert_eq!(export_subagents(&output_dir, &[]), None);
    assert!(!output_dir.join(SUBAGENTS_DIRNAME).exists());
}

#[test]
fn an_export_entry_serializes_to_the_contract() {
    let mut subagent = report("c1", None).subagent;
    subagent.journal_dir = Some("subagents/c1".into());

    let json = serde_json::to_value(&subagent).unwrap();

    assert_eq!(
        json,
        json!({
            "session_id": "c1",
            "parent_session_id": "root",
            "root_session_id": "root",
            "agent_name": "reader",
            "agent_type": "explore",
            "spawn_action_id": "action-1",
            "spawn_call_id": "program:root_call_0:1",
            "status": "idle",
            "model": "devstral-small",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 10,
                "cached_input_tokens": 400,
                "total_tokens": 1010,
            },
            "cost_usd": 0.00146,
            "pricing_model": "small",
            "pricing_fallback": false,
            "steps": 2,
            "journal_dir": "subagents/c1",
        })
    );
}
