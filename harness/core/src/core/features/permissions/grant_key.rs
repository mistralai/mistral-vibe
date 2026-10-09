use crate::core::features::permissions::{PermissionPolicy, public_arguments::PublicArguments};
use crate::core::tools::external::ExternalToolCall;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct GrantKey {
    key: String,
}

const GRANT_KEY_DOMAIN: &[u8] = b"mistral-harness/permission-grant/v1\0";

impl GrantKey {
    pub(crate) fn derive(policy: &PermissionPolicy, call: &ExternalToolCall) -> Self {
        let policy =
            serde_json::to_vec(policy).expect("serializing a permission policy cannot fail");
        let stable_tool_id = call.hook_tool_key().qualified_name;
        let arguments = PublicArguments::from_call(call).to_canonical_json();

        let mut input = GRANT_KEY_DOMAIN.to_vec();
        Self::append_component(&mut input, &policy);
        Self::append_component(&mut input, stable_tool_id.as_bytes());
        Self::append_component(&mut input, &arguments);

        Self {
            key: sha256::digest(input),
        }
    }

    pub(crate) fn as_str(&self) -> &str {
        &self.key
    }

    fn append_component(target: &mut Vec<u8>, value: &[u8]) {
        let length = u64::try_from(value.len()).expect("grant key component length fits in u64");
        target.extend_from_slice(&length.to_be_bytes());
        target.extend_from_slice(value);
    }
}

impl TryFrom<String> for GrantKey {
    type Error = String;

    fn try_from(key: String) -> Result<Self, Self::Error> {
        let is_lowercase_sha256 = key.len() == 64
            && key
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase());

        if !is_lowercase_sha256 {
            return Err(
                "permission grant key must contain 64 lowercase hexadecimal characters".to_string(),
            );
        }

        Ok(Self { key })
    }
}

#[cfg(test)]
mod tests {
    use serde_json::{Value, json};

    use super::GrantKey;
    use crate::core::features::permissions::PermissionPolicy;
    use crate::core::tools::external::{
        ExternalTool, ExternalToolCall, RuntimeBuiltinToolName, ToolOrigin,
    };

    fn permission_policy(default: &str) -> PermissionPolicy {
        serde_json::from_value(json!({
            "default": default,
            "rules": [{
                "tools": ["file_system.bash"],
                "decision": "ask",
                "allowlist": [],
                "denylist": ["*rm -rf*"],
                "sensitive": ["*sudo*"]
            }]
        }))
        .unwrap()
    }

    fn derive_grant_key(
        policy: &PermissionPolicy,
        name: RuntimeBuiltinToolName,
        invocation_name: &str,
        arguments: Value,
    ) -> GrantKey {
        let call = ExternalToolCall {
            action_id: "test-action".to_string(),
            call_id: "test-call".to_string(),
            origin: ToolOrigin::TopLevel,
            call: ExternalTool::RuntimeBuiltin {
                name,
                invocation_name: invocation_name.to_string(),
                arguments,
            },
        };

        GrantKey::derive(policy, &call)
    }

    #[test]
    fn derives_deterministic_grant_key_from_policy_tool_and_arguments() {
        let policy = permission_policy("deny");
        let key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            json!({"timeout": 30, "command": "git status"}),
        );

        assert_eq!(
            key.as_str(),
            "8681b321bca728b177b94dd4185c24be7a9b0bda09de733430af83eff1e83d73"
        );
    }

    #[test]
    fn preserves_grant_key_when_argument_object_keys_are_reordered() {
        let policy = permission_policy("deny");
        let first_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            serde_json::from_str(
                r#"{"command":"git status","options":{"timeout":30,"cwd":"src"}}"#,
            )
            .unwrap(),
        );
        let reordered_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            serde_json::from_str(
                r#"{"options":{"cwd":"src","timeout":30},"command":"git status"}"#,
            )
            .unwrap(),
        );

        assert_eq!(first_key.as_str(), reordered_key.as_str());
    }

    #[test]
    fn changes_grant_key_when_policy_changes() {
        let first_policy = permission_policy("deny");
        let changed_policy = permission_policy("allow");
        let first_key = derive_grant_key(
            &first_policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            json!({"command": "git status"}),
        );
        let changed_key = derive_grant_key(
            &changed_policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            json!({"command": "git status"}),
        );

        assert_ne!(first_key.as_str(), changed_key.as_str());
    }

    #[test]
    fn changes_grant_key_when_stable_tool_id_changes() {
        let policy = permission_policy("deny");
        let bash_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            json!({"path": "README.md"}),
        );
        let read_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemReadFile,
            "read_file",
            json!({"path": "README.md"}),
        );

        assert_ne!(bash_key.as_str(), read_key.as_str());
    }

    #[test]
    fn changes_grant_key_when_arguments_change() {
        let policy = permission_policy("deny");
        let status_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            json!({"command": "git status"}),
        );
        let diff_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            json!({"command": "git diff"}),
        );

        assert_ne!(status_key.as_str(), diff_key.as_str());
    }

    #[test]
    fn changes_grant_key_when_search_replace_block_order_changes() {
        let policy = permission_policy("deny");
        let first_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemSearchReplace,
            "edit",
            json!({
                "file_path": "src/lib.rs",
                "content": [
                    {"old_str": "first", "new_str": "one", "replace_all": false},
                    {"old_str": "second", "new_str": "two", "replace_all": false}
                ]
            }),
        );
        let reordered_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemSearchReplace,
            "edit",
            json!({
                "file_path": "src/lib.rs",
                "content": [
                    {"old_str": "second", "new_str": "two", "replace_all": false},
                    {"old_str": "first", "new_str": "one", "replace_all": false}
                ]
            }),
        );

        assert_ne!(first_key.as_str(), reordered_key.as_str());
    }

    #[test]
    fn preserves_grant_key_when_model_visible_name_changes() {
        let policy = permission_policy("deny");
        let first_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "bash",
            json!({"command": "git status"}),
        );
        let renamed_key = derive_grant_key(
            &policy,
            RuntimeBuiltinToolName::FileSystemBash,
            "terminal",
            json!({"command": "git status"}),
        );

        assert_eq!(first_key.as_str(), renamed_key.as_str());
    }

    #[test]
    fn rejects_malformed_grant_key() {
        let error = GrantKey::try_from("not-a-sha256-digest".to_string()).unwrap_err();

        assert_eq!(
            error,
            "permission grant key must contain 64 lowercase hexadecimal characters"
        );
    }
}
