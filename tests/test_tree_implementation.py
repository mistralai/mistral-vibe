#!/usr/bin/env python3
"""Test the conversation tree implementation with real data."""

from pathlib import Path
from vibe.cli.textual_ui.utils import build_conversation_tree

def test_tree_building():
    """Test building the tree structure from actual conversations."""
    conv_dir = Path.cwd() / "conversations"
    
    if not conv_dir.exists():
        return
    
    # Use the utility function to build the tree
    tree_items = build_conversation_tree(conv_dir)
    
    if not tree_items:
        return
    
    # Verify tree structure
    for item in tree_items:
        assert item.name is not None
        assert item.path is not None
        assert isinstance(item.is_folder, bool)
        assert item.level >= 0
        if not item.is_folder:
            assert item.message_count >= 0
            assert item.model is not None

if __name__ == "__main__":
    test_tree_building()
