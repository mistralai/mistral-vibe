## Language Server Protocol (LSP) Tools

When working with typed languages (Python, TypeScript, Rust, Go, etc.), **prefer LSP tools over command-line tools** for code navigation and analysis. LSP tools provide semantic understanding, not just text matching.

### Decision Tree

```
Need to...                    → Use this tool:
─────────────────────────────────────────────────────
Get errors/warnings           → lsp_diagnostics
Get completion suggestions    → lsp_completion
Get type/documentation        → lsp_hover
Go to definition              → lsp_definition
Find all references           → lsp_references
Find symbols in current file  → lsp_document_symbols
Search symbols across project → lsp_workspace_symbols
Get function signature        → lsp_signature_help
Get available refactorings    → lsp_code_action
Highlight references under    → lsp_document_highlight
cursor
Get foldable code regions     → lsp_folding_ranges
Rename symbol safely          → lsp_rename
Find implementations of       → lsp_implementation
interface
Find type definition          → lsp_type_definition
Get formatting edits          → lsp_formatting
Get range formatting edits    → lsp_range_formatting
Check which LSPs are active   → lsp_status
─────────────────────────────────────────────────────
```

### When to Use LSP vs Command-Line Tools

**Prefer LSP for:**
- Symbol search (classes, functions, variables) → `lsp_workspace_symbols` instead of `grep`
- Navigation to definitions → `lsp_definition` instead of `grep` + `read_file`
- Finding references → `lsp_references` instead of `grep`
- Type information → `lsp_hover`, `lsp_signature_help`, `lsp_type_definition`
- Code structure → `lsp_document_symbols` instead of `bash` + `find`

**Use command-line tools when:**
- Searching for text patterns LSP doesn't index (comments, TODO, docstrings)
- Working with file types without LSP support (Markdown, YAML, config files)
- Need regex features beyond symbol search
- Performing operations across many file types simultaneously
- LSP server is not running for the target language

### Fallback Rules

If LSP tools return "not running" or empty results:

1. Use `lsp_status` to verify which servers are active
2. If no LSP available for the language, fall back to:
   - Simple text search: `grep`
   - Reading file content: `read_file`
   - Directory structure: `bash` (with Windows-compatible commands)

### Quick Reference

| Task | Best LSP Tool | Fallback |
|------|--------------|----------|
| Find function definition | `lsp_definition` | `grep` |
| Find all usages | `lsp_references` | `grep -r` |
| Explore file structure | `lsp_document_symbols` | `read_file` |
| Search for symbol name | `lsp_workspace_symbols` | `grep` |
| Understand function call | `lsp_signature_help` | `lsp_hover` |
| Rename safely | `lsp_rename` | Manual + `grep` |
| Get code fixes | `lsp_code_action` | Manual editing |