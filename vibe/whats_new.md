# What's new in v2.25.7

- **Queued prompts**: messages you queue while Vibe is working are now sent together in one turn, not one at a time
- **Stable session models**: resumed conversations keep the model selected for that session
- **Compact tool call groups**: consecutive tool calls now fold into a one-line summary (e.g. "Running commands" while in progress, "Ran commands" when done). Click to expand the full list, or use Ctrl+O to toggle all groups at once. File edits and writes are included in the fold.
- **Experimental harness**: You can now try our new experimental harness with the --experimental-harness flag.
- **Loops in VS Code**: Schedule recurring prompts from the chat input — open the loop panel via the + menu or the loop icon, set an interval and prompt, and let it run on a schedule.
- **Images on any model**: attach screenshots even when your active model has no vision — a vision-capable model on the same provider describes them for it (`--experimental-harness`).
