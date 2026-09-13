"""Tests for --auto-exit CLI argument."""

from __future__ import annotations

import sys
from unittest.mock import patch

from vibe.cli.entrypoint import parse_arguments


def test_auto_exit_argument_parsed_correctly():
    with patch.object(sys, "argv", ["vibe", "-p", "hello", "--auto-exit"]):
        args = parse_arguments()
        assert args.prompt == "hello"
        assert args.auto_exit is True


def test_auto_exit_default_is_false():
    with patch.object(sys, "argv", ["vibe"]):
        args = parse_arguments()
        assert args.auto_exit is False
