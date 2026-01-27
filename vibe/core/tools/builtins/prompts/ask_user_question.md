Use `ask_user_question` to gather information from the user when you need clarification, want to validate assumptions, or need help making a decision. **Don't hesitate to use this tool** - it's better to ask than to guess wrong.

## When to Use

- **Clarifying requirements**: Ambiguous instructions, unclear scope
- **Technical decisions**: Architecture choices, library selection, tradeoffs
- **Preference gathering**: UI style, naming conventions, approach options
- **Validation**: Confirming understanding before starting significant work
- **Multiple valid paths**: When several approaches could work and you want user input

## Question Structure

Each question has these fields:

- `question`: The full question text (be specific and clear)
- `header`: A short label displayed as a chip (max 12 characters, e.g., "Auth", "Database", "Approach")
- `options`: 2-4 choices (an "Other" option is automatically added for free text)
- `multi_select`: Set to `true` if user can pick multiple options (default: `false`)

### Options Structure

Each option has:
- `label`: Short display text (1-5 words)
- `description`: Brief explanation of what this choice means or its implications

## Examples

**Single question with recommended option:**
```json
{
  "questions": [{
    "question": "Which authentication method should we use?",
    "header": "Auth",
    "options": [
      {"label": "JWT tokens (Recommended)", "description": "Stateless, scalable, works well with APIs"},
      {"label": "Session cookies", "description": "Traditional approach, requires session storage"},
      {"label": "OAuth 2.0", "description": "Third-party auth, more complex setup"}
    ],
    "multi_select": false
  }]
}
```

**Multiple questions (displayed as tabs):**
```json
{
  "questions": [
    {
      "question": "Which database should we use?",
      "header": "Database",
      "options": [
        {"label": "PostgreSQL", "description": "Relational, ACID compliant"},
        {"label": "MongoDB", "description": "Document store, flexible schema"}
      ],
      "multi_select": false
    },
    {
      "question": "Which features should be included in v1?",
      "header": "Features",
      "options": [
        {"label": "User auth", "description": "Login, signup, password reset"},
        {"label": "Search", "description": "Full-text search across content"},
        {"label": "Export", "description": "CSV and PDF export"}
      ],
      "multi_select": true
    }
  ]
}
```

## Key Constraints

- **Header max length**: 12 characters (keeps UI clean)
- **Options count**: 2-4 per question (plus automatic "Other")
- **Questions count**: 1-4 per call
- **Label length**: Keep to 1-5 words for readability

## Tips

1. **Put recommended option first** and add "(Recommended)" to its label
2. **Use descriptive headers** that categorize the question type
3. **Keep descriptions concise** but informative about tradeoffs
4. **Use multi_select** when choices aren't mutually exclusive (e.g., features to include)
5. **Ask early** - it's better to clarify before starting than to redo work
