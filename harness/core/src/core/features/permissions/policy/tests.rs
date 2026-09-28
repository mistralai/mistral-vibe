use rstest::rstest;

use crate::core::features::permissions::policy::{PermissionPattern, PermissionRule};
use crate::core::tools::external::{ExternalTool, RuntimeBuiltinToolName, ToolOrigin};

use super::*;

#[test]
fn invalid_permission_pattern_is_rejected() {
    assert!(PermissionPattern::new("[").is_err());
}

fn build_permission_pattern(source: &str) -> PermissionPattern {
    PermissionPattern::new(source).unwrap()
}

fn build_permission_rule(tool: &str, decision: PermissionDecision) -> PermissionRule {
    PermissionRule {
        tools: vec![build_permission_pattern(tool)],
        decision,
        allowlist: vec![],
        denylist: vec![],
        sensitive: vec![],
    }
}

fn build_direct_runtime_builtin_call(
    name: RuntimeBuiltinToolName,
    invocation_name: &str,
    arguments: serde_json::Value,
) -> ExternalToolCall {
    ExternalToolCall {
        action_id: "test-action".to_string(),
        call_id: "test-call".to_string(),
        origin: ToolOrigin::TopLevel,
        call: ExternalTool::RuntimeBuiltin {
            name,
            invocation_name: invocation_name.to_string(),
            arguments,
        },
    }
}

fn build_programmatic_runtime_builtin_call(
    name: RuntimeBuiltinToolName,
    invocation_name: &str,
) -> ExternalToolCall {
    ExternalToolCall {
        origin: ToolOrigin::Programmatic,
        ..build_direct_runtime_builtin_call(name, invocation_name, serde_json::Value::Null)
    }
}

fn build_provided_call(
    origin: ToolOrigin,
    group_name: &str,
    tool_name: &str,
    arguments: serde_json::Value,
) -> ExternalToolCall {
    ExternalToolCall {
        action_id: "test-action".to_string(),
        call_id: "test-call".to_string(),
        origin,
        call: ExternalTool::Provided {
            group_name: group_name.to_string(),
            tool_name: tool_name.to_string(),
            arguments,
        },
    }
}

fn build_bash_call(command: &str) -> ExternalToolCall {
    build_direct_runtime_builtin_call(
        RuntimeBuiltinToolName::FileSystemBash,
        "bash",
        serde_json::json!({ "command": command }),
    )
}

mod rule_selection {
    use super::*;

    #[test]
    fn uses_policy_default_if_unmatched_tool() {
        let policy_with_no_rules = PermissionPolicy {
            default: PermissionDecision::Deny,
            rules: vec![],
        };

        assert_eq!(
            policy_with_no_rules.evaluate(&build_direct_runtime_builtin_call(
                RuntimeBuiltinToolName::FileSystemWriteFile,
                "write_file",
                serde_json::Value::Null,
            )),
            PermissionDecision::Deny
        );
    }

    #[rstest]
    #[case::allow(PermissionDecision::Allow, PermissionDecision::Deny)]
    #[case::deny(PermissionDecision::Deny, PermissionDecision::Allow)]
    #[case::ask(PermissionDecision::Ask, PermissionDecision::Allow)]
    fn matching_rule_uses_its_decision(
        #[case] decision: PermissionDecision,
        #[case] default: PermissionDecision,
    ) {
        let call = build_direct_runtime_builtin_call(
            RuntimeBuiltinToolName::FileSystemReadFile,
            "read_file",
            serde_json::Value::Null,
        );
        let policy = PermissionPolicy {
            rules: vec![build_permission_rule("file_system.read_file", decision)],
            default,
        };

        assert_eq!(policy.evaluate(&call), decision);
    }

    #[test]
    fn matches_any_tool_pattern() {
        let mut rule = build_permission_rule("file_system.write_*", PermissionDecision::Allow);
        rule.tools
            .push(build_permission_pattern("file_system.read_*"));
        let policy = PermissionPolicy {
            rules: vec![rule],
            default: PermissionDecision::Deny,
        };

        assert_eq!(
            policy.evaluate(&build_direct_runtime_builtin_call(
                RuntimeBuiltinToolName::FileSystemReadFile,
                "read_file",
                serde_json::Value::Null,
            )),
            PermissionDecision::Allow
        );
    }

    #[rstest]
    #[case::top_level(ToolOrigin::TopLevel)]
    #[case::programmatic(ToolOrigin::Programmatic)]
    fn stable_provided_tool_id_matches_each_origin(#[case] origin: ToolOrigin) {
        let policy = PermissionPolicy {
            rules: vec![build_permission_rule(
                "linear.list_issues",
                PermissionDecision::Allow,
            )],
            default: PermissionDecision::Deny,
        };
        let call = build_provided_call(origin, "linear", "list_issues", serde_json::Value::Null);

        assert_eq!(policy.evaluate(&call), PermissionDecision::Allow);
    }

    #[test]
    fn model_visible_runtime_builtin_name_does_not_match() {
        let policy = PermissionPolicy {
            rules: vec![build_permission_rule(
                "read_file",
                PermissionDecision::Allow,
            )],
            default: PermissionDecision::Deny,
        };

        assert_eq!(
            policy.evaluate(&build_direct_runtime_builtin_call(
                RuntimeBuiltinToolName::FileSystemReadFile,
                "read_file",
                serde_json::Value::Null,
            )),
            PermissionDecision::Deny
        );
    }

    #[test]
    fn model_visible_provided_tool_name_does_not_match() {
        let policy = PermissionPolicy {
            rules: vec![build_permission_rule(
                "list_issues",
                PermissionDecision::Allow,
            )],
            default: PermissionDecision::Deny,
        };
        let call = build_provided_call(
            ToolOrigin::TopLevel,
            "linear",
            "list_issues",
            serde_json::Value::Null,
        );

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn programmatic_runtime_builtin_name_does_not_match() {
        let policy = PermissionPolicy {
            rules: vec![build_permission_rule("sleep", PermissionDecision::Allow)],
            default: PermissionDecision::Deny,
        };
        let call =
            build_programmatic_runtime_builtin_call(RuntimeBuiltinToolName::SelfSleep, "sleep");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn programmatic_provided_tool_name_does_not_match() {
        let policy = PermissionPolicy {
            rules: vec![build_permission_rule(
                "provided_tool::linear::list_issues",
                PermissionDecision::Allow,
            )],
            default: PermissionDecision::Deny,
        };
        let call = build_provided_call(
            ToolOrigin::Programmatic,
            "linear",
            "list_issues",
            serde_json::Value::Null,
        );

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn matches_the_first_matching_rule() {
        let policy = PermissionPolicy {
            rules: vec![
                build_permission_rule("file_system.read_*", PermissionDecision::Allow),
                build_permission_rule("file_system.read_file", PermissionDecision::Deny),
            ],
            default: PermissionDecision::Deny,
        };

        assert_eq!(
            policy.evaluate(&build_direct_runtime_builtin_call(
                RuntimeBuiltinToolName::FileSystemReadFile,
                "read_file",
                serde_json::Value::Null,
            )),
            PermissionDecision::Allow
        );
    }
}

mod argument_filters {
    use super::*;

    #[test]
    fn denylist_overrides_allow_decision() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                denylist: vec![
                    build_permission_pattern("*deploy staging*"),
                    build_permission_pattern(r#"*"command": "deploy production"*"#),
                ],
                ..build_permission_rule("file_system.bash", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Deny,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn denylist_matches_provided_tool_arguments() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                denylist: vec![build_permission_pattern(r#"*"state": "closed"*"#)],
                ..build_permission_rule("linear.list_issues", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Ask,
        };
        let call = build_provided_call(
            ToolOrigin::TopLevel,
            "linear",
            "list_issues",
            serde_json::json!({"state": "closed"}),
        );

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn denylist_preserves_rule_decision_when_it_does_not_match() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                denylist: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.bash", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Deny,
        };
        let call = build_bash_call("deploy staging");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Allow);
    }

    #[test]
    fn denylist_has_no_effect_on_unmatched_tool() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                denylist: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.read_file", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Ask,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Ask);
    }
    #[test]
    fn sensitive_overrides_allow_decision() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                sensitive: vec![
                    build_permission_pattern("*deploy staging*"),
                    build_permission_pattern("*production*"),
                ],
                ..build_permission_rule("file_system.bash", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Deny,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Ask);
    }

    #[test]
    fn sensitive_preserves_rule_decision_when_it_does_not_match() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                sensitive: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.bash", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Deny,
        };
        let call = build_bash_call("deploy staging");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Allow);
    }

    #[test]
    fn sensitive_has_no_effect_on_unmatched_tool() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                sensitive: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.read_file", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Deny,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn denylist_takes_precedence_when_both_match() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                denylist: vec![build_permission_pattern("*production*")],
                sensitive: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.bash", PermissionDecision::Allow)
            }],
            default: PermissionDecision::Ask,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn allowlist_match_overrides_deny_decision() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                allowlist: vec![
                    build_permission_pattern("*deploy staging*"),
                    build_permission_pattern("*deploy production*"),
                ],
                ..build_permission_rule("file_system.bash", PermissionDecision::Deny)
            }],
            default: PermissionDecision::Ask,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Allow);
    }

    #[rstest]
    #[case::allow(PermissionDecision::Allow)]
    #[case::deny(PermissionDecision::Deny)]
    #[case::ask(PermissionDecision::Ask)]
    fn allowlist_miss_returns_ask_regardless_of_rule_decision(
        #[case] decision: PermissionDecision,
    ) {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                allowlist: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.bash", decision)
            }],
            default: PermissionDecision::Deny,
        };
        let call = build_bash_call("deploy staging");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Ask);
    }

    #[test]
    fn denylist_takes_precedence_over_allowlist_when_both_match() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                denylist: vec![build_permission_pattern("*production*")],
                allowlist: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.bash", PermissionDecision::Ask)
            }],
            default: PermissionDecision::Allow,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Deny);
    }

    #[test]
    fn sensitive_takes_precedence_over_allowlist_when_both_match() {
        let policy = PermissionPolicy {
            rules: vec![PermissionRule {
                sensitive: vec![build_permission_pattern("*production*")],
                allowlist: vec![build_permission_pattern("*production*")],
                ..build_permission_rule("file_system.bash", PermissionDecision::Deny)
            }],
            default: PermissionDecision::Allow,
        };
        let call = build_bash_call("deploy production");

        assert_eq!(policy.evaluate(&call), PermissionDecision::Ask);
    }
}
