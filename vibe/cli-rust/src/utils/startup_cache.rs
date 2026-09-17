//! Best-effort cache for app-server values needed by the first UI frame.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::server::{AgentSafety, AgentSummary};
use crate::utils::paths;

const SCHEMA_VERSION: u32 = 7;
const FILE_NAME: &str = "ui_startup_config.json";
const MAX_CACHED_SKILLS: usize = 256;
/// Agent a fresh install starts on (Python `VibeConfig.default_agent`).
const DEFAULT_AGENT: &str = "accept-edits";

/// Cycleable builtins in discovery order, used until the server's snapshot
/// lands (Python `BUILTIN_AGENTS` minus the subagents).
fn builtin_agents() -> Vec<AgentSummary> {
    [
        ("ask", "Ask", AgentSafety::Neutral),
        ("plan", "Plan", AgentSafety::Safe),
        (DEFAULT_AGENT, "Accept Edits", AgentSafety::Destructive),
        ("auto-approve", "Auto Approve", AgentSafety::Yolo),
    ]
    .into_iter()
    .map(|(name, display_name, safety)| AgentSummary {
        name: name.into(),
        display_name: display_name.into(),
        safety,
        ..Default::default()
    })
    .collect()
}

pub fn read_skills(response: &Value) -> Vec<(String, String)> {
    let Some(skills) = response
        .pointer("/runtime/skills")
        .and_then(Value::as_array)
    else {
        return Vec::new();
    };
    skills
        .iter()
        .filter(|skill| skill.get("userInvocable").and_then(Value::as_bool) == Some(true))
        .filter_map(|skill| {
            let name = skill.get("name").and_then(Value::as_str)?;
            let description = skill
                .get("description")
                .and_then(Value::as_str)
                .unwrap_or("");
            Some((name.to_owned(), description.to_owned()))
        })
        .collect()
}

fn cached_skills(response: &Value) -> Vec<(String, String)> {
    read_skills(response)
        .into_iter()
        .take(MAX_CACHED_SKILLS)
        .collect()
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartupConfig {
    pub schema_version: u32,
    pub server_version: String,
    pub theme: String,
    pub disable_welcome_banner_animation: bool,
    /// Whether a mouse selection copies itself on release.
    pub autocopy_to_clipboard: bool,
    pub active_model: String,
    pub active_model_display_name: String,
    pub active_model_supports_images: bool,
    pub models_count: usize,
    /// Configured default used to choose the startup label from `agents`.
    pub default_agent: String,
    /// The cycleable agents, so Shift+Tab works before the snapshot lands.
    pub agents: Vec<AgentSummary>,
    /// Bounded user-invocable skill metadata for `/` completion before readiness.
    pub skills: Vec<(String, String)>,
    pub skills_count: usize,
    pub hooks_count: usize,
    pub connectors_connected: usize,
    pub connectors_total: usize,
    pub mcp_servers_enabled: usize,
    pub mcp_servers_total: usize,
    pub context_window: u64,
    pub vibe_code_enabled: bool,
    /// Whether the narrator requests turn summaries (Python `narrator_enabled`).
    pub narrator_enabled: bool,
}

impl Default for StartupConfig {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            server_version: env!("CARGO_PKG_VERSION").into(),
            theme: "auto".into(),
            disable_welcome_banner_animation: false,
            autocopy_to_clipboard: true,
            active_model: String::new(),
            active_model_display_name: String::new(),
            active_model_supports_images: false,
            models_count: 0,
            default_agent: DEFAULT_AGENT.into(),
            agents: builtin_agents(),
            skills: Vec::new(),
            skills_count: 0,
            hooks_count: 0,
            connectors_connected: 0,
            connectors_total: 0,
            mcp_servers_enabled: 0,
            mcp_servers_total: 0,
            context_window: 0,
            vibe_code_enabled: true,
            narrator_enabled: false,
        }
    }
}

impl StartupConfig {
    pub fn load() -> Option<Self> {
        cache_file().as_deref().and_then(Self::load_from)
    }

    fn load_from(path: &Path) -> Option<Self> {
        let cache: Self = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
        (cache.schema_version == SCHEMA_VERSION).then_some(cache)
    }

    pub fn update_cache(&self) -> io::Result<bool> {
        let Some(path) = cache_file() else {
            return Ok(false);
        };
        self.update_at(&path)
    }

    fn update_at(&self, path: &Path) -> io::Result<bool> {
        if Self::load_from(path).as_ref() == Some(self) {
            return Ok(false);
        }
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut body = serde_json::to_vec_pretty(self)?;
        body.push(b'\n');
        fs::write(path, body)?;
        Ok(true)
    }

    pub fn from_runtime(server_version: &str, response: &Value) -> Option<Self> {
        let runtime = response.get("runtime")?;
        let config = runtime.get("config")?;
        let model = config.get("activeModel")?;
        let model_name = model.get("displayName")?.as_str()?;
        let thinking = model.get("thinking")?.as_str()?;
        let sources = runtime.pointer("/mcp/sources").and_then(Value::as_array);
        let mcp_servers = sources
            .into_iter()
            .flatten()
            .filter(|source| source.get("kind").and_then(Value::as_str) == Some("server"));
        let (mcp_servers_enabled, mcp_servers_total) =
            mcp_servers.fold((0, 0), |(enabled, total), source| {
                let disabled = source.get("status").and_then(Value::as_str) == Some("disabled");
                (enabled + usize::from(!disabled), total + 1)
            });
        Some(Self {
            schema_version: SCHEMA_VERSION,
            server_version: server_version.to_owned(),
            theme: config.get("theme")?.as_str()?.to_owned(),
            disable_welcome_banner_animation: config
                .get("disableWelcomeBannerAnimation")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            autocopy_to_clipboard: config
                .get("autocopyToClipboard")
                .and_then(Value::as_bool)
                .unwrap_or(true),
            active_model: format!("{model_name}[{thinking}]"),
            active_model_display_name: model_name.to_owned(),
            active_model_supports_images: model
                .get("supportsImages")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            models_count: config
                .get("models")
                .and_then(Value::as_array)
                .map_or(0, Vec::len),
            default_agent: config
                .get("defaultAgent")
                .and_then(Value::as_str)
                .unwrap_or(DEFAULT_AGENT)
                .to_owned(),
            agents: runtime
                .get("agents")
                .and_then(Value::as_array)
                .map(|agents| {
                    agents
                        .iter()
                        .filter_map(|agent| serde_json::from_value(agent.clone()).ok())
                        .collect()
                })
                .unwrap_or_else(builtin_agents),
            skills: cached_skills(response),
            skills_count: runtime
                .get("skills")
                .and_then(Value::as_array)
                .map_or(0, |skills| {
                    skills
                        .iter()
                        .filter(|skill| {
                            skill.get("source").and_then(Value::as_str) != Some("builtin")
                        })
                        .count()
                }),
            hooks_count: usize_at(runtime, "/hooksCount"),
            connectors_connected: usize_at(runtime, "/connectors/connected"),
            connectors_total: runtime
                .pointer("/connectors/total")
                .and_then(Value::as_u64)
                .and_then(|value| usize::try_from(value).ok())
                .unwrap_or(0),
            mcp_servers_enabled,
            mcp_servers_total,
            context_window: runtime
                .get("contextWindow")
                .and_then(Value::as_u64)
                .unwrap_or(0),
            vibe_code_enabled: config
                .get("vibeCodeEnabled")
                .and_then(Value::as_bool)
                .unwrap_or(true),
            narrator_enabled: config
                .get("narratorEnabled")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        })
    }
}

fn usize_at(value: &Value, pointer: &str) -> usize {
    value
        .pointer(pointer)
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .unwrap_or(0)
}

fn cache_file() -> Option<PathBuf> {
    paths::vibe_home().map(|home| home.join(FILE_NAME))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::server::AgentType;

    fn runtime(safety: &str) -> Value {
        serde_json::json!({
            "runtime": {
                "config": {
                    "theme": "ansi-dark",
                    "activeModel": {"displayName": "Medium", "thinking": "off"},
                    "defaultAgent": "plan",
                },
                "activeAgent": {"name": "ask", "displayName": "Ask", "safety": "neutral"},
                "agents": [
                    {"name": "ask", "displayName": "Ask", "safety": "neutral"},
                    {"name": "plan", "displayName": "Plan", "safety": safety},
                    {"name": "explore", "displayName": "Explore", "agentType": "subagent"},
                ],
            }
        })
    }

    #[test]
    fn projects_the_default_agent_and_the_cycleable_list() {
        let config = StartupConfig::from_runtime("1.0.0", &runtime("safe")).unwrap();
        assert_eq!(config.default_agent, "plan");
        assert_eq!(config.agents.len(), 3);
        assert_eq!(config.agents[2].agent_type, AgentType::Subagent);
    }

    #[test]
    fn an_older_runtime_without_default_agent_uses_the_builtin_default() {
        let mut runtime = runtime("safe");
        runtime["runtime"]["config"]
            .as_object_mut()
            .unwrap()
            .remove("defaultAgent");
        let config = StartupConfig::from_runtime("1.0.0", &runtime).unwrap();
        assert_eq!(config.default_agent, DEFAULT_AGENT);
    }

    #[test]
    fn with_no_cache_the_builtins_are_cycleable() {
        let config = StartupConfig::default();
        assert_eq!(config.default_agent, DEFAULT_AGENT);
        assert_eq!(config.agents.len(), 4);
    }

    #[test]
    fn round_trips_through_the_cache_file_format() {
        let config = StartupConfig::from_runtime("1.0.0", &runtime("destructive")).unwrap();
        let raw = serde_json::to_string(&config).unwrap();
        assert!(raw.contains("\"defaultAgent\":\"plan\""));
        assert!(!raw.contains("activeAgent"));
        assert_eq!(serde_json::from_str::<StartupConfig>(&raw).unwrap(), config);
    }
}
