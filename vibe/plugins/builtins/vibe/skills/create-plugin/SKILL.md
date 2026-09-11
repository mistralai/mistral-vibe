---
name: create-plugin
description: Create a Vibe plugin package — the Agent Plugins 1.0 format with optional Vibe extensions for skills, MCP servers, hooks, knowledge, agents, libraries, and connectors. Use when the user wants to create, scaffold, or author a plugin for Mistral Vibe.
---

# Create a Vibe Plugin

A plugin is a directory containing a `plugin.json` manifest (Agent Plugins 1.0
schema) plus optional component directories and config files. Vibe discovers
plugins under `.vibe/plugins/` (project scope, requires trusted folder) and
`~/.vibe/plugins/` (user global).

## Plugin discovery locations

| Scope | Path | When |
|---|---|---|
| Project | `<project>/.vibe/plugins/<name>/` | trusted folder only |
| User global | `~/.vibe/plugins/<name>/` | always |

Each subdirectory under a plugins root is one plugin package. The first match
wins: project plugins take precedence over user plugins with the same name.

## Plugin directory structure

```
my-plugin/
  plugin.json                 # required — Agent Plugins 1.0 manifest
  mcp.json                    # optional — MCP server definitions
  libraries.json              # optional — Node/Python library dependencies
  connectors.json             # optional — managed connector requirements
  skills/                     # optional — one subdirectory per skill
    my-skill/
      SKILL.md
  ai.mistral.vibe/            # optional — Vibe-specific extensions
    hooks.toml                # Vibe extension only
    knowledge/                # one subdirectory per knowledge folder
      topic-name/
        KNOWLEDGE.md
    agents/                   # one .toml per subagent
      researcher.toml
    INSTRUCTIONS.md           # optional plugin-wide instructions (not loaded)
```

## Step 1 — plugin.json (required)

The manifest is a JSON file with `$schema` and `name` as required fields.

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "What this plugin provides.",
  "author": {
    "name": "Author Name",
    "email": "author@example.com",
    "url": "https://example.com"
  },
  "homepage": "https://example.com/my-plugin",
  "repository": "https://github.com/example/my-plugin",
  "license": "MIT",
  "keywords": ["productivity", "finance"],
  "extensions": {}
}
```

Rules:
- `name` must match `^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$` (1-64 chars), no `--`
  or `..`.
- `$schema` must be exactly
  `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`. Any other
  schema URL produces a fatal `plugin.schema.version_unsupported` error.
- All fields other than those listed above are rejected (`extra="forbid"`).

## Step 2 — Vibe extension (optional)

To use Vibe-specific components (hooks, knowledge, agents, libraries,
connectors), add an `ai.mistral.vibe` extension to `plugin.json`:

```json
{
  "extensions": {
    "ai.mistral.vibe": {
      "schemaVersion": 1,
      "toolNamespace": "myPlugin",
      "toolOverrides": {
        "lookup": {
          "name": "search",
          "exposure": "direct_and_programmatic"
        }
      }
    }
  }
}
```

Without this extension, Vibe only loads `plugin.json`, `skills/`, and
`mcp.json`. The `toolNamespace` defaults to a TypeScript identifier derived
from the plugin name. It must not be one of the reserved namespaces:
`file_system`, `self`, `process`, `agent`, `vibe`.

`vibe` is reserved for built-in plugins shipped with the CLI; user plugins
must choose a different namespace.

`toolOverrides` rename or restrict tool exposure. Each key matches a discovered
tool name from MCP servers or connectors. `exposure` is one of
`programmatic`, `direct`, or `direct_and_programmatic`.

## Step 3 — Skills (optional)

Place skills under `skills/` — one subdirectory per skill, each containing a
`SKILL.md`. The skill name in `SKILL.md` frontmatter must match the directory
name.

```
skills/
  format-reports/
    SKILL.md
```

Skills follow the standard SKILL.md format with YAML frontmatter (`name`,
`description`, `user-invocable`, `allowed-tools`). Plugin skills are namespaced
as `<namespace>:<skill-name>` at runtime.

## Step 4 — MCP servers (optional)

Define MCP servers in `mcp.json`. Three transport types are supported:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "lookup": {
      "type": "stdio",
      "command": "node",
      "args": ["${PLUGIN_ROOT}/server/index.js"],
      "env": {
        "DEBUG": "true"
      },
      "cwd": "."
    },
    "remote-api": {
      "type": "streamable-http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "X-Custom-Header": "value"
      }
    }
  }
}
```

Rules:
- `command`, `args`, and `env` values support `${PLUGIN_ROOT}` and
  `${PLUGIN_DATA}` variable expansion.
- `PLUGIN_ROOT` and `PLUGIN_DATA` are also injected automatically into `env`;
  do not set them yourself (rejected with an error).
- `env` variable names `PLUGIN_ROOT` and `PLUGIN_DATA` are reserved.
- `cwd` of `.` means the plugin root.
- `streamable-http` servers support static `headers` only; use OAuth at the
  Vibe config level for authenticated remote servers.
- `sse` transport is detected but not supported — it produces a diagnostic.
- The `command` for stdio servers is resolved relative to the plugin root
  when it is a bare filename; absolute paths are used as-is.

## Step 5 — Hooks (optional, Vibe extension only)

Place hooks in `ai.mistral.vibe/hooks.toml`. Format is identical to the Vibe
hooks.toml format, but hooks run in the plugin root directory with
`PLUGIN_ROOT` and `PLUGIN_DATA` in the environment.

```toml
[[hooks]]
name = "guard-lookup"
type = "pre_tool"
match = "myPlugin.lookup"
command = "python guard.py"
strict = true

[[hooks]]
name = "post-agent-lint"
type = "post_agent"
command = "eslint --quiet ."
```

Limits:
- Maximum 128 hooks per plugin.
- File size limit: 64 KB.
- Hook names must be unique within the plugin. Runtime names are prefixed as
  `<plugin-name>:<hook-name>`.

## Step 6 — Knowledge (optional, Vibe extension only)

Place knowledge folders under `ai.mistral.vibe/knowledge/`. Each folder
contains a `KNOWLEDGE.md` with YAML frontmatter:

```
ai.mistral.vibe/
  knowledge/
    accounting-policy/
      KNOWLEDGE.md
      reference.md
```

`KNOWLEDGE.md` frontmatter:

```yaml
---
name: accounting-policy
description: How revenue is recognized and reported.
display_name: Accounting Policy
icon: book
---
```

Rules:
- `name` must match the directory name (`^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`).
- `description` is 5-300 characters.
- Maximum 100 knowledge folders per plugin.
- `KNOWLEDGE.md` entrypoint limit: 256 KB.
- No symbolic links inside knowledge directories.
- Knowledge is namespaced as `<namespace>:<name>`.

## Step 7 — Agents (optional, Vibe extension only)

Place subagent definitions under `ai.mistral.vibe/agents/`. Each `.toml` file
defines one subagent:

```
ai.mistral.vibe/
  agents/
    researcher.toml
```

`researcher.toml`:

```toml
schemaVersion = 1
agentType = "subagent"
displayName = "Researcher"
description = "Searches the knowledge base and summarizes findings."
safety = "safe"
activeModel = "mistral-medium-3.5"
instructions = "You are a research assistant."

enabledTools = ["read_file", "grep"]
disabledTools = ["bash"]

[tools.bash]
permission = "never"
allowlist = ["ls"]
denylist = ["rm"]
```

Rules:
- `schemaVersion` must be 1.
- `agentType` must be `"subagent"` (the only supported type).
- Filename must be lowercase kebab-case (`^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`).
- `safety` is one of `"safe"`, `"neutral"`, `"destructive"`, `"yolo"`.
- Maximum 128 agents per plugin, 64 KB per file.
- Tool lists cannot contain duplicates or empty names.
- Agent names are namespaced as `<namespace>:<filename-stem>`.

## Step 8 — Libraries (optional, Vibe extension only)

Declare Node and Python library dependencies in `libraries.json`:

```json
{
  "schemaVersion": 1,
  "node": {
    "@productivity/finance": "./node/finance",
    "lodash": "./node/lodash"
  },
  "python": {
    "vibe_finance": "./python/vibe_finance",
    "utils": "./python/utils.py"
  }
}
```

Rules:
- Node aliases must match `^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$`
  (max 214 chars).
- Python aliases must match `^[A-Za-z_][A-Za-z0-9_]*$` (max 128 chars).
- Source paths are relative to the plugin root, using forward slashes only.
  No `..` or absolute paths.
- Python library sources can be a directory (package) or a single `.py` file.
- Node library sources must be directories.
- No symbolic links inside library directories.
- Library aliases that collide across plugins are all dropped.
- Maximum 128 entries per language.

## Step 9 — Connectors (optional, Vibe extension only)

Declare required managed connectors in `connectors.json`:

```json
{
  "schemaVersion": 1,
  "connectors": [
    {
      "id": "github",
      "tools": ["create_issue", "list_issues"]
    },
    {
      "id": "linear",
      "tools": ["create_issue"]
    }
  ]
}
```

Rules:
- Connector IDs must be unique within the plugin.
- Tool names per connector must be unique, 1-256 characters.
- Maximum 128 connector requirements.
- A connector requirement is a declaration: it appears whether or not the
  account has the connector. Availability is reported as a diagnostic.

## Diagnostic codes

When a plugin fails to load or has issues, Vibe emits typed diagnostics. The
most common ones:

| Code | Fatal | Meaning |
|---|---|---|
| `plugin.manifest.invalid` | yes | `plugin.json` missing, unreadable, or invalid |
| `plugin.schema.version_unsupported` | yes | unsupported schema version |
| `plugin.compatibility.format_unrecognized` | yes | no supported manifest found |
| `plugin.namespace.reserved` | yes | namespace is reserved |
| `plugin.namespace.collision` | yes | two plugins claim one namespace |
| `plugin.name.collision` | yes | two plugins share a name at the same scope |
| `plugin.path.outside_root` | yes | a declared path escapes the plugin root |
| `plugin.skill.invalid` | no | one `SKILL.md` failed to parse |
| `plugin.hooks.invalid` | no | one hook entry failed validation |
| `plugin.knowledge.invalid` | no | one knowledge folder failed validation |
| `plugin.agent.invalid` | no | one agent document failed validation |
| `plugin.libraries.invalid` | no | `libraries.json` failed to load |
| `plugin.library.invalid` | no | one library path is invalid |
| `plugin.library.alias_collision` | no | library alias shared across plugins |
| `plugin.connectors.invalid` | no | `connectors.json` failed to load |
| `plugin.mcp.connection_failed` | no | an MCP server did not answer |
| `plugin.mcp.authorization_required` | no | an MCP server needs authorization |
| `plugin.tool_override.unused` | no | a `toolOverrides` key matched no tool |

Fatal diagnostics drop the entire plugin. Non-fatal diagnostics drop only the
offending component; the rest of the plugin survives.

## Compatibility with other plugin formats

Vibe also detects and adapts plugins authored for other agents:

| Format | Marker | Adapted as |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` | skills, MCP servers, hooks |
| Codex | `.codex-plugin/plugin.json` | skills, MCP servers |
| Kimi Code | `kimi.plugin.json` or `.kimi-plugin/plugin.json` | skills, MCP servers, hooks |
| OpenCode | `.opencode/` directory | skills only (executable modules unsupported) |

If markers for more than one format are present, the plugin is rejected as
ambiguous. Native Agent Plugins 1.0 (a valid `plugin.json` with the correct
`$schema`) always takes precedence.

When creating a new plugin, always use the native Agent Plugins 1.0 format.

## Workflow

1. Determine the plugin name, scope (project vs user global), and which
   components the user needs.
2. Create the directory at `.vibe/plugins/<name>/` (project) or
   `~/.vibe/plugins/<name>/` (user).
3. Write `plugin.json` with the required `$schema` and `name` fields.
4. Add the `ai.mistral.vibe` extension to `plugin.json` if any Vibe-specific
   components (hooks, knowledge, agents, libraries, connectors) are needed.
5. Add component directories and files per the instructions above.
6. Tell the user to run `/reload` to pick up the new plugin without
   restarting.
