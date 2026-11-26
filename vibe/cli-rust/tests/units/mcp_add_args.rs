//! `/mcp add` slash arguments: forwarded options and parser rejections.
use vibe_rs::mcp::add_args::{parse_add_args, MCP_ADD_USAGE};

#[test]
fn defaults_to_streamable_http_oauth_with_login() {
    let args = parse_add_args("https://mcp.linear.app/mcp").unwrap();
    assert_eq!(args.url, "https://mcp.linear.app/mcp");
    assert_eq!(args.name, None);
    assert!(args.scopes.is_empty());
    assert_eq!(args.transport, "streamable-http");
    assert!(args.login);
    assert!(!args.allow_insecure_http);
}

#[test]
fn keeps_alias_repeated_scopes_transport_and_no_login() {
    let args = parse_add_args(
        "https://mcp.example.com/mcp --name docs --scope read --scope 'write all' --transport http --no-login",
    )
    .unwrap();
    assert_eq!(args.url, "https://mcp.example.com/mcp");
    assert_eq!(args.name.as_deref(), Some("docs"));
    assert_eq!(args.scopes, ["read", "write all"]);
    assert_eq!(args.transport, "http");
    assert!(!args.login);
}

#[test]
fn allow_insecure_http_opts_a_lan_url_in() {
    let args = parse_add_args("http://192.168.0.8:3002/mcp --transport http --allow-insecure-http")
        .unwrap();
    assert_eq!(args.url, "http://192.168.0.8:3002/mcp");
    assert!(args.allow_insecure_http);
}

#[test]
fn rejects_invalid_arguments_with_their_message() {
    for (raw, message) in [
        ("", MCP_ADD_USAGE),
        ("https://mcp.example.com/mcp extra", MCP_ADD_USAGE),
        (
            "https://mcp.example.com/mcp --unknown",
            "Unknown /mcp add option: --unknown",
        ),
        (
            "https://mcp.example.com/mcp --login",
            "Unknown /mcp add option: --login",
        ),
        (
            "https://mcp.example.com/mcp --name",
            "Usage: /mcp add --name <alias>",
        ),
        (
            "https://mcp.example.com/mcp --name a --name b",
            "Usage: /mcp add accepts --name only once.",
        ),
        (
            "https://mcp.example.com/mcp --scope --no-login",
            "Usage: /mcp add --scope <scope>",
        ),
        (
            "https://mcp.example.com/mcp --transport",
            "Usage: /mcp add --transport <http|streamable-http>",
        ),
        (
            "https://mcp.example.com/mcp --transport sse",
            "Unsupported MCP transport: sse. Use http or streamable-http.",
        ),
        (
            "https://mcp.example.com/mcp --transport http --transport streamable-http",
            "Usage: /mcp add accepts --transport only once.",
        ),
        (
            "'unterminated",
            "Invalid /mcp add arguments: No closing quotation",
        ),
    ] {
        assert_eq!(parse_add_args(raw).unwrap_err(), message, "{raw:?}");
    }
}
