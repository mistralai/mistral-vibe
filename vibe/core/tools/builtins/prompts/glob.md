Use the glob tool to find files by name pattern. This is the fastest way to discover files in a project.

## When to use glob vs grep vs bash

- **glob**: Find files by name - `**/*.py`, `src/**/*.ts`, `**/config.*`
- **grep**: Search file contents for text or regex patterns
- **bash**: Complex operations, piped commands, or anything not covered above

## Common patterns

- `**/*.py` - All Python files recursively
- `src/**/*.ts` - TypeScript files under src/
- `**/*.{js,ts,tsx}` - Multiple extensions
- `**/test_*.py` - Test files
- `**/config.*` - Config files with any extension
- `*.md` - Markdown files in current directory only (no recursion)

## Tips

- Always use `**/*.ext` for recursive search, not `*.ext` (which only searches current dir)
- The path parameter lets you narrow the search to a subdirectory
- Results are sorted alphabetically and limited to 200 by default
