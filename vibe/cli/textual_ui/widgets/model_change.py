from __future__ import annotations

from vibe.app_server.models import PublicCheckpointEntry
from vibe.cli.textual_ui.widgets.status_message import StatusMessage


def model_change_model(entry: PublicCheckpointEntry) -> str | None:
    """The model a ``model_change`` checkpoint names, if it names one."""
    details = entry.details
    if not isinstance(details, dict):
        return None
    model = details.get("model")
    return model if isinstance(model, str) else None


class ModelChangeMessage(StatusMessage):
    """Marks where the session moved to another model.

    Settled on mount: the change has already taken effect by the time the
    Harness writes the checkpoint this renders.
    """

    def __init__(self, model: str) -> None:
        super().__init__()
        self.add_class("model-change-message")
        self._model = model

    def get_content(self) -> str:
        return f"Model changed to {self._model}"

    def on_mount(self) -> None:
        super().on_mount()
        self.stop_spinning(success=True)
