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

/// A `$VIBE_HOME/.env` path under the test process's real home, so the `~`
/// abbreviation is exercised without assuming a fixed home directory.
fn home_env_file_path() -> String {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .expect("HOME or USERPROFILE is set");
    format!("{home}/.vibe/.env")
}

fn auth_view(auth_kind: &str, sources: serde_json::Value) -> serde_json::Value {
    let mut view = view(
        "Claude Sonnet",
        "anthropic",
        Some("https://api.anthropic.com/v1"),
    );
    view["authKind"] = json!(auth_kind);
    view["apiKeyEnvVar"] = json!("ANTHROPIC_API_KEY");
    view["envFilePath"] = json!(home_env_file_path());
    view["sources"] = sources;
    view
}

fn source(kind: &str, state: &str) -> serde_json::Value {
    json!({"kind": kind, "state": state})
}

#[test]
fn single_in_use_source_renders_one_credential_line() {
    let text = render_provider_auth_section(&auth_view(
        "api_key",
        json!([source("os_keyring", "in_use")]),
    ));
    assert!(
        text.contains("- **Credential (ANTHROPIC\\_API\\_KEY)**: OS keyring"),
        "{text}"
    );
}

#[test]
fn multiple_sources_render_a_precedence_list() {
    let text = render_provider_auth_section(&auth_view(
        "api_key",
        json!([
            source("process_env", "in_use"),
            source("vibe_home_env_file", "available"),
            source("os_keyring", "available"),
        ]),
    ));
    assert!(
        text.contains(
            "- **Credential (ANTHROPIC\\_API\\_KEY)**:\n  1. Process environment · currently in use\n  2. ~/\\.vibe/\\.env\n  3. OS keyring"
        ),
        "{text}"
    );
}

#[test]
fn unavailable_source_keeps_its_marker() {
    let text = render_provider_auth_section(&auth_view(
        "api_key",
        json!([
            source("process_env", "in_use"),
            source("vibe_home_env_file", "unavailable"),
        ]),
    ));
    assert!(
        text.contains("  2. ~/\\.vibe/\\.env · unavailable"),
        "{text}"
    );
}

#[test]
fn single_unavailable_source_renders_as_a_one_entry_list() {
    let text = render_provider_auth_section(&auth_view(
        "api_key",
        json!([source("os_keyring", "unavailable")]),
    ));
    assert!(text.contains("  1. OS keyring · unavailable"), "{text}");
}

#[test]
fn unknown_source_kind_renders_literally() {
    // The server may grow a source kind this client does not know; the raw
    // value is shown rather than dropped or mislabeled.
    let text = render_provider_auth_section(&auth_view(
        "api_key",
        json!([source("mystery_source", "available")]),
    ));
    assert!(
        text.contains("- **Credential (ANTHROPIC\\_API\\_KEY)**:\n  1. mystery\\_source"),
        "{text}"
    );
}

#[test]
fn no_source_available() {
    let text = render_provider_auth_section(&auth_view("api_key", json!([])));
    assert!(
        text.contains("- **Credential (ANTHROPIC\\_API\\_KEY)**: no source available"),
        "{text}"
    );
}

#[test]
fn none_auth_omits_the_credential_line() {
    let mut view = view("Devstral", "llamacpp", Some("http://127.0.0.1:8080/v1"));
    view["authKind"] = json!("none");
    let text = render_provider_auth_section(&view);
    assert!(!text.contains("Credential"), "{text}");
}

#[test]
fn missing_auth_kind_omits_the_credential_line() {
    // An older server's view carries no `authKind`; the section degrades to
    // the increment-1 shape rather than guessing a source list.
    let text = render_provider_auth_section(&view("Claude Sonnet", "anthropic", None));
    assert!(!text.contains("Credential"), "{text}");
}

#[test]
fn google_adc_resolved_on_request() {
    let mut view = view("Claude Vertex", "vertex", None);
    view["authKind"] = json!("google_adc");
    let text = render_provider_auth_section(&view);
    assert!(
        text.contains(
            "- **Credential**: Google Application Default Credentials · resolved on request"
        ),
        "{text}"
    );
}

#[test]
fn env_file_path_outside_home_renders_the_absolute_path() {
    let mut view = auth_view("api_key", json!([source("vibe_home_env_file", "in_use")]));
    view["envFilePath"] = json!("/etc/vibe/.env");
    let text = render_provider_auth_section(&view);
    assert!(
        text.contains("- **Credential (ANTHROPIC\\_API\\_KEY)**: /etc/vibe/\\.env"),
        "{text}"
    );
}

#[test]
fn env_file_path_renders_literally() {
    // `VIBE_HOME` is user-controlled: markdown punctuation in the path must
    // reach the screen as those characters, not as injected structure.
    let mut view = auth_view("api_key", json!([source("vibe_home_env_file", "in_use")]));
    view["envFilePath"] = json!("/tmp/[x](https://evil)/.env");
    let text = render_provider_auth_section(&view);
    assert!(
        text.contains(
            "- **Credential (ANTHROPIC\\_API\\_KEY)**: /tmp/\\[x\\](https\\://evil)/\\.env"
        ),
        "{text}"
    );
}
