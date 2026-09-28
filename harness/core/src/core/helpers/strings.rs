pub(crate) fn normalized_similarity_score(left: &str, right: &str) -> f64 {
    let left = normalize_for_similarity(left);
    let right = normalize_for_similarity(right);
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }
    if left == right {
        return 1.0;
    }
    if left.contains(&right) || right.contains(&left) {
        return 0.9;
    }
    let max_len = left.len().max(right.len());
    1.0 - levenshtein_distance(&left, &right) as f64 / max_len as f64
}

fn normalize_for_similarity(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .map(|character| character.to_ascii_lowercase())
        .collect()
}

fn levenshtein_distance(left: &str, right: &str) -> usize {
    let mut previous = (0..=right.len()).collect::<Vec<_>>();
    for (left_index, left_byte) in left.bytes().enumerate() {
        let mut diagonal = previous[0];
        previous[0] = left_index + 1;
        for (right_index, right_byte) in right.bytes().enumerate() {
            let insert_cost = previous[right_index + 1] + 1;
            let delete_cost = previous[right_index] + 1;
            let replace_cost = diagonal + usize::from(left_byte != right_byte);
            diagonal = previous[right_index + 1];
            previous[right_index + 1] = insert_cost.min(delete_cost).min(replace_cost);
        }
    }
    previous[right.len()]
}

#[cfg(test)]
mod tests {
    use super::normalized_similarity_score;

    #[test]
    fn similarity_ignores_ascii_case_and_separators() {
        assert_eq!(normalized_similarity_score("RDE veille", "rde-veille"), 1.0);
    }

    #[test]
    fn similarity_prefers_contained_names() {
        assert_eq!(
            normalized_similarity_score("rde-veille-collecte", "rde-veille"),
            0.9
        );
    }

    #[test]
    fn similarity_rejects_empty_normalized_values() {
        assert_eq!(normalized_similarity_score("---", "rde-veille"), 0.0);
    }
}
