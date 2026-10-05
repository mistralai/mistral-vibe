//! The `/status` "Model & Provider" section renderer and read fallback.

use serde_json::json;

use vibe_rs::commands::provider_auth::{attach, literal, render_provider_auth_section};

fn view(model: &str, provider: &str, api_base: Option<&str>) -> serde_json::Value {
    let mut view = json!({
        "modelDisplayName": model,
        "providerName": provider,
    });
    if let Some(base) = api_base {
        view["apiBase"] = json!(base);
    }
    view
}

#[test]
fn renders_model_provider_and_api_base() {
    let text = render_provider_auth_section(&view(
        "Claude Sonnet",
        "anthropic",
        Some("https://api.anthropic.com/v1"),
    ));
    assert_eq!(
        text,
        "## Model & Provider\n\n\
         - **Model**: Claude Sonnet\n\
         - **Provider**: anthropic\n\
         - **API base**: https\\://api\\.anthropic\\.com/v1"
    );
}

#[test]
fn invalid_api_base_without_displayable_value() {
    let text = render_provider_auth_section(&view("Claude Sonnet", "anthropic", None));
    assert!(text.contains("- **API base**: Invalid API URL"));
}

#[test]
fn dynamic_values_render_literally_not_as_markup() {
    let text = render_provider_auth_section(&view(
        "Model [bold]",
        "not-a-tag",
        Some("https://api.example.com/v1"),
    ));
    assert!(text.contains("- **Model**: Model \\[bold\\]"));
    assert!(text.contains("- **Provider**: not-a-tag"));
}

#[test]
fn strikethrough_pairs_render_literally() {
    let text = render_provider_auth_section(&view("Model ~~x~~", "anthropic", None));
    assert!(text.contains("- **Model**: Model \\~\\~x\\~\\~"));
}

#[test]
fn control_characters_render_as_one_line_and_a_space() {
    // A newline must not start a new markdown line; U+0085 (C1) is a control
    // character too and must not survive into the rendered line.
    let injected = render_provider_auth_section(&view("Bad\n- **injected**", "anthropic", None));
    assert!(injected.contains("- **Model**: Bad - \\*\\*injected\\*\\*"));
    assert!(!injected.contains("\n- **injected**"));

    let nel = render_provider_auth_section(&view("Bad\u{85}Name", "anthropic", None));
    assert!(nel.contains("- **Model**: Bad Name"));
}

#[test]
fn escaped_values_leave_nothing_to_structure() {
    // The escaper's safety depends on the parser's feature set, so this pins
    // the parser the UI renders with: a value carrying every construct it
    // understands must parse to one plain paragraph, not structure.
    let hostile = "*em* **strong** `code` [link](https://evil.example) \
                   ![image](https://evil.example) ~~struck~~ <b> &amp; #hash \
                   www.evil.example https://evil.example ftp://evil.example \
                   user@evil.example back\\_slash";
    let blocks = vibe_rs::ui::markdown::parse(&format!("prefix {}", literal(hostile)));
    let [block] = &blocks[..] else {
        panic!("expected one block, got {}", blocks.len());
    };
    let vibe_rs::ui::markdown::Block::Paragraph(inline) = block else {
        panic!("expected a paragraph");
    };
    let rendered: String = inline.iter().map(|(c, _, _)| *c).collect();
    assert_eq!(rendered, format!("prefix {hostile}"));
    assert!(
        inline
            .iter()
            .all(|(_, style, _)| style.add_modifier.is_empty()),
        "structured styling survived escaping"
    );
}

#[test]
fn failed_read_keeps_statistics_only() {
    let text = attach(
        "## Agent Statistics".to_owned(),
        Err(anyhow::anyhow!("connection closed")),
    );
    assert_eq!(text, "## Agent Statistics");
}

#[test]
fn response_without_auth_keeps_statistics_only() {
    let text = attach(
        "## Agent Statistics".to_owned(),
        Ok(json!({"unexpected": true})),
    );
    assert_eq!(text, "## Agent Statistics");
}

#[test]
fn successful_read_appends_the_section() {
    let text = attach(
        "## Agent Statistics".to_owned(),
        Ok(json!({"auth": {
            "modelDisplayName": "Claude Sonnet",
            "providerName": "anthropic",
            "apiBase": "https://api.anthropic.com/v1",
        }})),
    );
    assert!(text.starts_with("## Agent Statistics\n## Model & Provider"));
    assert!(text.contains("- **Model**: Claude Sonnet"));
}
