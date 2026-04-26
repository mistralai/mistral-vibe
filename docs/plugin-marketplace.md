# Plugins & Marketplaces

Plugins extend Vibe with installable bundles of skills, agents, tools, markdown-backed commands, and optional MCP server definitions.

## Quick Start

```bash
# Add a marketplace (one-time setup)
vibe plugin marketplace add anthropics/claude-code

# Search for plugins
vibe plugin search security

# Install a plugin by name
vibe plugin install security-guidance

# List installed plugins
vibe plugin list
```

## Managing Marketplaces

Marketplaces are git repositories that index multiple plugins. Once a marketplace
is added, you can install any of its plugins by name — no URL required.

```bash
# Add a marketplace
vibe plugin marketplace add https://github.com/anthropics/claude-code
vibe plugin marketplace add owner/repo --name "My Marketplace"

# List configured marketplaces
vibe plugin marketplace list

# Re-fetch marketplace caches
vibe plugin marketplace update
vibe plugin marketplace update "My Marketplace"

# Remove a marketplace
vibe plugin marketplace remove "My Marketplace"
```

Marketplace configurations are stored in your `config.toml`:

```toml
[[plugin_marketplaces]]
url = "https://github.com/anthropics/claude-code"
name = "claude-code-plugins"
```

## Installing Plugins

### From a Marketplace (Recommended)

After adding a marketplace, install plugins by name:

```bash
# Search across all configured marketplaces
vibe plugin search security

# Install by name (searches configured marketplaces automatically)
vibe plugin install security-guidance
```

### From Git

```bash
# Install from a GitHub repository
vibe plugin install https://github.com/<owner>/<repo>

# Pin to a specific branch or tag
vibe plugin install https://github.com/<owner>/<repo> --ref <branch>

# Install from a subdirectory (monorepo)
vibe plugin install https://github.com/<owner>/<repo>/tree/<branch>/<path>
```

### From a Local Directory

```bash
# Install from a local directory (creates a symlink)
vibe plugin install ./my-plugin --local
```

### Install Resolution Order

`vibe plugin install` resolves the source argument in this order:

1. **`--local` flag** — symlink from a local directory.
2. **`--marketplace` flag** — fetch from the specified marketplace URL.
3. **URL containing `/`** — if the URL points to a marketplace repository
   (contains a marketplace index), it is automatically added as a marketplace.
   Otherwise it is cloned as a single-plugin git install.
4. **Bare name** (no `/`) — searches all configured marketplaces and installs
   the first match. If no match is found, an error is displayed.

## Managing Plugins

```bash
# List installed plugins
vibe plugin list

# Show detailed info
vibe plugin info <plugin-name>

# Enable or disable a plugin
vibe plugin enable <plugin-name>
vibe plugin disable <plugin-name>

# Update git-sourced plugins
vibe plugin update           # update all
vibe plugin update <name>    # update one

# Remove a plugin
vibe plugin remove <plugin-name>
```

## Plugin Scopes

Plugins can be installed in multiple scopes:

| Scope | Location | Flag |
|-------|----------|------|
| `user` (default) | `~/.vibe/plugins/` | `--scope user` |
| `project` | `<repo>/.vibe/plugins/` | `--scope project` |
| `local` | `<repo>/.vibe/local-plugins/` | `--scope local` |

Installed plugin state is tracked in a `registry.toml` file within the scope directory.
If the same plugin name exists in multiple scopes, Vibe resolves using this precedence:

**user > local > project**

Project and local scopes require running Vibe inside a trusted project directory.

## Plugin Layout

Each plugin contains a manifest file (`plugin.toml` or `plugin.json`) and conventional directories:

```
my-plugin/
├── plugin.toml          # or plugin.json
├── skills/
│   └── greet/
│       └── SKILL.md     # Skill definition
├── agents/
│   └── concise.toml     # Agent configuration
├── tools/
│   └── word_count.py    # Python tool
├── commands/
│   └── review.md        # Slash command (/my-plugin:review)
└── .mcp.json            # Optional MCP server definitions
```

### Manifest Format (TOML)

```toml
name = "my-plugin"
version = "1.0.0"
description = "A sample plugin"
author = "Your Name"
```

### Manifest Format (JSON)

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "A sample plugin",
  "author": "Your Name"
}
```

### MCP Server Definitions

Plugins can include MCP servers via the `.mcp.json` file:

```json
{
  "mcpServers": {
    "my-server": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "my-mcp-server"]
    }
  }
}
```

## Using Plugin Features in Chat

Enabled plugins are automatically discovered at startup:

- Skills, agents, and tools become available to the assistant.
- Markdown files in `commands/` become slash commands: `/plugin-name:command-name`
- MCP servers are merged from manifest entries and `.mcp.json`.

Only enabled plugins contribute features. Use `/plugin` in chat to manage plugins interactively, or `/reload-plugins` after making changes during development.

## Cross-Platform Compatibility

Vibe discovers manifests in these locations (in order):

1. Root directory (`plugin.toml` / `plugin.json`)
2. `.github/plugin/` (GitHub Copilot plugin format)
3. `.claude-plugin/` (Claude Code plugin format)

This enables installing plugins from other ecosystems:

```bash
# Copilot plugin from a monorepo
vibe plugin install https://github.com/github/awesome-copilot/tree/main/plugins/awesome-copilot/.github/plugin

# Claude Code plugin from a monorepo
vibe plugin install https://github.com/anthropics/claude-code/tree/main/plugins/feature-dev
```

## Developing Plugins Locally

For development, load a plugin directory without installing it:

```bash
vibe --plugin-dir /path/to/plugin
```

- `--plugin-dir` can be specified multiple times.
- Dev-loaded plugins override installed plugins with the same name for that session.
- Use `/reload-plugins` after making changes during development.

## Creating a Marketplace

A marketplace is a git repository with a `marketplace.toml` or `marketplace.json` index:

### marketplace.toml

```toml
name = "my-marketplace"
description = "A collection of useful plugins"
version = "1.0.0"

[[plugins]]
name = "my-plugin"
source = "plugins/my-plugin"
description = "Does something useful"
version = "1.0.0"

[[plugins]]
name = "another-plugin"
source = "plugins/another-plugin"
description = "Does something else"
version = "2.0.0"
```

### marketplace.json

```json
{
  "name": "my-marketplace",
  "description": "A collection of useful plugins",
  "version": "1.0.0",
  "plugins": [
    {
      "name": "my-plugin",
      "source": "plugins/my-plugin",
      "description": "Does something useful",
      "version": "1.0.0"
    }
  ]
}
```

Each `source` field points to a subdirectory within the marketplace repo that contains a valid plugin (with its own `plugin.toml` or `plugin.json`).
