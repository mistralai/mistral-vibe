"""ChefChat Kitchen Bus - The async event-driven messaging system.

This module implements the Actor Model architecture for the kitchen:
- ChefMessage: Pydantic model for inter-station communication
- KitchenBus: Central message router using asyncio.PriorityQueue
- BaseStation: Abstract base class for all kitchen stations (agents)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pydantic
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class MessagePriority(IntEnum):
    """Priority levels for kitchen messages.

    The Head Chef's orders come first!
    """

    CRITICAL = 0  # "86 it!" - Immediate action required
    HIGH = 1  # "Fire!" - Start cooking now
    NORMAL = 2  # "All day" - Regular order
    LOW = 3  # "Mise en place" - Prep work


class ChefMessage(BaseModel):
    """A message passed between kitchen stations.

    In the kitchen metaphor:
    - sender: The station calling out (e.g., "sous_chef")
    - recipient: The station receiving ("line_cook" or "ALL" for broadcast)
    - payload: The actual order details
    - priority: How urgent this order is
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    sender: str = Field(description="Station name sending the message")
    recipient: str = Field(description="Station name or 'ALL' for broadcast")
    action: str = Field(description="The action to perform")
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)

    @pydantic.validator("payload", pre=True)
    def validate_payload(cls, v: Any) -> dict[str, Any]:
        """Ensure payload is always a dictionary.

        If None is passed, convert to empty dict.
        If non-dict is passed, wrap in 'content' key or raise error?
        For safety in kitchen, we coerce None->dict and error on others.
        """
        if v is None:
            return {}
        if not isinstance(v, dict):
            # Attempt to coerce or fail?
            # Let's be strict but helpful - if it's a string, maybe it's meant to be a simple message?
            # But bus.py logic expects structured data.
            # Let's raise ValueError to catch bad code early.
            raise ValueError(f"Payload must be a dictionary, got {type(v).__name__}")
        return v

    class Config:
        """Pydantic config."""

        use_enum_values = True


@dataclass(order=True)
class PrioritizedMessage:
    """Wrapper for priority queue ordering."""

    priority: int
    message: ChefMessage = field(compare=False)


class KitchenBus:
    """The central event bus connecting all kitchen stations.

    Uses asyncio.PriorityQueue for ordered message delivery.
    Stations subscribe to messages and the bus routes them accordingly.
    """

    def __init__(self) -> None:
        """Initialize the kitchen bus."""
        self._queue: asyncio.PriorityQueue[PrioritizedMessage] = asyncio.PriorityQueue()
        self._subscribers: dict[str, list[Callable[[ChefMessage], None]]] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def publish(self, message: ChefMessage) -> None:
        """Publish a message to the bus.

        Args:
            message: The ChefMessage to send
        """
        prioritized = PrioritizedMessage(priority=message.priority, message=message)
        await self._queue.put(prioritized)

    def subscribe(
        self, station_name: str, callback: Callable[[ChefMessage], None]
    ) -> None:
        """Subscribe a station to receive messages.

        Args:
            station_name: Name of the subscribing station
            callback: Function to call when message received
        """
        if station_name not in self._subscribers:
            self._subscribers[station_name] = []
        self._subscribers[station_name].append(callback)

    def unsubscribe(self, station_name: str) -> None:
        """Remove a station's subscriptions.

        Args:
            station_name: Name of the station to unsubscribe
        """
        if station_name in self._subscribers:
            del self._subscribers[station_name]

    async def _dispatch(self, message: ChefMessage) -> None:
        """Route a message to the appropriate subscribers.

        Args:
            message: The message to dispatch
        """
        recipients: list[str] = []

        if message.recipient == "ALL":
            # Broadcast to all stations
            recipients = list(self._subscribers.keys())
        elif message.recipient in self._subscribers:
            recipients = [message.recipient]

        for station in recipients:
            for callback in self._subscribers.get(station, []):
                try:
                    # Handle both sync and async callbacks
                    result = callback(message)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    # Log but don't crash the bus
                    logger.exception(
                        "Error dispatching message %s to station %s: %s",
                        message.id,
                        station,
                        e,
                    )

    async def start(self) -> None:
        """Start the bus message loop."""
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the bus gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Main message processing loop."""
        while self._running:
            try:
                # Get next message with timeout to allow checking _running
                prioritized = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                await self._dispatch(prioritized.message)
                self._queue.task_done()
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    @property
    def is_running(self) -> bool:
        """Check if the bus is running."""
        return self._running


class BaseStation(ABC):
    """Abstract base class for all kitchen stations (agents).

    Each station runs its own async loop, listening for messages
    and processing them according to its specialty.
    """

    def __init__(self, name: str, bus: KitchenBus) -> None:
        """Initialize a kitchen station.

        Args:
            name: Unique identifier for this station
            bus: The KitchenBus to connect to
        """
        self.name = name
        self._bus = bus
        self._inbox: asyncio.Queue[ChefMessage] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Register with the bus
        self._bus.subscribe(self.name, self._receive)

    def _receive(self, message: ChefMessage) -> None:
        """Callback for incoming messages.

        Args:
            message: Message received from the bus
        """
        # Non-blocking put to our inbox
        try:
            self._inbox.put_nowait(message)
        except asyncio.QueueFull:
            # TODO: Handle overflow - log warning
            pass

    async def send(
        self,
        recipient: str,
        action: str,
        payload: dict[str, Any] | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> None:
        """Send a message to another station.

        Args:
            recipient: Target station name or "ALL"
            action: The action to perform
            payload: Optional data to send
            priority: Message priority level
        """
        message = ChefMessage(
            sender=self.name,
            recipient=recipient,
            action=action,
            payload=payload or {},
            priority=priority,
        )
        await self._bus.publish(message)

    @abstractmethod
    async def handle(self, message: ChefMessage) -> None:
        """Process an incoming message.

        Subclasses must implement this to define their behavior.

        Args:
            message: The message to process
        """
        ...

    async def start(self) -> None:
        """Start the station's message loop."""
        self._running = True
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        """Stop the station gracefully."""
        self._running = False
        self._bus.unsubscribe(self.name)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _listen(self) -> None:
        """Main message listening loop."""
        while self._running:
            try:
                message = await asyncio.wait_for(self._inbox.get(), timeout=0.1)
                await self.handle(message)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    @property
    def is_running(self) -> bool:
        """Check if the station is running."""
        return self._running
