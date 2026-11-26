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
    let max_len = left.chars().count().max(right.chars().count());
    1.0 - edit_distance(&left, &right) as f64 / max_len as f64
}

fn normalize_for_similarity(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .map(|character| character.to_ascii_lowercase())
        .collect()
}

/// Levenshtein distance over Unicode scalar values. Normalized similarity
/// values are ASCII, so for them this counts bytes and scalar values alike.
pub(crate) fn edit_distance(left: &str, right: &str) -> usize {
    let right: Vec<char> = right.chars().collect();
    let mut previous: Vec<usize> = (0..=right.len()).collect();
    for (row, left_char) in left.chars().enumerate() {
        let mut current = vec![row + 1];
        for (column, right_char) in right.iter().enumerate() {
            let substitution = previous[column] + usize::from(left_char != *right_char);
            current.push(
                substitution
                    .min(previous[column + 1] + 1)
                    .min(current[column] + 1),
            );
        }
        previous = current;
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
