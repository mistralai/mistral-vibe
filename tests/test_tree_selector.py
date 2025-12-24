#!/usr/bin/env python3
"""Test the conversation tree selector."""

from pathlib import Path
from datetime import datetime
from vibe.cli.textual_ui.widgets.conversation_tree_selector import (
    ConversationTreeItem,
    ConversationTreeSelector,
)


class TestConversationTreeSelector:
    """Test suite for ConversationTreeSelector widget."""

    def test_basic_selector_creation(self):
        """Test basic selector creation and initialization."""
        # Create some test data
        test_items = [
            ConversationTreeItem(
                path=Path("/test/conversations"),
                name="conversations",
                is_folder=True,
                level=0,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1"),
                name="folder1",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder2"),
                name="folder2",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/conversation1.json"),
                name="conversation1",
                is_folder=False,
                date=datetime(2024, 1, 1, 12, 0),
                message_count=10,
                model="gpt-4",
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/conversation2.json"),
                name="conversation2",
                is_folder=False,
                date=datetime(2024, 1, 2, 12, 0),
                message_count=15,
                model="gpt-4",
                level=2,
            ),
        ]

        selector = ConversationTreeSelector(conversations=test_items)
        assert len(selector.all_items) == 5
        assert selector.selected_index == 0

    def test_visible_items_all_expanded(self):
        """Test visible items when all folders are expanded."""
        test_items = [
            ConversationTreeItem(
                path=Path("/test/conversations"),
                name="conversations",
                is_folder=True,
                level=0,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1"),
                name="folder1",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/conversation2.json"),
                name="conversation2",
                is_folder=False,
                date=datetime(2024, 1, 2, 12, 0),
                message_count=15,
                model="gpt-4",
                level=2,
            ),
        ]

        selector = ConversationTreeSelector(conversations=test_items)
        selector.expanded_folders = {str(item.path) for item in test_items if item.is_folder}
        visible = selector._get_visible_items()
        assert len(visible) == 3

    def test_visible_items_folder_collapsed(self):
        """Test visible items when a folder is collapsed."""
        test_items = [
            ConversationTreeItem(
                path=Path("/test/conversations"),
                name="conversations",
                is_folder=True,
                level=0,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1"),
                name="folder1",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/conversation2.json"),
                name="conversation2",
                is_folder=False,
                date=datetime(2024, 1, 2, 12, 0),
                message_count=15,
                model="gpt-4",
                level=2,
            ),
        ]

        selector = ConversationTreeSelector(conversations=test_items)
        selector.expanded_folders = {str(item.path) for item in test_items if item.is_folder}
        visible_all = selector._get_visible_items()
        
        selector.expanded_folders.remove(str(Path("/test/conversations/folder1")))
        visible_collapsed = selector._get_visible_items()
        assert len(visible_all) > len(visible_collapsed)

    def test_nested_tree_structure(self):
        """Test tree display with nested folders."""
        # Create test data with nested structure
        test_items = [
            ConversationTreeItem(
                path=Path("/test/conversations"),
                name="conversations",
                is_folder=True,
                level=0,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1"),
                name="folder1",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/subfolder1"),
                name="subfolder1",
                is_folder=True,
                level=2,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder2"),
                name="folder2",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/file1.json"),
                name="file1",
                is_folder=False,
                date=datetime(2024, 1, 1, 12, 0),
                message_count=10,
                model="gpt-4",
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/file2.json"),
                name="file2",
                is_folder=False,
                date=datetime(2024, 1, 2, 12, 0),
                message_count=15,
                model="gpt-4",
                level=2,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/subfolder1/nested_file1.json"),
                name="nested_file1",
                is_folder=False,
                date=datetime(2024, 1, 3, 12, 0),
                message_count=20,
                model="gpt-4",
                level=3,
            ),
        ]

        selector = ConversationTreeSelector(conversations=test_items)
        
        # Test with only root expanded
        selector.expanded_folders = set()
        visible = selector._get_visible_items()
        assert len(visible) == 1  # Only root should be visible
        
        # Test with folder1 expanded
        selector.expanded_folders = {str(Path("/test/conversations/folder1"))}
        visible = selector._get_visible_items()
        assert len(visible) == 3  # Root, folder1, and file2
        
        # Test with folder1 and subfolder1 expanded
        selector.expanded_folders = {
            str(Path("/test/conversations/folder1")),
            str(Path("/test/conversations/folder1/subfolder1")),
        }
        visible = selector._get_visible_items()
        assert len(visible) == 4  # Root, folder1, file2, and nested_file1

    def test_tree_behavior_multiple_levels(self):
        """Test tree selector behavior with multiple levels."""
        test_items = [
            # Root level
            ConversationTreeItem(
                path=Path("/test/conversations"),
                name="conversations",
                is_folder=True,
                level=0,
            ),
            # Level 1 - folders and conversations in root
            ConversationTreeItem(
                path=Path("/test/conversations/folder1"),
                name="folder1",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder2"),
                name="folder2",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/conversation1.json"),
                name="conversation1",
                is_folder=False,
                date=datetime(2024, 1, 1, 12, 0),
                message_count=10,
                model="gpt-4",
                level=1,
            ),
            # Level 2 - conversations in folder1
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/conversation2.json"),
                name="conversation2",
                is_folder=False,
                date=datetime(2024, 1, 2, 12, 0),
                message_count=15,
                model="gpt-4",
                level=2,
            ),
            # Level 3 - conversations in folder1/subfolder
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/subfolder"),
                name="subfolder",
                is_folder=True,
                level=2,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/subfolder/conversation3.json"),
                name="conversation3",
                is_folder=False,
                date=datetime(2024, 1, 3, 12, 0),
                message_count=20,
                model="gpt-4",
                level=3,
            ),
        ]

        selector = ConversationTreeSelector(conversations=test_items)
        
        # Test 1: All folders expanded
        selector.expanded_folders = {str(item.path) for item in test_items if item.is_folder}
        visible = selector._get_visible_items()
        assert len(visible) == 7
        
        # Test 2: Only root expanded
        selector.expanded_folders = {str(Path("/test/conversations"))}
        visible = selector._get_visible_items()
        assert len(visible) == 4  # Root + 3 level 1 items
        
        # Test 3: Root and folder1 expanded
        selector.expanded_folders = {
            str(Path("/test/conversations")),
            str(Path("/test/conversations/folder1")),
        }
        visible = selector._get_visible_items()
        assert len(visible) == 6  # Root + 3 level 1 + 2 folder1 items

    def test_tree_display_rendering(self):
        """Test tree display rendering logic."""
        test_items = [
            ConversationTreeItem(
                path=Path("/test/conversations"),
                name="conversations",
                is_folder=True,
                level=0,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1"),
                name="folder1",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder2"),
                name="folder2",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/file1.json"),
                name="file1",
                is_folder=False,
                date=datetime(2024, 1, 1, 12, 0),
                message_count=10,
                model="gpt-4",
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/file2.json"),
                name="file2",
                is_folder=False,
                date=datetime(2024, 1, 2, 12, 0),
                message_count=15,
                model="gpt-4",
                level=1,
            ),
        ]

        selector = ConversationTreeSelector(conversations=test_items)
        
        # Test with only root expanded
        selector.expanded_folders = set()
        visible = selector._get_visible_items()
        assert len(visible) == 1
        
        # Test with all folders expanded
        selector.expanded_folders = {str(item.path) for item in test_items if item.is_folder}
        visible = selector._get_visible_items()
        assert len(visible) == 5

    def test_folder_ordering(self):
        """Test folder ordering in tree display."""
        test_items = [
            # Root
            ConversationTreeItem(
                path=Path("/test/conversations"),
                name="conversations",
                is_folder=True,
                level=0,
            ),
            # Level 1 - folders and conversations at root (folders should come first)
            ConversationTreeItem(
                path=Path("/test/conversations/folder1"),
                name="folder1",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder2"),
                name="folder2",
                is_folder=True,
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/conversation1.json"),
                name="conversation1",
                is_folder=False,
                date=datetime(2024, 1, 1, 12, 0),
                message_count=10,
                model="gpt-4",
                level=1,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder1/conversation2.json"),
                name="conversation2",
                is_folder=False,
                date=datetime(2024, 1, 2, 12, 0),
                message_count=15,
                model="gpt-4",
                level=2,
            ),
            ConversationTreeItem(
                path=Path("/test/conversations/folder2/conversation3.json"),
                name="conversation3",
                is_folder=False,
                date=datetime(2024, 1, 3, 12, 0),
                message_count=20,
                model="gpt-4",
                level=2,
            ),
        ]

        selector = ConversationTreeSelector(conversations=test_items)
        selector.expanded_folders = {str(item.path) for item in test_items if item.is_folder}
        visible = selector._get_visible_items()
        
        # Check that folders come before conversations at the same level
        names_at_level_1 = [item.name for item in visible if item.level == 1]
        assert names_at_level_1 == ["folder1", "folder2", "conversation1"]
