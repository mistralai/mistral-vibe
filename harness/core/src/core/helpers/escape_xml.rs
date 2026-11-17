//! Escape for embedding text in XML-ish prompt markup.
pub(crate) fn escape_xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

#[cfg(test)]
mod tests {
    use super::escape_xml;

    #[test]
    fn escapes_all_xml_special_characters() {
        assert_eq!(
            escape_xml(r#"a&b<c>d"e'f"#),
            r#"a&amp;b&lt;c&gt;d&quot;e&apos;f"#,
        );
    }
}
