from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Static

if TYPE_CHECKING:
    pass


@dataclass
class ConversationTreeItem:
    """Represents an item in the conversation tree (folder or conversation)."""
    path: Path
    name: str
    is_folder: bool
    date: datetime | None = None
    message_count: int = 0
    model: str = "unknown"
    level: int = 0  # Indentation level in tree


class ConversationTreeSelector(Container):
    """Widget for selecting a saved conversation with tree navigation."""
    can_focus = True
    can_focus_children = False

    BINDINGS: list[BindingType] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "close", "Cancel", show=False),
        Binding("left", "collapse", "Collapse", show=False),
        Binding("right", "expand", "Expand", show=False),
    ]

    class ConversationSelected(Message):
        """Message sent when a conversation is selected."""
        def __init__(self, filepath: Path) -> None:
            super().__init__()
            self.filepath = filepath

    class ConversationClosed(Message):
        """Message sent when the selector is closed without selection."""
        def __init__(self) -> None:
            super().__init__()

    def __init__(self, conversations: list[ConversationTreeItem]) -> None:
        super().__init__(id="conversation-tree-selector")
        self.all_items = conversations
        self.selected_index = 0
        self.item_widgets: list[Static] = []
        self.expanded_folders: set[str] = set()  # Track expanded folders by path

    def compose(self) -> ComposeResult:
        with Vertical(id="conversation-tree-content"):
            title = Static("Select Conversation", classes="approval-title")
            yield title

            yield Static("")

            for _ in self.all_items:
                widget = Static("", classes="approval-option")
                self.item_widgets.append(widget)
                yield widget

            yield Static("")

            help_text = Static(
                "↑↓ navigate  Enter select  ESC cancel  ←/→ expand/collapse",
                classes="approval-help",
            )
            yield help_text

    def on_mount(self) -> None:
        # Folders are collapsed by default (except root) via the expanded_folders set
        self._update_display()
        self.focus()

    def _update_display(self) -> None:
        """Update the display to show the tree structure."""
        visible_items = self._get_visible_items()
        
        # Only update widgets for visible items
        for i, (item, widget) in enumerate(zip(visible_items, self.item_widgets)):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "

            # Create indentation based on level
            indent = "  " * item.level
            
            # Tree marker - use proper tree structure with |- and   
            if item.level > 0:
                # Check if this is the last item at this level
                is_last_at_level = self._is_last_item_at_level(visible_items, i, item.level)
                
                # Build tree markers for each level
                tree_markers = []
                for level in range(1, item.level):
                    # Check if there's a sibling at this level
                    has_sibling = self._has_sibling_at_level(visible_items, i, level)
                    tree_markers.append("│ " if has_sibling else "  ")
                
                # Add the final connector
                tree_markers.append("└─ " if is_last_at_level else "├─ ")
                
                tree_marker = "".join(tree_markers)
            else:
                # For root level, no marker
                tree_marker = ""

            # Create display text
            if item.is_folder:
                # Show folder with expand/collapse indicator
                expand_indicator = "[+] " if str(item.path) not in self.expanded_folders else "[-] "
                text = f"{cursor}{indent}{tree_marker}{expand_indicator}{item.name}/"
            else:
                # Show conversation details inline with color coding
                date_str = item.date.strftime("%Y-%m-%d %H:%M") if item.date else "unknown"
                # The stats line should have the same base indentation as the file name
                # but WITHOUT the tree marker to avoid the |- character appearing
                # Add padding to account for the tree marker length (2 chars: ├─ or └─)
                stats_indent = f"{indent}{' ' * len(tree_marker)}"
                
                # Use ANSI color codes for better readability
                # Date in primary (bright), Message count in green, Model in yellow
                # Improved formatting: remove labels, just show the values
                text = (
                    f"{cursor}{indent}{tree_marker}{item.name}\n"
                    f"{stats_indent}[primary]{date_str}[/] | "
                    f"[green]{item.message_count} messages[/] | "
                    f"[yellow]{item.model}[/]"
                )

            widget.update(text)

            # Update styling
            widget.remove_class("approval-cursor-selected")
            widget.remove_class("approval-option-selected")

            if is_selected:
                widget.add_class("approval-cursor-selected")
                widget.add_class("approval-option-selected")
            else:
                widget.add_class("approval-option-selected")
        
        # Hide any extra widgets that aren't needed
        for widget in self.item_widgets[len(visible_items):]:
            widget.update("")
            widget.remove_class("approval-cursor-selected")
            widget.remove_class("approval-option-selected")

    def _get_visible_items(self) -> list[ConversationTreeItem]:
        """Get items that should be visible based on expanded folders."""
        # Build a hierarchical structure: {parent_path: [children]}
        hierarchy: dict[str, list[ConversationTreeItem]] = {}
        
        # First pass: collect all visible items and build hierarchy
        for item in self.all_items:
            # Always show root level items
            if item.level == 0:
                if "" not in hierarchy:
                    hierarchy[""] = []
                hierarchy[""].append(item)
                continue
            
            # For non-root items, check if their immediate parent folder is expanded
            parent_path = str(item.path.parent)
            
            # Check if the parent folder is expanded
            # Root folder is always expanded, so level 1 items are always visible
            if item.level == 1 or parent_path in self.expanded_folders:
                if parent_path not in hierarchy:
                    hierarchy[parent_path] = []
                hierarchy[parent_path].append(item)
        
        # Second pass: recursively flatten the hierarchy while maintaining order
        visible: list[ConversationTreeItem] = []
        self._flatten_hierarchy("", hierarchy, visible, self.expanded_folders)
        
        return visible
    
    def _flatten_hierarchy(
        self,
        parent_path: str,
        hierarchy: dict[str, list[ConversationTreeItem]],
        visible: list[ConversationTreeItem],
        expanded_folders: set[str]
    ) -> None:
        """Recursively flatten the hierarchy into a flat list while maintaining order."""
        # Get children of this parent
        children = hierarchy.get(parent_path, [])
        
        # Sort children: folders first (alphabetically), then files (alphabetically)
        children.sort(key=lambda c: (not c.is_folder, c.name))
        
        # Add children to visible list
        for child in children:
            visible.append(child)
            
            # If this is an expanded folder, recursively add its children
            if child.is_folder and str(child.path) in expanded_folders:
                self._flatten_hierarchy(
                    str(child.path),
                    hierarchy,
                    visible,
                    expanded_folders
                )

    def _is_last_item_at_level(self, items: list[ConversationTreeItem], index: int, level: int) -> bool:
        """Check if the item at the given index is the last item at the specified level."""
        # Find all items at the same level
        items_at_level = [i for i, item in enumerate(items) if item.level == level]
        return index == items_at_level[-1] if items_at_level else False

    def _has_sibling_at_level(self, items: list[ConversationTreeItem], current_index: int, level: int) -> bool:
        """Check if there's a sibling at the specified level after the current item."""
        # Find all items at the specified level
        items_at_level = [i for i, item in enumerate(items) if item.level == level]
        
        # Check if there are items after the current index at this level
        for idx in items_at_level:
            if idx > current_index:
                return True
        
        return False

    def action_move_up(self) -> None:
        """Move selection up."""
        visible_items = self._get_visible_items()
        if not visible_items:
            return
        
        self.selected_index = (self.selected_index - 1) % len(visible_items)
        self._update_display()

    def action_move_down(self) -> None:
        """Move selection down."""
        visible_items = self._get_visible_items()
        if not visible_items:
            return
        
        self.selected_index = (self.selected_index + 1) % len(visible_items)
        self._update_display()

    def action_select(self) -> None:
        """Select the currently highlighted item."""
        visible_items = self._get_visible_items()
        if not visible_items:
            return
        
        selected_item = visible_items[self.selected_index]
        
        if selected_item.is_folder:
            # Toggle folder expansion
            folder_path = str(selected_item.path)
            if folder_path in self.expanded_folders:
                self.expanded_folders.remove(folder_path)
            else:
                self.expanded_folders.add(folder_path)
            self._update_display()
        else:
            # Select conversation file
            self.post_message(
                self.ConversationSelected(filepath=selected_item.path)
            )

    def action_collapse(self) -> None:
        """Collapse the current folder."""
        visible_items = self._get_visible_items()
        if not visible_items:
            return
        
        selected_item = visible_items[self.selected_index]
        if selected_item.is_folder:
            folder_path = str(selected_item.path)
            if folder_path in self.expanded_folders:
                self.expanded_folders.remove(folder_path)
                self._update_display()

    def action_expand(self) -> None:
        """Expand the current folder."""
        visible_items = self._get_visible_items()
        if not visible_items:
            return
        
        selected_item = visible_items[self.selected_index]
        if selected_item.is_folder:
            folder_path = str(selected_item.path)
            if folder_path not in self.expanded_folders:
                self.expanded_folders.add(folder_path)
                self._update_display()

    def action_close(self) -> None:
        """Close the selector without selecting."""
        self.post_message(self.ConversationClosed())

    def on_blur(self, event: events.Blur) -> None:
        """Refocus when blurred."""
        self.call_after_refresh(self.focus)
