Use `ask_user_question` to ask the user questions during execution.

Usage:
- Use it to gather preferences, clarify ambiguous instructions, get decisions, or offer a choice of direction. It is better to ask than to guess wrong.
- Provide 1-4 `questions` (displayed as tabs when there are several); each has a `question`, a short `header` (max 20 characters), and 2-4 `options`. An "Other" free-text option is added automatically.
- Each option has a `label` (1-5 words) and a `description` of what the choice means or its implications.
- Set `multi_select: true` when the user may pick more than one option (e.g. features to include).
- If you recommend an option, put it first and add "(Recommended)" to its label.
