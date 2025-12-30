# Custom Slash Commands

Custom slash commands allow you to extend Mistral Vibe with your own commands that can execute bash scripts, insert prompt templates, or run custom logic.

## Quick Start

### 1. Create the commands directory

```bash
mkdir -p ~/.vibe/commands
```

### 2. Create your first custom command

Create a file `~/.vibe/commands/test.toml`:

```toml
[command]
name = "test"
aliases = ["/test", "/t"]
description = "Run the project's test suite"
type = "bash"
command = "pytest tests/ -v"
```

### 3. Use your command

Start Vibe and type `/test` to run your test suite!

## Command Types

### Bash Commands

Execute shell commands in the current working directory:

```toml
[command]
name = "build"
aliases = ["/build", "/b"]
description = "Build the project"
type = "bash"
command = "npm run build"
```

**Features:**
- Runs in the project's working directory
- Uses `/bin/bash` on Unix systems
- Captures stdout and stderr
- Shows output in the Vibe UI
- 30-second timeout (configurable)

### Prompt Templates

Insert pre-written prompts into the conversation:

```toml
[command]
name = "review"
aliases = ["/review", "/r"]
description = "Request a code review"
type = "prompt"
template = '''Please review the changes I just made. Focus on:
1. Code quality and best practices
2. Potential bugs or edge cases
3. Performance considerations
4. Security implications

Provide constructive feedback and suggestions for improvement.'''
```

**Features:**
- Submits the template as a user message
- Agent responds with analysis
- Perfect for common prompts you use frequently
- Supports multi-line templates

## Command Format

All custom commands are defined in TOML files in `~/.vibe/commands/`.

### Required Fields

- `name`: Unique identifier for the command
- `description`: Help text shown in `/help`
- `type`: Command type (`"bash"` or `"prompt"`)

### Optional Fields

- `aliases`: List of command aliases (defaults to `/name`)
- `exits`: Whether the command should exit Vibe (default: `false`)

### Bash Command Fields

- `command`: The bash command to execute

### Prompt Command Fields

- `template`: The prompt text to submit

## Examples

### Run Tests

```toml
# ~/.vibe/commands/test.toml
[command]
name = "test"
aliases = ["/test", "/t"]
description = "Run project tests"
type = "bash"
command = "pytest tests/ -v"
```

### Git Status

```toml
# ~/.vibe/commands/git-status.toml
[command]
name = "gitstatus"
aliases = ["/gs", "/gst"]
description = "Show git status and recent commits"
type = "bash"
command = "git status && echo '\\n--- Recent Commits ---' && git log --oneline -5"
```

### Code Review Template

```toml
# ~/.vibe/commands/review.toml
[command]
name = "review"
aliases = ["/review", "/r"]
description = "Request a thorough code review"
type = "prompt"
template = '''Please review the code changes I just made:

**Review Checklist:**
1. Code Quality & Style
   - Follows project conventions?
   - Readable and maintainable?
   - Properly documented?

2. Functionality
   - Logic is correct?
   - Edge cases handled?
   - Error handling appropriate?

3. Performance
   - Efficient algorithms?
   - No obvious bottlenecks?

4. Security
   - Input validation?
   - No injection vulnerabilities?
   - Secrets properly handled?

Provide specific, actionable feedback.'''
```

### Deploy Command

```toml
# ~/.vibe/commands/deploy.toml
[command]
name = "deploy"
aliases = ["/deploy"]
description = "Deploy to staging environment"
type = "bash"
command = "./scripts/deploy.sh staging"
```

### Documentation Generator

```toml
# ~/.vibe/commands/docs.toml
[command]
name = "gendocs"
aliases = ["/docs"]
description = "Generate API documentation"
type = "bash"
command = "npm run docs:generate && echo 'Documentation generated in ./docs/api/'"
```

### Explain Recent Changes

```toml
# ~/.vibe/commands/explain.toml
[command]
name = "explain"
aliases = ["/explain", "/e"]
description = "Explain recent git changes"
type = "prompt"
template = "Please explain the recent changes in this repository. Look at the git diff and commit history, then provide a clear summary of what changed and why."
```

## Best Practices

### Naming Conventions

- Use lowercase names with hyphens: `git-status`, `run-tests`
- Keep aliases short: `/t` for `/test`, `/gs` for `/gitstatus`
- Make names descriptive and memorable

### Organization

- One command per file
- Group related commands with prefixes: `git-status.toml`, `git-log.toml`
- Use clear filenames that match the command name

### Bash Commands

- Keep commands simple and focused
- Chain multiple commands with `&&` for sequential execution
- Use absolute paths when necessary
- Include error handling in your scripts
- Test commands manually before adding them

### Prompt Templates

- Be specific about what you want
- Include structured formatting (bullets, numbers)
- Add context about your project if needed
- Keep templates reusable and not too specific

## Viewing Commands

Your custom commands appear in the `/help` output under a "Custom Commands" section.

```
### Custom Commands

- `/test`, `/t`: Run project tests
- `/review`, `/r`: Request a thorough code review
- `/gs`, `/gst`: Show git status and recent commits
```

## Troubleshooting

### Command Not Found

- Check that the file is in `~/.vibe/commands/`
- Verify the file has `.toml` extension
- Restart Vibe to reload commands

### Bash Command Fails

- Test the command manually in your terminal
- Check that paths are correct
- Verify required tools are installed
- Check command timeout (30s default)

### Syntax Errors

- Validate TOML syntax: `python -m tomli ~/.vibe/commands/mycommand.toml`
- Check for required fields
- Ensure strings are properly quoted
- Use triple quotes for multi-line templates

## Advanced Features

### Project-Specific Commands

Create a `.vibe/commands/` directory in your project root for project-specific commands that won't appear in other projects.

### Command Chaining

```toml
[command]
name = "test-and-build"
type = "bash"
command = "pytest tests/ && npm run build && echo 'Tests passed and build completed!'"
```

### Environment Variables

Custom commands have access to the same environment as Vibe:

```toml
[command]
name = "check-env"
type = "bash"
command = "echo $VIBE_PROJECT_DIR && echo $USER"
```

## Future Enhancements

The custom command system is extensible and may support additional features in the future:

- Python command type for custom Python code
- Command arguments and parameters
- Interactive prompts
- Conditional execution
- Command groups and subcommands

## Contributing

Have ideas for improving custom commands? Open an issue or PR on GitHub!
