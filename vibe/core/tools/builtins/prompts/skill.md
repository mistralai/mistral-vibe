Use `skill` to load a specialized skill when the task matches one listed in your system prompt.

Usage:
- Pass the skill's `name` exactly as shown in the available skills section of your system prompt.
- The tool injects the skill's full instructions and may reference bundled scripts, files, and templates in the skill's directory.
- File paths in the skill output are relative to the skill's base directory.
- You are the executor: follow the loaded instructions step by step.
