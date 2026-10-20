//! Exact snapshots of every Harness-authored instruction the model can see.
//!
//! The canonical context snapshot covers one fully enabled request. These
//! snapshots cover what it does not: the system message with each optional
//! section omitted, both compaction requests, and the built-in tool catalog
//! for every command environment. Together they guard that moving instruction
//! text between Rust and data files leaves the model-visible bytes unchanged.

use serde_json::{Value, json};

use crate::core::HarnessConfig;
use crate::core::config::{ImageDeliveryMode, ImageTokenCost};
use crate::core::features::background_processes::Mode as BackgroundProcessMode;
use crate::core::features::compaction::{
    CompactionBudget, CompactionProjection, CompactionProjectionFit, CompactionRequestKind,
    NewestToolExchange, prompt_message,
};
use crate::core::features::model_wait::Mode as ModelWaitMode;
use crate::core::features::skills::SkillDefinition;
use crate::core::features::subagents::Mode as SubagentMode;
use crate::core::prompt::build_system_prompt;
use crate::core::testing::{assistant_text, config};
use crate::core::tools::command_environment::CommandEnvironment;
use crate::core::tools::resolved::ResolvedTools;
use crate::core::wire::message::{Message, StoredMessage};

const ENVIRONMENTS: [(&str, CommandEnvironment); 5] = [
    ("disabled", CommandEnvironment::Disabled),
    ("unix", CommandEnvironment::Unix),
    ("git_bash", CommandEnvironment::GitBash),
    ("powershell", CommandEnvironment::PowerShell),
    ("in_memory_bash", CommandEnvironment::InMemoryBash),
];

fn all_features(environment: CommandEnvironment) -> HarnessConfig {
    let mut config = config();
    config.system_instructions = String::new();
    config.settings.tools.command_environment = environment;
    config.settings.tools.background_processes = if environment.is_enabled() {
        BackgroundProcessMode::Enabled
    } else {
        BackgroundProcessMode::Disabled
    };
    config.settings.tools.subagents = SubagentMode::enabled();
    config.settings.tools.model_wait = ModelWaitMode::Enabled {
        external_input: true,
    };
    config.capabilities.skills = vec![SkillDefinition {
        name: "review".to_string(),
        description: "Review code.".to_string(),
        path: "/skills/review/SKILL.md".to_string(),
    }];
    config
}

fn system_prompt(config: &HarnessConfig) -> String {
    let tools = ResolvedTools::compile(config).expect("snapshot configuration is valid");
    build_system_prompt(config, tools.tool_group_inventory_prompt_section())
}

#[test]
fn system_message_with_each_optional_section() {
    let full = all_features(CommandEnvironment::Unix);
    let mut without_background = full.clone();
    without_background.settings.tools.background_processes = BackgroundProcessMode::Disabled;
    let mut without_subagents = full.clone();
    without_subagents.settings.tools.subagents = SubagentMode::Disabled;
    let mut without_skills = full.clone();
    without_skills.capabilities.skills.clear();

    let mut by_profile = serde_json::Map::new();
    for (name, environment) in ENVIRONMENTS {
        by_profile.insert(
            name.to_string(),
            reviewable(json!(system_prompt(&all_features(environment)))),
        );
    }

    insta::assert_json_snapshot!(
        "system_message_sections",
        json!({
            "all_features_by_command_environment": by_profile,
            "without_background_processes": reviewable(json!(system_prompt(&without_background))),
            "without_subagents": reviewable(json!(system_prompt(&without_subagents))),
            "without_skills": reviewable(json!(system_prompt(&without_skills))),
        })
    );
}

#[test]
fn compaction_requests() {
    let history = vec![
        StoredMessage::visible(Message::user_text("Rename the config loader.")),
        StoredMessage::visible(assistant_text("Renamed it in src/config.rs.")),
    ];
    let generated = StoredMessage::generated_system(Message::system_text("agent system prompt"));
    let budget = CompactionBudget {
        token_threshold: None,
        image_delivery: ImageDeliveryMode::Native,
        image_cost: ImageTokenCost::default(),
    };

    let mut requests = serde_json::Map::new();
    for (name, extra) in [
        ("without_extra_instructions", ""),
        ("with_extra_instructions", "Keep file paths verbatim."),
    ] {
        let mut messages = vec![generated.clone()];
        messages.extend(history.clone());
        messages.push(prompt_message(extra));
        let in_context = CompactionProjection::restore(
            messages.clone(),
            0,
            CompactionRequestKind::InContext,
            &[],
            budget,
            NewestToolExchange::MayRemove,
        );
        let standalone = CompactionProjection::restore(
            messages,
            1,
            CompactionRequestKind::Standalone,
            &[],
            budget,
            NewestToolExchange::MayRemove,
        );
        requests.insert(
            name.to_string(),
            json!({
                "in_context": model_messages(in_context),
                "standalone": model_messages(standalone),
            }),
        );
    }

    insta::assert_json_snapshot!("compaction_requests", Value::Object(requests));
}

#[test]
fn built_in_tool_catalog_by_command_environment() {
    for (name, environment) in ENVIRONMENTS {
        let config = all_features(environment);
        let tools = ResolvedTools::compile(&config).expect("snapshot configuration is valid");
        let snapshot = json!({
            "top_level_tools": tools.top_level_tools(),
            "programmatic_tools": tools.program_descriptors(),
        });
        insta::assert_json_snapshot!(format!("built_in_tools_{name}"), reviewable(snapshot));
    }
}

fn model_messages(
    projection: Result<CompactionProjectionFit, crate::core::error::CoreError>,
) -> Value {
    let Ok(CompactionProjectionFit::Fitted(projection)) = projection else {
        panic!("snapshot compaction request fits without a threshold");
    };
    reviewable(serde_json::to_value(projection.model_messages()).unwrap())
}

fn reviewable(value: Value) -> Value {
    match value {
        Value::String(text) if text.contains('\n') => json!({
            "$multiline": text.split('\n').collect::<Vec<_>>()
        }),
        Value::Array(values) => Value::Array(values.into_iter().map(reviewable).collect()),
        Value::Object(fields) => Value::Object(
            fields
                .into_iter()
                .map(|(key, value)| (key, reviewable(value)))
                .collect(),
        ),
        value => value,
    }
}
