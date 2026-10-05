use serde::{Deserialize, Deserializer, Serialize, Serializer};

use crate::core::features::permissions::public_arguments::PublicArguments;
use crate::core::tools::external::ExternalToolCall;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionDecision {
    Allow,
    Deny,
    Ask,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct PermissionPolicy {
    pub(super) default: PermissionDecision,
    pub(super) rules: Vec<PermissionRule>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct PermissionRule {
    pub(super) tools: Vec<PermissionPattern>,
    pub(super) decision: PermissionDecision,
    pub(super) allowlist: Vec<PermissionPattern>,
    pub(super) denylist: Vec<PermissionPattern>,
    pub(super) sensitive: Vec<PermissionPattern>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct PermissionPattern {
    pattern: glob::Pattern,
}

impl PermissionPattern {
    pub(super) fn new(source: &str) -> Result<Self, glob::PatternError> {
        Ok(Self {
            pattern: glob::Pattern::new(source)?,
        })
    }

    pub(super) fn matches(&self, value: &str) -> bool {
        self.pattern.matches(value)
    }
}

impl Serialize for PermissionPattern {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.pattern.as_str())
    }
}

impl<'de> Deserialize<'de> for PermissionPattern {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let source = String::deserialize(deserializer)?;
        Self::new(&source).map_err(serde::de::Error::custom)
    }
}

impl PermissionPolicy {
    pub(crate) fn evaluate(&self, call: &ExternalToolCall) -> PermissionDecision {
        let stable_tool_id = call.hook_tool_key().qualified_name;

        let Some(rule) = self.rules.iter().find(|rule| {
            rule.tools
                .iter()
                .any(|pattern| pattern.matches(&stable_tool_id))
        }) else {
            return self.default;
        };

        if rule.denylist.is_empty() && rule.sensitive.is_empty() && rule.allowlist.is_empty() {
            return rule.decision;
        }

        let public_arguments = PublicArguments::from_call(call);
        let filter_text = public_arguments.to_filter_text();

        if rule
            .denylist
            .iter()
            .any(|pattern| pattern.matches(&filter_text))
        {
            return PermissionDecision::Deny;
        }

        if rule
            .sensitive
            .iter()
            .any(|pattern| pattern.matches(&filter_text))
        {
            return PermissionDecision::Ask;
        }

        if !rule.allowlist.is_empty() {
            return if rule
                .allowlist
                .iter()
                .any(|pattern| pattern.matches(&filter_text))
            {
                PermissionDecision::Allow
            } else {
                PermissionDecision::Ask
            };
        }

        rule.decision
    }
}

#[cfg(test)]
mod tests;
