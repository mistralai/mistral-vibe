"""Value types shared by Session state, events, and procedures."""

from typing import Literal

from ....session_values import OutputType as OutputType
from ....session_values import ProtocolError as ProtocolError
from ....session_values import UnknownValue as UnknownValue

type SessionId = str
type TurnId = str
type HistoryEntryId = str
type ToolCallId = str
type CallbackId = str
type EventId = str
type UnixMs = int
type Uri = str
type AbsolutePath = str
type MessageRole = Literal["system", "user", "assistant"]
type ToolKind = Literal["builtin", "local", "mcp", "client", "plugin", "connector"]
type GenerationStatus = Literal["generating", "complete"]
type SessionStatus = Literal["running", "waiting", "terminated"]
type TurnStatus = Literal["running", "blocked", "terminated"]

DEFAULT_INTERRUPT_REASON = "interrupt requested"
"""Reason recorded when a Turn interruption does not give one.

Clients and parent agents share it, so an interrupted Turn ends the same way
whoever interrupted it.
"""
