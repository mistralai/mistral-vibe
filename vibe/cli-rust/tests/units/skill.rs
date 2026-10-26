//! Skill origins read from runtime summaries, tolerant of older servers and caches.

use serde_json::json;
use vibe_rs::skill::{Skill, SkillOrigin};
use vibe_rs::utils::startup_cache::{read_skills, StartupConfig};

fn summary(source: Option<&str>, scope: Option<&str>) -> serde_json::Value {
    let mut skill = json!({"name": "deploy", "description": "Ship it", "userInvocable": true});
    if let Some(source) = source {
        skill["source"] = source.into();
    }
    if let Some(scope) = scope {
        skill["scope"] = scope.into();
    }
    skill
}

#[test]
fn the_origin_is_the_scope_whatever_the_source() {
    for (source, scope, origin) in [
        (Some("local"), Some("global"), SkillOrigin::User),
        (Some("plugin"), Some("global"), SkillOrigin::User),
        (Some("registry"), Some("global"), SkillOrigin::User),
        (Some("local"), Some("project"), SkillOrigin::Project),
        (Some("plugin"), Some("project"), SkillOrigin::Project),
        (Some("registry"), Some("project"), SkillOrigin::Project),
        (Some("builtin"), Some("builtin"), SkillOrigin::Vibe),
        (Some("plugin"), Some("builtin"), SkillOrigin::Vibe),
        (Some("builtin"), Some("global"), SkillOrigin::Vibe),
        (Some("local"), None, SkillOrigin::Other),
        (None, None, SkillOrigin::Other),
        (
            Some("future-source"),
            Some("future-scope"),
            SkillOrigin::Other,
        ),
    ] {
        let skill = Skill::from_wire(&summary(source, scope)).expect("user-invocable");
        assert_eq!(skill.origin, origin, "{source:?} {scope:?}");
    }
}

#[test]
fn only_named_user_invocable_skills_are_offered() {
    let response = json!({"runtime": {"skills": [
        summary(Some("local"), Some("project")),
        {"name": "model-only", "description": "", "userInvocable": false},
        {"name": "legacy", "description": ""},
        {"description": "nameless", "userInvocable": true},
    ]}});
    let skills = read_skills(&response);
    assert_eq!(skills.len(), 1);
    assert_eq!(skills[0].name, "deploy");
    assert_eq!(skills[0].origin, SkillOrigin::Project);
}

#[test]
fn a_cache_written_before_origins_still_loads_its_skills() {
    let mut cache = serde_json::to_value(StartupConfig::default()).expect("serialize");
    cache["skills"] = json!([["deploy", "Ship it"]]);
    let loaded: StartupConfig = serde_json::from_value(cache).expect("old cache loads");
    assert_eq!(loaded.skills, vec![Skill::new("deploy", "Ship it")]);
}

#[test]
fn a_cached_origin_survives_a_round_trip() {
    let skill = Skill {
        origin: SkillOrigin::Project,
        ..Skill::new("toolkit:deploy", "Ship it")
    };
    let value = serde_json::to_value(&skill).expect("serialize");
    assert_eq!(serde_json::from_value::<Skill>(value).expect("load"), skill);
}

#[test]
fn an_unknown_cached_origin_loads_as_a_plain_skill() {
    let value = json!({"name": "deploy", "description": "Ship it", "origin": "registry"});
    let skill: Skill = serde_json::from_value(value).expect("load");
    assert_eq!(skill.origin, SkillOrigin::Other);
}
