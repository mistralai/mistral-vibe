use crate::core::features::permissions::public_arguments::PublicArguments;
use crate::core::tools::external::ExternalToolCall;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum PermissionDecision {
    Allow,
    Deny,
    Ask,
}

pub(super) struct PermissionPolicy {
    pub(super) default: PermissionDecision,
    pub(super) rules: Vec<PermissionRule>,
}

pub(super) struct PermissionRule {
    pub(super) tools: Vec<PermissionPattern>,
    pub(super) decision: PermissionDecision,
    pub(super) allowlist: Vec<PermissionPattern>,
    pub(super) denylist: Vec<PermissionPattern>,
    pub(super) sensitive: Vec<PermissionPattern>,
}

pub(super) type PermissionPattern = glob::Pattern;

impl PermissionPolicy {
    pub(super) fn evaluate(&self, call: &ExternalToolCall) -> PermissionDecision {
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
