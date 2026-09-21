//! `/mcp add` argument parsing (Python `parse_mcp_add_args`).

pub const MCP_ADD_USAGE: &str = "Usage: /mcp add <url> [--name <alias>] [--scope <scope> ...] [--transport <http|streamable-http>] [--no-login] [--allow-insecure-http]";

/// Long help for `/mcp add --help`, matching Python's `MCP_ADD_HELP`.
pub fn add_help() -> String {
    format!(
        "{MCP_ADD_USAGE}\n\nOAuth-only shortcut for hosted MCP servers.\nDefaults to streamable-http; pass --transport http for servers documented with\nHTTP transport.\nPass --allow-insecure-http to allow a plaintext http:// URL on a non-localhost\nhost, such as a server on the LAN.\nFor API-key/static auth, edit config.toml."
    )
}

/// Parsed `/mcp add` arguments (Python `MCPAddArgs`).
pub struct AddArgs {
    pub url: String,
    pub name: Option<String>,
    pub scopes: Vec<String>,
    pub transport: String,
    pub login: bool,
    pub allow_insecure_http: bool,
}

pub fn parse_add_args(raw_args: &str) -> Result<AddArgs, String> {
    let tokens = split_args(raw_args)?;
    let mut url: Option<String> = None;
    let mut name: Option<String> = None;
    let mut scopes: Vec<String> = Vec::new();
    let mut transport = "streamable-http".to_owned();
    let mut transport_seen = false;
    let mut login = true;
    let mut allow_insecure_http = false;
    let mut index = 0;
    while index < tokens.len() {
        match tokens[index].as_str() {
            "--no-login" => {
                login = false;
                index += 1;
            }
            "--allow-insecure-http" => {
                allow_insecure_http = true;
                index += 1;
            }
            "--name" => {
                if name.is_some() {
                    return Err("Usage: /mcp add accepts --name only once.".to_owned());
                }
                name = Some(option_value(&tokens, index, "--name", "<alias>")?);
                index += 2;
            }
            "--transport" => {
                if transport_seen {
                    return Err("Usage: /mcp add accepts --transport only once.".to_owned());
                }
                transport = parse_transport(&option_value(
                    &tokens,
                    index,
                    "--transport",
                    "<http|streamable-http>",
                )?)?;
                transport_seen = true;
                index += 2;
            }
            "--scope" => {
                scopes.push(option_value(&tokens, index, "--scope", "<scope>")?);
                index += 2;
            }
            token if token.starts_with("--") => {
                return Err(format!("Unknown /mcp add option: {token}"))
            }
            token => {
                if url.is_some() {
                    return Err(MCP_ADD_USAGE.to_owned());
                }
                url = Some(token.to_owned());
                index += 1;
            }
        }
    }
    Ok(AddArgs {
        url: url.ok_or_else(|| MCP_ADD_USAGE.to_owned())?,
        name,
        scopes,
        transport,
        login,
        allow_insecure_http,
    })
}

fn parse_transport(value: &str) -> Result<String, String> {
    match value {
        "http" | "streamable-http" => Ok(value.to_owned()),
        _ => Err(format!(
            "Unsupported MCP transport: {value}. Use http or streamable-http."
        )),
    }
}

fn option_value(
    tokens: &[String],
    index: usize,
    option: &str,
    placeholder: &str,
) -> Result<String, String> {
    match tokens.get(index + 1) {
        Some(value) if !value.starts_with("--") => Ok(value.clone()),
        _ => Err(format!("Usage: /mcp add {option} {placeholder}")),
    }
}

/// Whitespace split honoring single and double quotes (Python `shlex.split`).
fn split_args(raw_args: &str) -> Result<Vec<String>, String> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let mut started = false;
    let mut quote: Option<char> = None;
    for character in raw_args.chars() {
        match quote {
            Some(open) if character == open => quote = None,
            Some(_) => token.push(character),
            None if character == '\'' || character == '"' => {
                quote = Some(character);
                started = true;
            }
            None if character.is_whitespace() => {
                if started {
                    tokens.push(std::mem::take(&mut token));
                    started = false;
                }
            }
            None => {
                token.push(character);
                started = true;
            }
        }
    }
    if quote.is_some() {
        return Err("Invalid /mcp add arguments: No closing quotation".to_owned());
    }
    if started {
        tokens.push(token);
    }
    Ok(tokens)
}
