use uuid::Uuid;

const ACTION_ID_DOMAIN: &str = "mistral.ai/harness-core/action-id/v1";

pub(crate) fn completion(task_id: &str, compaction_count: u64, message_count: usize) -> String {
    let compaction_count = compaction_count.to_string();
    let message_count = message_count.to_string();
    derive("completion", &[task_id, &compaction_count, &message_count])
}

pub(crate) fn compaction(task_id: &str, compaction_count: u64) -> String {
    let compaction_count = compaction_count.to_string();
    derive("compaction", &[task_id, &compaction_count])
}

pub(crate) fn compaction_attempt(compaction_id: &str, attempt: u32) -> String {
    let attempt = attempt.to_string();
    derive("compaction_attempt", &[compaction_id, &attempt])
}

pub(crate) fn tool(origin: &str, operation_id: &str) -> String {
    derive("tool", &[origin, operation_id])
}

pub(crate) fn hook(subject_action_id: &str, point: &str) -> String {
    derive("hook", &[subject_action_id, point])
}

pub(crate) fn filesystem(operation: &str, origin: &str, operation_id: &str) -> String {
    derive("filesystem", &[operation, origin, operation_id])
}

fn derive(kind: &str, parts: &[&str]) -> String {
    let mut name = Vec::new();
    append_part(&mut name, ACTION_ID_DOMAIN);
    append_part(&mut name, kind);
    for part in parts {
        append_part(&mut name, part);
    }
    Uuid::new_v5(&Uuid::NAMESPACE_URL, &name).to_string()
}

fn append_part(target: &mut Vec<u8>, value: &str) {
    let length = u64::try_from(value.len()).expect("action identity part length fits in u64");
    target.extend_from_slice(&length.to_be_bytes());
    target.extend_from_slice(value.as_bytes());
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;

    #[test]
    fn action_ids_are_canonical_deterministic_and_domain_separated() {
        let ids = [
            completion("task", 0, 2),
            completion("task", 1, 2),
            compaction("task", 1),
            compaction_attempt(&compaction("task", 1), 2),
            tool("top_level", "call"),
            tool("programmatic", "call"),
            hook("subject", "pre_llm_call"),
            hook("subject", "post_llm_call"),
            filesystem("write", "direct", "call"),
            filesystem("write", "run_typescript", "call"),
        ];

        assert!(
            ids.iter().all(|id| {
                Uuid::parse_str(id).is_ok_and(|uuid| uuid.to_string() == id.as_str())
            })
        );
        assert_eq!(completion("task", 0, 2), completion("task", 0, 2));
        assert_eq!(ids.len(), ids.iter().collect::<HashSet<_>>().len());
    }

    #[test]
    fn length_prefixing_prevents_identity_part_aliases() {
        assert_ne!(derive("kind", &["a", "b:c"]), derive("kind", &["a:b", "c"]));
    }
}
