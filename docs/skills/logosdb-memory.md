# LogosDB Memory Skills

This guide shows how to give Vibe persistent semantic memory across sessions
using [LogosDB](https://github.com/jose-compu/logosdb) — a local, zero-infrastructure
HNSW vector database.

Once installed, three slash commands become available:

| Command | What it does |
|---------|-------------|
| `/ldb-index <path> [--namespace=<n>]` | Embed and store a file or directory |
| `/ldb-search <query> [--namespace=<n>]` | Semantic similarity search |
| `/ldb-forget [--id=<id>\|--query=<q>]` | Remove stored entries |

## Prerequisites

- Python ≥ 3.10
- A Mistral AI API key (already required by Vibe)

## Installation

```bash
pip install "logosdb[vibe]"
```

This installs both the `logosdb` library and the `logosdb-vibe` CLI that the
skills call under the hood.

Verify:

```bash
logosdb-vibe --help
```

## Skill setup

Create one directory per skill under `.vibe/skills/` in your project root
(or `~/.vibe/skills/` for a user-wide installation):

```
.vibe/
  skills/
    ldb-index/
      SKILL.md
    ldb-search/
      SKILL.md
    ldb-forget/
      SKILL.md
```

### `.vibe/skills/ldb-index/SKILL.md`

```markdown
---
name: ldb-index
description: Index a file or directory into LogosDB for semantic search across Vibe sessions.
user-invocable: true
allowed-tools:
  - bash
---

Index files into LogosDB using the logosdb-vibe CLI.

Parse the user's arguments:
- First positional argument is the path to index (file or directory)
- `--namespace=<name>` or `-n <name>` sets the collection (default: `code`)

Run exactly:
\```
logosdb-vibe index <path> --namespace <namespace>
\```

Then respond in this exact format (no extra prose):
Indexed {files} files into '{namespace}' collection
```

### `.vibe/skills/ldb-search/SKILL.md`

```markdown
---
name: ldb-search
description: Semantic search over an indexed LogosDB namespace.
user-invocable: true
allowed-tools:
  - bash
---

Search LogosDB using the logosdb-vibe CLI.

Parse the user's arguments:
- Everything before any flag is the search query (quote it)
- `--namespace=<name>` or `-n <name>` sets the collection (default: `code`)
- `--top-k=<n>` or `-k <n>` sets result count (default: 5)

Run exactly:
\```
logosdb-vibe search "<query>" --namespace <namespace> --top-k <top_k>
\```

The CLI returns JSON. Parse it and present results in this format:
Searching... Found {N} matches:
  1. {file} (score: {score})
  2. {file} (score: {score})
  ...

If the list is empty, respond: No matches found in '{namespace}' namespace.
```

### `.vibe/skills/ldb-forget/SKILL.md`

```markdown
---
name: ldb-forget
description: Delete an entry from LogosDB by row ID or by semantic query match.
user-invocable: true
allowed-tools:
  - bash
---

Delete entries from LogosDB using the logosdb-vibe CLI.

Parse the user's arguments:
- `--namespace=<name>` or `-n <name>` sets the collection (default: `code`)
- `--id=<number>` deletes by row ID
- `--query=<text>` or `-q <text>` deletes the closest semantic match

Run exactly one of:
\```
logosdb-vibe forget --namespace <namespace> --id <id>
logosdb-vibe forget --namespace <namespace> --query "<query>"
\```

Then respond: Deleted {n} entr{y/ies} from '{namespace}' namespace.
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MISTRAL_API_KEY` | *(required)* | Already set by Vibe |
| `LOGOSDB_PATH` | `~/.vibe/logosdb` | Where the index files are stored |
| `LOGOSDB_EMBED_MODEL` | `mistral-embed` | Mistral embedding model |

Set these in `~/.vibe/.env` or your shell profile:

```bash
export LOGOSDB_PATH="$HOME/.vibe/logosdb"
export LOGOSDB_EMBED_MODEL="mistral-embed"
```

## Optional: MCP server alternative

If you prefer the MCP route, LogosDB also ships a Node.js MCP server that
Vibe can connect to via `.vibe/config.toml`:

```toml
[[mcp_servers]]
name = "logosdb"
command = "npx"
args  = ["-y", "logosdb-mcp-server"]

[mcp_servers.env]
LOGOSDB_PATH        = "/path/to/.logosdb"
EMBEDDING_PROVIDER  = "openai"        # or "voyageai"
OPENAI_API_KEY      = "sk-..."

[mcp_servers.tool_permissions]
logosdb_index_file = "always"
logosdb_search     = "always"
logosdb_delete     = "ask"
logosdb_list       = "always"
logosdb_info       = "always"
```

Install the server once: `npm install -g logosdb-mcp-server`

## Example session

```
$ cd myproject
$ vibe

> /ldb-index ./src --namespace=backend
Indexed 42 files into 'backend' collection

> /ldb-search "JWT validation" --namespace=backend
Searching... Found 3 matches:
  1. src/auth/jwt.ts       (score: 0.94)
  2. src/middleware/auth.ts (score: 0.87)
  3. src/utils/token.ts    (score: 0.72)

> Explain what src/auth/jwt.ts does
[Vibe reads the file and explains the JWT implementation]

> /ldb-search "rate limiting" --namespace=backend
Searching... Found 2 matches:
  1. src/middleware/rateLimit.ts (score: 0.91)
  2. src/utils/redis.ts          (score: 0.78)

> /ldb-forget --query="rate limiting" --namespace=backend
Deleted 1 entry from 'backend' namespace.
```

## How it works

1. `/ldb-index` walks the target path, splits each file into overlapping text
   chunks, embeds them via Mistral Embed, and stores the vectors in a local
   HNSW index (a plain directory on disk).
2. `/ldb-search` embeds the query with the same model and performs
   approximate nearest-neighbour search — all locally, in milliseconds.
3. The index persists across Vibe sessions. Re-run `/ldb-index` only when
   files change.

## Resources

- [logosdb on PyPI](https://pypi.org/project/logosdb/)
- [logosdb GitHub](https://github.com/jose-compu/logosdb)
- [logosdb-mcp-server on npm](https://www.npmjs.com/package/logosdb-mcp-server)
