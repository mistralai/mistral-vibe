# What's new in v2.25.4

- **Queued prompts**: messages you queue while Vibe is working are now sent together in one turn, not one at a time
- **Stable session models**: resumed conversations keep the model selected for that session
- **Compact tool call groups**: consecutive tool calls now fold into a one-line summary (e.g. "Running commands" while in progress, "Ran commands" when done). Click to expand the full list, or use Ctrl+O to toggle all groups at once. File edits and writes are included in the fold.
- **Experimental harness**: You can now try our new experimental harness with the --experimental-harness flag.
