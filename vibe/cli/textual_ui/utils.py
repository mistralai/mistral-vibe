"""Utility functions for the Textual UI."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from vibe.cli.textual_ui.widgets.conversation_tree_selector import ConversationTreeItem
from vibe.core.agent import Agent
from vibe.core.paths.global_paths import ensure_conversations_dir_exists
from vibe.core.types import LLMMessage


def load_conversation_from_file(filepath: Path, agent: Agent | None) -> tuple[dict, list]:
    """Load conversation data from a JSON file and update agent messages.

    Args:
        filepath: Path to the conversation JSON file
        agent: The agent instance to load messages into

    Returns:
        Tuple of (data dict, messages list) or (None, None) if loading fails

    Raises:
        FileNotFoundError: If the file doesn't exist
        json.JSONDecodeError: If the file is not valid JSON
        ValueError: If the conversation data is invalid
    """
    # Validate filepath
    if not filepath.exists():
        raise FileNotFoundError(f"Conversation file not found: {filepath}")

    if not filepath.is_file():
        raise ValueError(f"Path is not a file: {filepath}")

    try:
        with open(filepath) as f:
            data = json.load(f)

        # Validate data structure
        if not isinstance(data, dict):
            raise ValueError(f"Conversation data must be a dictionary, got {type(data)}")

        messages = data.get("messages", [])

        if not messages:
            return None, None

        if not isinstance(messages, list):
            raise ValueError(f"Messages must be a list, got {type(messages)}")

        # Validate each message
        for i, msg_data in enumerate(messages):
            if not isinstance(msg_data, dict):
                raise ValueError(f"Message {i} must be a dictionary, got {type(msg_data)}")

            # Validate required fields
            if "role" not in msg_data:
                raise ValueError(f"Message {i} is missing required 'role' field")

            if "content" not in msg_data:
                raise ValueError(f"Message {i} is missing required 'content' field")

        # Load messages into agent
        if agent:
            # Clear current history (keep system prompt)
            if len(agent.messages) > 0:
                system_prompt = agent.messages[0]
                agent.messages = [system_prompt]

            # Add loaded messages
            for msg_data in messages:
                msg = LLMMessage.model_validate(msg_data)
                agent.messages.append(msg)

        return data, messages

    except FileNotFoundError as e:
        raise FileNotFoundError(f"Conversation file not found: {filepath}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in conversation file: {filepath}") from e
    except ValueError as e:
        # Re-raise ValueError with additional context
        raise ValueError(f"Invalid conversation data: {e}") from e
    except Exception as e:
        raise ValueError(f"Failed to load conversation: {e}") from e


def build_conversation_tree(conv_dir: Path | None = None) -> list[ConversationTreeItem]:
    """Build a tree structure of conversations and folders from the conversations directory.

    Args:
        conv_dir: Path to the conversations directory. If None, uses the default conversations directory.

    Returns:
        List of ConversationTreeItem objects representing the tree structure
    """
    if conv_dir is None:
        conv_dir = ensure_conversations_dir_exists()

    # Ensure the directory exists
    if not conv_dir.exists():
        return []
    tree_items: list[ConversationTreeItem] = []

    # Collect all folders
    folders = []
    for item in conv_dir.iterdir():
        if item.is_dir():
            folders.append(item)

    # Add root "conversations" item
    tree_items.append(
        ConversationTreeItem(
            path=conv_dir,
            name="conversations",
            is_folder=True,
            level=0,
        )
    )

    # Add folders as children of root
    for folder in sorted(folders, key=lambda f: f.name):
        # Calculate level based on depth in directory tree
        # Folders at root level (direct children of conversations) have level 1
        relative_path = folder.relative_to(conv_dir)
        level = len(relative_path.parts)
        tree_items.append(
            ConversationTreeItem(
                path=folder,
                name=folder.name,
                is_folder=True,
                level=level,
            )
        )

    # Find all conversation files recursively
    conv_files = list(conv_dir.rglob("*.json"))

    # Process conversation files
    for conv_file in conv_files:
        try:
            with open(conv_file) as f:
                data = json.load(f)

            # Validate JSON structure - ensure it's a dictionary
            if not isinstance(data, dict):
                continue  # Skip invalid files

            # Validate required fields and types
            if "messages" not in data or not isinstance(data["messages"], list):
                continue  # Skip files without valid messages

            # Validate message structure if present
            for msg in data["messages"]:
                if not isinstance(msg, dict):
                    continue  # Skip files with invalid message format

            # Get name (use stored name or filename)
            name = data.get("name", conv_file.stem)

            # Get timestamp
            # saved_at could be a relative timestamp (from old bug) or absolute timestamp
            # Unix timestamp threshold: 1970-01-01 00:00:00 is 0, reasonable files start after 2000 (946684800)
            # If timestamp is missing or looks like a relative timestamp, use file mtime
            timestamp = data.get("saved_at", conv_file.stat().st_mtime)

            # Check if timestamp looks like a Unix timestamp (greater than 2000-01-01)
            UNIX_TIMESTAMP_2000 = 946684800  # 2000-01-01 00:00:00 UTC
            if timestamp and (not isinstance(timestamp, (int, float)) or timestamp < UNIX_TIMESTAMP_2000):
                # This is likely a relative timestamp from the bug, use file mtime
                timestamp = conv_file.stat().st_mtime

            date = datetime.fromtimestamp(timestamp)

            # Get message count
            messages = data.get("messages", [])
            message_count = len(messages)

            # Get model
            model = data.get("model", "unknown")

            # Calculate level based on depth in directory tree
            # Files in root have level 1, files in subfolders have level 2, etc.
            relative_path = conv_file.relative_to(conv_dir)
            level = len(relative_path.parts)

            tree_items.append(
                ConversationTreeItem(
                    path=conv_file,
                    name=name,
                    is_folder=False,
                    date=date,
                    message_count=message_count,
                    model=model,
                    level=level,
                )
            )
        except Exception:
            # Skip files that can't be loaded
            continue

    # Sort by name within each level
    # First sort by level, then by whether it's a folder (folders first), then by name
    tree_items.sort(key=lambda c: (c.level, not c.is_folder, c.name))

    return tree_items
