# /skills Command Implementation Plan

## Overview
This document outlines the implementation plan for adding a `/skills` command to Vibe that allows users to browse existing skills and edit basic skill metadata.

## Requirements
- **Primary use case**: Browse existing skills and edit basic metadata
- **Editing level**: Basic metadata only (name, description, license, compatibility, user-invocable)
- **Invocation**: Keep both `/skills` command and existing `/skill-name` slash commands
- **UI approach**: Simple text list interface

## Phase 1: Basic Skills Browser (2-3 hours)

### Objective
Create a simple `/skills` command that lists available skills

### Files to Modify
1. **`vibe/cli/commands.py`** - Add `/skills` command registration
2. **`vibe/cli/textual_ui/app.py`** - Add `_show_skills()` handler

### Implementation

#### 1. Add Command Registration
```python
# In vibe/cli/commands.py
"skills": Command(
    aliases=frozenset(["/skills"]),
    description="Browse and manage skills",
    handler="_show_skills",
),
```

#### 2. Implement Basic Handler
```python
# In vibe/cli/textual_ui/app.py
async def _show_skills(self, skill_name: str | None = None) -> None:
    """Show skills list or specific skill details."""
    if not self.agent_loop:
        await self._mount_and_scroll(UserCommandMessage("Skills not available."))
        return

    skills = self.agent_loop.skill_manager.available_skills

    if skill_name:
        await self._show_skill_details(skill_name, skills)
    else:
        await self._show_skills_list(skills)

async def _show_skills_list(self, skills: dict[str, SkillInfo]) -> None:
    if not skills:
        await self._mount_and_scroll(UserCommandMessage("No skills available."))
        return

    skill_list = "\n".join(
        f"- `/{name}`: {info.description}"
        for name, info in sorted(skills.items())
    )
    await self._mount_and_scroll(UserCommandMessage(
        f"Available skills:\n\n{skill_list}\n\n"
        "Type `/skills <name>` to view details or `/skill-name` to invoke."
    ))

async def _show_skill_details(self, skill_name: str, skills: dict[str, SkillInfo]) -> None:
    skill_info = skills.get(skill_name)
    if not skill_info:
        await self._mount_and_scroll(UserCommandMessage(
            f"Skill '{skill_name}' not found."
        ))
        return

    details = [
        f"Name: {skill_info.name}",
        f"Description: {skill_info.description}",
        f"License: {skill_info.license or 'None'}",
        f"Compatibility: {skill_info.compatibility or 'None'}",
        f"User-invocable: {skill_info.user_invocable}",
        f"Path: {skill_info.skill_path}",
    ]

    await self._mount_and_scroll(UserCommandMessage(
        f"Skill details: {skill_name}\n\n" + "\n".join(details)
    ))
```

## Phase 2: Skill Detail View (3-4 hours)

### Objective
Add ability to view full skill details when specific skill is requested

### Implementation
- Extend `_show_skills()` to accept optional skill name parameter
- Parse command input to extract skill name if provided
- Show detailed metadata when specific skill is requested
- Example usage: `/skills code-review` shows full details

### Code Updates
The detail view implementation is already included in Phase 1 above.

## Phase 3: Basic Metadata Editing (4-5 hours)

### Objective
Add simple metadata editing for basic fields

### Files to Create
1. **`vibe/cli/textual_ui/widgets/skill_editor.py`** - Simple metadata editor widget

### Implementation
```python
# vibe/cli/textual_ui/widgets/skill_editor.py
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Input, Button, Static
from textual.message import Message
from pathlib import Path
import yaml

from vibe.core.skills.models import SkillMetadata
from vibe.core.skills.parser import parse_frontmatter

class SkillEditor(Container):
    """Simple skill metadata editor."""

    class SkillSaved(Message):
        """Message posted when skill is successfully saved."""
        def __init__(self, skill_name: str) -> None:
            super().__init__()
            self.skill_name = skill_name

    class EditCancelled(Message):
        """Message posted when editing is cancelled."""
        pass

    def __init__(self, skill_path: Path) -> None:
        super().__init__()
        self.skill_path = skill_path
        self.skill_content = skill_path.read_text(encoding="utf-8")
        self.frontmatter, self.markdown_body = parse_frontmatter(self.skill_content)
        self.metadata = SkillMetadata.model_validate(self.frontmatter)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Edit Skill Metadata", classes="editor-title")
            yield Static("", classes="editor-separator")

            # Name field
            yield Static("Name:")
            self.name_input = Input(value=self.metadata.name)
            yield self.name_input

            # Description field
            yield Static("Description:")
            self.desc_input = Input(value=self.metadata.description, multiline=True)
            yield self.desc_input

            # License field
            yield Static("License:")
            self.license_input = Input(value=self.metadata.license or "")
            yield self.license_input

            # Compatibility field
            yield Static("Compatibility:")
            self.compat_input = Input(value=self.metadata.compatibility or "")
            yield self.compat_input

            # User-invocable checkbox
            self.invocable_checkbox = Input(
                value="Yes" if self.metadata.user_invocable else "No",
                placeholder="Yes/No"
            )
            yield Static("User-invocable (Yes/No):")
            yield self.invocable_checkbox

            yield Static("", classes="editor-separator")

            with Container(id="editor-buttons"):
                yield Button("Save", id="save-button", variant="primary")
                yield Button("Cancel", id="cancel-button", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-button":
            self._save_skill()
        elif event.button.id == "cancel-button":
            self.post_message(self.EditCancelled())

    def _save_skill(self) -> None:
        try:
            # Validate inputs
            name = self.name_input.value.strip()
            if not name:
                raise ValueError("Name cannot be empty")

            description = self.desc_input.value.strip()
            if not description:
                raise ValueError("Description cannot be empty")

            # Update metadata
            self.metadata.name = name
            self.metadata.description = description
            self.metadata.license = self.license_input.value.strip() or None
            self.metadata.compatibility = self.compat_input.value.strip() or None
            self.metadata.user_invocable = self.invocable_checkbox.value.lower() in ("yes", "true", "1")

            # Reconstruct frontmatter
            frontmatter_yaml = yaml.dump(
                self.metadata.model_dump(by_alias=True, exclude_none=True),
                default_flow_style=False,
                allow_unicode=True
            )

            # Reconstruct full content
            new_content = f"---\n{frontmatter_yaml.strip()}\n---\n\n{self.markdown_body}"

            # Save to file
            self.skill_path.write_text(new_content, encoding="utf-8")

            self.post_message(self.SkillSaved(self.metadata.name))

        except Exception as e:
            self.notify(f"Error saving skill: {e}", severity="error")
```

### Integration with Main App
```python
# In vibe/cli/textual_ui/app.py
async def on_skill_editor_skill_saved(self, message: SkillEditor.SkillSaved) -> None:
    await self._mount_and_scroll(UserCommandMessage(
        f"Skill '{message.skill_name}' saved successfully!"
    ))
    await self._switch_to_input_app()

async def on_skill_editor_edit_cancelled(self, message: SkillEditor.EditCancelled) -> None:
    await self._mount_and_scroll(UserCommandMessage("Skill editing cancelled."))
    await self._switch_to_input_app()
```

## Phase 4: Integration and Testing (2-3 hours)

### Tasks
1. Test skill listing functionality
2. Test detail view for individual skills
3. Test metadata editing and saving
4. Verify file permissions and error handling
5. Test with various skill configurations

### Test Cases
- List skills when none available
- List skills when some available
- Show details for existing skill
- Show details for non-existent skill
- Edit and save skill metadata
- Handle invalid metadata inputs
- Test file permission errors

## Implementation Timeline

### Total Estimated Time: 11-15 hours
- **Phase 1 (Basic Browser)**: 2-3 hours
- **Phase 2 (Detail View)**: 3-4 hours
- **Phase 3 (Editing)**: 4-5 hours
- **Phase 4 (Testing)**: 2-3 hours

### Recommended Priority Order
1. **High Priority**: Phases 1-2 (Basic browsing and detail view)
2. **Medium Priority**: Phase 3 (Basic editing)
3. **Low Priority**: Advanced features (templates, bulk operations)

## Benefits of This Implementation

1. **Simple and Focused**: Starts with basic browsing functionality
2. **Backward Compatible**: Doesn't break existing slash command invocation
3. **Progressive Enhancement**: Can add features gradually
4. **User-Friendly**: Simple text interface matches user preference
5. **Safe**: Basic implementation has minimal risk
6. **Extensible**: Foundation for future enhancements

## Usage Examples

### List all skills
```
/skills
```

### Show specific skill details
```
/skills code-review
```

### Invoke a skill (existing functionality preserved)
```
/code-review
```

## Future Enhancements

1. **Skill Creation**: Template-based new skill creation
2. **Advanced Editing**: Full YAML and Markdown editing
3. **Enable/Disable**: Toggle skills without file editing
4. **Search/Filter**: Find skills by name or description
5. **Bulk Operations**: Enable/disable multiple skills at once

## Error Handling Considerations

1. **File Not Found**: Handle missing skill files gracefully
2. **Permission Errors**: Provide clear messages for file access issues
3. **Invalid YAML**: Validate and show specific error locations
4. **Duplicate Names**: Prevent name conflicts when saving
5. **Validation Errors**: Show user-friendly error messages

## Backward Compatibility

- Existing `/skill-name` slash commands continue to work
- No changes to skill file format or discovery mechanism
- Config file changes are additive only
- All existing functionality preserved

## Implementation Checklist

- [ ] Add command registration to `vibe/cli/commands.py`
- [ ] Implement basic handler in `vibe/cli/textual_ui/app.py`
- [ ] Add list view functionality
- [ ] Add detail view functionality
- [ ] Create skill editor widget
- [ ] Implement metadata editing
- [ ] Add save functionality with validation
- [ ] Test all functionality
- [ ] Handle edge cases and errors
- [ ] Update documentation

## Notes

- This plan focuses on the user's stated preference for simple text interfaces
- Editing is limited to basic metadata as requested
- The implementation maintains all existing functionality
- Error handling and user feedback are prioritized
- The design allows for easy future enhancements
