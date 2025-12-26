# Save/Load Feature User Guide

## Overview
The enhanced save/load feature allows you to save your conversation history with custom names and easily load them later with an interactive selection interface.

## Saving a Conversation

### Basic Usage
1. Type `/save` or `/stash` at any time during your conversation
2. A dialog will appear with a suggested name based on your first message
3. You can:
   - **Create a folder**: Press '1' to create a new folder for organization
   - **Edit the name**: Type your custom name
   - **Accept the suggestion**: Just press Enter
   - **Cancel**: Press Escape

### Organizing Conversations

You can create folders to organize your conversations:

```
Save Conversation
----------------
1. Create Folder

My_Conversation_Name

Enter submit  ESC cancel
```

**To create a folder:**
1. Press '1' in the save dialog
2. Enter your folder name (e.g., "code_reviews")
3. The folder will be created in the `conversations/` directory
4. Future saves can use this folder for organization

**Example folder structure:**
```
conversations/
├── code_reviews/
│   ├── PR_123_Review.json
│   └── PR_456_Review.json
├── architecture/
│   └── System_Design.json
└── onboarding/
    └── New_Hire_Guide.json
```

### Example
```
User: How do I create a virtual environment in Python?
Assistant: You can use the venv module...

User: /save

[Dialog appears]
Save Conversation
----------------
How_do_I_create_a_virtual_environment_in_Python_

Enter submit  ESC cancel
```

## Loading a Conversation

### Basic Usage
1. Type `/load` or `/resume` to see your saved conversations
2. Use the arrow keys (↑/↓) to navigate through the tree structure
3. Press Enter to load the selected conversation or expand/collapse a folder
4. Press Escape to cancel
5. Use ←/→ keys to collapse/expand folders

**Important**: After loading, the token counter at the bottom of the screen will automatically update to show your current token usage. The system estimates token usage based on message length and displays the percentage used. This helps you avoid running out of tokens unexpectedly.

### Tree Navigation
The load interface now displays conversations in a tree structure, making it easy to navigate through your organized folders:

```
User: /load

[Conversation Selector appears]
Select Conversation
------------------
› conversations/
  [-] code_reviews/
  │  [-] PR_123_Review
  │    Date: 2024-12-20 15:30 | Messages: 12 | Model: gpt-4
  │  [-] PR_456_Review
  │    Date: 2024-12-19 10:15 | Messages: 8 | Model: gpt-4
  [-] architecture/
  │  [-] System_Design
  │    Date: 2024-12-18 09:45 | Messages: 20 | Model: gpt-4
  [-] onboarding/
  │  [-] New_Hire_Guide
  │    Date: 2024-12-17 14:20 | Messages: 15 | Model: gpt-4
  [-] Python Virtual Environment Setup
    Date: 2024-12-16 10:10 | Messages: 10 | Model: gpt-4
  [-] Python Debugging Tips
    Date: 2024-12-15 09:30 | Messages: 8 | Model: gpt-4

↑↓ navigate  Enter select  ESC cancel  ←/→ expand/collapse
```

**Folder Indicators:**
- `[+]` - Folder is collapsed (click to expand)
- `[-]` - Folder is expanded (click to collapse)
- All folders are expanded by default for easy browsing

**Navigation Tips:**
- Use ↑/↓ to move through the tree
- Use Enter to expand/collapse folders or load conversations
- Use ← to collapse the current folder
- Use → to expand the current folder
- Folders at the same level are grouped together

## Key Features

### Interactive Selection
- **Arrow keys (↑/↓)**: Navigate through your saved conversations
- **Enter**: Load the selected conversation
- **Escape**: Cancel and return to chat

### Conversation Metadata
Each saved conversation displays:
- **Name**: Your custom name or auto-generated name
- **Date**: When the conversation was saved (YYYY-MM-DD HH:MM)
- **Message count**: Number of messages in the conversation
- **Model**: The AI model used (e.g., gpt-4, devstral)

### Auto-Naming
If you don't provide a name, the system automatically generates one from:
- The first 50 characters of your first message
- Invalid characters are replaced with underscores

### Sorting
Conversations are sorted by date with the most recent first.

## Tips & Tricks

### Naming Conventions
- Use descriptive names for easy identification
- Include project or topic names
- Keep names under 50 characters for best display

### Organizing Conversations
- Save different topics separately
- Use consistent naming patterns (e.g., "ProjectX - FeatureY")
- Save frequently when working on complex topics

### Loading Conversations
- The most recent conversation is selected by default
- Use arrow keys to quickly navigate
- Loading a conversation replaces your current chat history

## Troubleshooting

### No Conversations Found
- Make sure you've saved at least one conversation with `/save`
- Check that the `conversations/` directory exists in your working directory

### Cannot Load While Agent is Processing
- Wait for the agent to finish processing before loading
- The system will show a message if you try to load during processing

### Invalid Conversation Files
- Some files may be skipped if they're corrupted
- The system continues to show valid conversations

## File Storage
- Conversations are saved as JSON files in the `conversations/` directory
- Each file includes:
  - Conversation name
  - Timestamp
  - All messages (excluding system prompts)
  - Statistics (token counts)
  - Model information

## Example Workflow

```
1. User starts a new conversation
   User: How do I deploy a Django application?

2. User continues the conversation
   Assistant: You'll need to...
   User: What about database migrations?
   Assistant: Run `python manage.py migrate`...

3. User saves the conversation
   User: /save
   [Edits name to "Django Deployment Guide"]
   ✓ Conversation saved as: Django Deployment Guide

4. Later, user loads the conversation
   User: /load
   [Navigates to "Django Deployment Guide"]
   Enter
   ✓ Loaded conversation: Django Deployment Guide
     - 12 messages loaded
     - Model: gpt-4

5. User continues where they left off
   User: What about production monitoring?
```

## Commands Reference

| Command | Aliases | Description |
|---------|---------|-------------|
| `/save` | `/stash` | Save current conversation with custom name |
| `/load` | `/resume` | Load a saved conversation from the list |

## Navigation Reference

### Save Dialog
- **Enter**: Submit the conversation name
- **Escape**: Cancel and return to chat
- **1**: Create a new folder

### Load Selector (Tree Navigation)
- **↑/↓**: Move selection up/down through the tree
- **Enter**: Load the selected conversation or toggle folder expansion/collapse
- **Escape**: Cancel and return to chat
- **←**: Collapse the current folder
- **→**: Expand the current folder
- **Note**: Folders are collapsed by default (except root) for better organization
