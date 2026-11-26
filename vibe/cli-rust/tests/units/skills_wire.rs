//! The `/skills` wire types: skew tolerance and the params each change sends.

use serde_json::json;
use vibe_rs::server::{
    method, InstalledSkills, SkillCatalog, SkillChange, SkillDetail, SkillScope, SkillUpdates,
    SkillVersions,
};

#[test]
fn skills_method_names_pin_the_wire_contract() {
    assert_eq!(method::SKILLS_INSTALLED, "skills/installed");
    assert_eq!(method::SKILLS_CATALOG, "skills/catalog");
    assert_eq!(method::SKILLS_VERSIONS, "skills/versions");
    assert_eq!(method::SKILLS_DETAIL, "skills/detail");
    assert_eq!(method::SKILLS_UPDATES, "skills/updates");
    assert_eq!(method::SKILLS_IMPORT, "skills/import");
    assert_eq!(method::SKILLS_REMOVE, "skills/remove");
    assert_eq!(method::SKILLS_SET_ENABLED, "skills/setEnabled");
}

#[test]
fn installed_skills_tolerate_new_scopes_missing_fields_and_bad_pins() {
    let answer: InstalledSkills = serde_json::from_value(json!({
        "skills": [
            {"name": "shared", "source": "registry", "scope": "session",
             "registry": {"skillId": "s", "version": 4, "alias": null, "future": 1},
             "futureField": true},
            {"name": "bare"},
            {"description": "no name"},
            {"name": "off", "enabled": false, "locked": true, "active": false}
        ],
        "futureTopLevel": 2
    }))
    .unwrap();
    let names: Vec<_> = answer
        .skills
        .iter()
        .map(|skill| skill.name.as_str())
        .collect();
    assert_eq!(names, ["shared", "bare", "off"]);
    assert_eq!(answer.skills[0].scope, "session");
    assert_eq!(
        answer.skills[0].registry.as_ref().map(|pin| pin.version),
        Some(4)
    );
    assert!(answer.skills[1].enabled, "a missing flag reads as enabled");
    assert!(
        answer.skills[1].active,
        "an older server reads as no shadowing"
    );
    assert!(!answer.skills[1].locked);
    assert!(!answer.skills[2].enabled && answer.skills[2].locked);
    assert!(!answer.skills[2].active);
}

#[test]
fn skill_catalog_versions_detail_and_updates_default_and_skip_bad_entries() {
    let catalog: SkillCatalog = serde_json::from_value(json!({
        "skills": [{"name": "a", "skillId": "1", "future": true}, {"name": "no id"}],
        "updates": {"a": 3},
    }))
    .unwrap();
    assert_eq!(catalog.skills.len(), 1);
    assert_eq!(catalog.updates.get("a"), Some(&3));
    assert!(!catalog.loaded && !catalog.project_available && catalog.authenticated);
    let versions: SkillVersions =
        serde_json::from_value(json!({"versions": [{"version": 2}, {"aliases": []}]})).unwrap();
    assert_eq!(versions.versions.len(), 1);
    let detail: SkillDetail =
        serde_json::from_value(json!({"detail": null, "body": "fallback"})).unwrap();
    assert_eq!(detail.into_text().as_deref(), Some("fallback"));
    let detail: SkillDetail =
        serde_json::from_value(json!({"detail": {"body": "full", "name": "a"}})).unwrap();
    assert_eq!(detail.into_text().as_deref(), Some("full"));
    let detail: SkillDetail =
        serde_json::from_value(json!({"detail": null, "body": null})).unwrap();
    assert_eq!(
        detail.into_text(),
        None,
        "a body the server could not fetch"
    );
    let updates: SkillUpdates =
        serde_json::from_value(json!({"updates": [{"name": "a", "latestVersion": 3}, {}]}))
            .unwrap();
    assert_eq!(updates.updates.len(), 1);
    assert_eq!(updates.updates[0].name, "a");
}

#[test]
fn skill_changes_send_the_params_their_methods_take() {
    let import = SkillChange::Import {
        skill_id: "id".to_owned(),
        version: None,
        alias: Some("latest".to_owned()),
        scope: SkillScope::Project,
    };
    let remove = SkillChange::Remove {
        name: "deploy".to_owned(),
        scope: SkillScope::Global,
    };
    let enabled = SkillChange::SetEnabled {
        name: "deploy".to_owned(),
        enabled: false,
    };
    let wire = |change: &SkillChange| (change.method(), serde_json::to_value(change).unwrap());
    assert_eq!(
        wire(&import),
        (
            "skills/import",
            json!({"skillId": "id", "version": null, "alias": "latest", "scope": "project"})
        )
    );
    assert_eq!(
        wire(&remove),
        (
            "skills/remove",
            json!({"name": "deploy", "scope": "global"})
        )
    );
    assert_eq!(
        wire(&enabled),
        (
            "skills/setEnabled",
            json!({"name": "deploy", "enabled": false})
        )
    );
}
