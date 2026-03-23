Use `skill` to load specialized skills that provide domain-specific instructions and workflows.

## When to Use This Tool

- When a task matches one of the available skills listed in your system prompt
- When the user references a skill by name (e.g., "use the review skill")
- When you need specialized workflows, templates, or scripts bundled with a skill

## How It Works

1. Call `skill` with the skill's `name` from the `<available_skills>` section
2. The tool returns the full skill instructions along with a list of bundled files
3. Follow the loaded instructions step by step — you are the executor

## Notes

- Skills may include bundled resources (scripts, references, templates) in their directory
- File paths in skill output are relative to the skill's base directory
- Each skill is loaded once per invocation — re-invoke if you need to reload
