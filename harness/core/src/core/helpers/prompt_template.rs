//! Closed-token text substitution; templates cannot access data or execute expressions.

use crate::core::error::CoreError;

pub(crate) fn validate_template(
    template: &str,
    tokens: &[&str],
    path: &'static str,
) -> Result<(), CoreError> {
    if template.trim().is_empty() {
        return Err(CoreError::invalid_configuration(
            path,
            "prompt must not be empty",
        ));
    }
    let values = tokens.iter().map(|token| (*token, "")).collect::<Vec<_>>();
    let (_, used_tokens) = render_with_tokens(template, &values)
        .map_err(|detail| CoreError::invalid_configuration(path, detail))?;
    for token in tokens {
        if !used_tokens.contains(token) {
            return Err(CoreError::invalid_configuration(
                path,
                format!("prompt must contain {{{token}}}"),
            ));
        }
    }
    Ok(())
}

pub(crate) fn render_template(template: &str, values: &[(&str, &str)]) -> Result<String, String> {
    render_with_tokens(template, values).map(|(text, _)| text)
}

fn render_with_tokens<'a>(
    template: &'a str,
    values: &[(&str, &str)],
) -> Result<(String, Vec<&'a str>), String> {
    let mut result = String::new();
    let mut used_tokens = Vec::new();
    let mut rest = template;
    while let Some(index) = rest.find(['{', '}']) {
        result.push_str(&rest[..index]);
        rest = &rest[index..];
        if rest.starts_with("{{") || rest.starts_with("}}") {
            result.push(rest.chars().next().expect("matched a brace"));
            rest = &rest[2..];
            continue;
        }
        if !rest.starts_with('{') {
            return Err("unmatched closing brace in prompt".into());
        }
        let Some(end) = rest.find('}') else {
            return Err("unclosed token in prompt".into());
        };
        let token = &rest[1..end];
        let Some((_, value)) = values.iter().find(|(name, _)| *name == token) else {
            return Err(format!("unsupported prompt token {{{token}}}"));
        };
        result.push_str(value);
        used_tokens.push(token);
        rest = &rest[end + 1..];
    }
    result.push_str(rest);
    Ok((result, used_tokens))
}
