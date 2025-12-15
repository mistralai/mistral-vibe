from __future__ import annotations

from textual.widgets import Static

from vibe.core.modes import PREDEFINED_MODES, ModeConfig, ModeID


class ModeIndicator(Static):
    """Widget displaying the current operational mode with cycling support."""

    def __init__(
        self,
        mode_id: str = ModeID.NORMAL,
        available_modes: list[ModeConfig] | None = None,
    ) -> None:
        super().__init__()
        self.can_focus = False
        self._mode_id = mode_id
        self._previous_mode: ModeConfig | None = (
            None  # Track previous for O(1) CSS cleanup
        )
        # Cache available modes as dict for O(1) lookups
        if available_modes is not None:
            self._mode_dict = {mode.id: mode for mode in available_modes}
        else:
            self._mode_dict = dict(PREDEFINED_MODES)
        self._update_display()

    def _update_display(self) -> None:
        mode_config = self._mode_dict.get(self._mode_id)

        if not mode_config:
            # Fallback to normal if mode not found
            mode_config = self._mode_dict.get(ModeID.NORMAL)
            if mode_config:
                self._mode_id = ModeID.NORMAL
            else:
                # Last resort fallback if no modes available
                self.update("⏵ (shift+tab to toggle)")
                return

        # Clear previous mode's classes (O(1) instead of O(n))
        if self._previous_mode:
            self.remove_class(f"mode-{self._previous_mode.id}")
            self.remove_class(f"mode-{self._previous_mode.border_color}")

        # Set display text and add new classes
        self.update(
            f"{mode_config.ui_indicator} {mode_config.name.lower()} (shift+tab to toggle)"
        )
        self.add_class(f"mode-{mode_config.id}")
        self.add_class(f"mode-{mode_config.border_color}")

        # Track current mode for next update
        self._previous_mode = mode_config

    def cycle_mode(self) -> str:
        mode_ids = list(self._mode_dict.keys())

        if not mode_ids:
            return self._mode_id

        try:
            current_index = mode_ids.index(self._mode_id)
        except ValueError:
            # Current mode not in list, start from beginning
            current_index = -1

        next_index = (current_index + 1) % len(mode_ids)
        self._mode_id = mode_ids[next_index]
        self._update_display()
        return self._mode_id

    def set_mode(self, mode_id: str) -> None:
        if self._mode_dict.get(mode_id):
            self._mode_id = mode_id
            self._update_display()

    def update_available_modes(self, modes: list[ModeConfig]) -> None:
        self._mode_dict = {mode.id: mode for mode in modes}
        # Re-validate current mode is still available
        if not self._mode_dict.get(self._mode_id):
            self._mode_id = ModeID.NORMAL
        self._update_display()

    def get_mode(self) -> str:
        return self._mode_id

    # Backward compatibility method
    def set_auto_approve(self, enabled: bool) -> None:
        self.set_mode(ModeID.AUTO_APPROVE if enabled else ModeID.NORMAL)
