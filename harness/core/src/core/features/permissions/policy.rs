use serde::{Deserialize, Serialize, Serializer};

use crate::core::features::permissions::public_arguments::{
    FilterTexts, PublicArguments, mixes_separator_spellings,
};
use crate::core::tools::external::ExternalToolCall;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionDecision {
    Allow,
    Deny,
    Ask,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(try_from = "PermissionPolicySource")]
pub(crate) struct PermissionPolicy {
    pub(super) default: PermissionDecision,
    pub(super) rules: Vec<PermissionRule>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
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

    pub(super) fn for_authoring(
        source: &str,
        field: PermissionPatternField,
    ) -> Result<Self, String> {
        let pattern = Self::new(source).map_err(|error| format!("is not a valid glob: {error}"))?;

        if field == PermissionPatternField::Arguments && mixes_separator_spellings(source) {
            return Err(
                "has a space after some JSON ':' or ',' separators but not after others, \
                 so it matches no arguments. Use one spelling for the whole pattern"
                    .to_owned(),
            );
        }

        Ok(pattern)
    }

    pub(super) fn matches(&self, value: &str) -> bool {
        self.pattern.matches(value)
    }

    fn matches_arguments(&self, texts: &FilterTexts) -> bool {
        self.matches(&texts.spaced) || self.matches(&texts.compact)
    }
}

/// The field of a permission rule that a pattern belongs to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PermissionPatternField {
    /// `tools`, matched against stable tool IDs.
    Tools,
    /// `allowlist`, `denylist` or `sensitive`, matched against the filter
    /// texts of the call's arguments.
    Arguments,
}

/// Checks one pattern before a Session reads it. An error is the rest of a
/// sentence that starts with the pattern, so the caller can name the rule and
/// field first.
///
/// Policy loading only rejects an invalid glob. This check also rejects an
/// argument pattern that mixes the two separator spellings, which loads but
/// matches no arguments.
pub(crate) fn validate_permission_pattern(
    source: &str,
    field: PermissionPatternField,
) -> Result<(), String> {
    PermissionPattern::for_authoring(source, field).map(|_| ())
}

impl Serialize for PermissionPattern {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.pattern.as_str())
    }
}

/// Wire form of [`PermissionPolicy`]. Patterns are compiled after decoding so
/// that an invalid pattern error can name its rule and field.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PermissionPolicySource {
    default: PermissionDecision,
    rules: Vec<PermissionRuleSource>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PermissionRuleSource {
    tools: Vec<String>,
    decision: PermissionDecision,
    allowlist: Vec<String>,
    denylist: Vec<String>,
    sensitive: Vec<String>,
}

impl TryFrom<PermissionPolicySource> for PermissionPolicy {
    type Error = String;

    fn try_from(source: PermissionPolicySource) -> Result<Self, Self::Error> {
        let rules = source
            .rules
            .into_iter()
            .enumerate()
            .map(|(index, rule)| PermissionRule::compile(index, rule))
            .collect::<Result<_, _>>()?;
        Ok(Self {
            default: source.default,
            rules,
        })
    }
}

impl PermissionRule {
    fn compile(index: usize, source: PermissionRuleSource) -> Result<Self, String> {
        let compile_field = |field: &str, patterns: &[String]| {
            patterns
                .iter()
                .map(|pattern| {
                    PermissionPattern::new(pattern).map_err(|error| {
                        format!(
                            "permission rule {index} for tools {tools:?}: invalid {field} \
                             pattern {pattern:?}: {error}",
                            tools = source.tools,
                        )
                    })
                })
                .collect::<Result<Vec<_>, _>>()
        };
        Ok(Self {
            tools: compile_field("tools", &source.tools)?,
            decision: source.decision,
            allowlist: compile_field("allowlist", &source.allowlist)?,
            denylist: compile_field("denylist", &source.denylist)?,
            sensitive: compile_field("sensitive", &source.sensitive)?,
        })
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

        let filter_texts = PublicArguments::from_call(call).to_filter_texts();

        if rule
            .denylist
            .iter()
            .any(|pattern| pattern.matches_arguments(&filter_texts))
        {
            return PermissionDecision::Deny;
        }

        if rule
            .sensitive
            .iter()
            .any(|pattern| pattern.matches_arguments(&filter_texts))
        {
            return PermissionDecision::Ask;
        }

        if !rule.allowlist.is_empty() {
            return if rule
                .allowlist
                .iter()
                .any(|pattern| pattern.matches_arguments(&filter_texts))
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
