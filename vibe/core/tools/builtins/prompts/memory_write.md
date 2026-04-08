Use the memory_write tool to save information that should persist across sessions.

## What to save

- **feedback**: User corrections or preferences about how you work ("don't mock the database", "use tabs not spaces")
- **user**: Information about the user's role, expertise, or goals
- **project**: Ongoing work context, decisions, deadlines that aren't in code or git
- **reference**: Pointers to external resources (URLs, tool locations, API docs)

## What NOT to save

- Code patterns or architecture (read the code instead)
- Git history (use git log)
- Anything already in AGENTS.md
- Temporary task details for the current session

## Tips

- Use descriptive names: "user_prefers_pytest" not "memory_1"
- Keep descriptions concise - they're used to decide relevance
- Use project scope for project-specific memories, global for user preferences
- Update existing memories rather than creating duplicates
