from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, TypedDict

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.theme import BUILTIN_THEMES
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig

THEMES = sorted(k for k in BUILTIN_THEMES if k != "textual-ansi")


class ConfigViewState(StrEnum):
    MAIN = "main"
    PROVIDERS_LIST = "providers_list"
    PROVIDER_EDIT = "provider_edit"
    MODELS_LIST = "models_list"
    MODEL_EDIT = "model_edit"


class SettingDefinition(TypedDict):
    key: str
    label: str
    type: str
    options: list[str]
    value: str


class ConfigApp(Container):
    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("space", "toggle_setting", "Toggle", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("a", "add_item", "Add", show=False),
        Binding("d", "delete_item", "Delete", show=False),
        Binding("left", "go_back", "Back", show=False),
        Binding("tab", "next_field", "Next Field", show=False),
        Binding("shift+tab", "prev_field", "Prev Field", show=False),
    ]

    class SettingChanged(Message):
        def __init__(self, key: str, value: str) -> None:
            super().__init__()
            self.key = key
            self.value = value

    class ConfigClosed(Message):
        def __init__(self, changes: dict) -> None:
            super().__init__()
            self.changes = changes


    def __init__(self, config: VibeConfig) -> None:
        super().__init__(id="config-app")
        self.config = config
        self.view_state = ConfigViewState.MAIN
        self.selected_index = 0
        self.changes: dict[str, str] = {}

        # Track providers/models changes
        self.providers_changes: list[ProviderConfig] = list(config.providers)
        self.models_changes: list[ModelConfig] = list(config.models)
        self.providers_deleted: set[str] = set()
        self.models_deleted: set[str] = set()

        # Edit state
        self.editing_provider_index: int | None = None
        self.editing_model_index: int | None = None
        self.editing_provider_new: bool = False
        self.editing_model_new: bool = False

        # Main settings
        self.settings: list[SettingDefinition] = [
            {
                "key": "active_model",
                "label": "Model",
                "type": "cycle",
                "options": [m.alias for m in self.config.models],
                "value": self.config.active_model,
            },
            {
                "key": "textual_theme",
                "label": "Theme",
                "type": "cycle",
                "options": THEMES,
                "value": self.config.textual_theme,
            },
            {
                "key": "providers",
                "label": "Providers",
                "type": "navigate",
                "options": [],
                "value": f"({len(self.config.providers)})",
            },
            {
                "key": "models",
                "label": "Models",
                "type": "navigate",
                "options": [],
                "value": f"({len(self.config.models)})",
            },
        ]

        self.title_widget: Static | None = None
        self.content_container: Container | None = None
        self.help_widget: Static | None = None
        self.list_widgets: list[Static] = []  # Track list item widgets

        # Form editing state
        self.form_inputs: list[Input] = []  # Track form input widgets
        self.current_form_field: int = 0  # Current field index in form

    def compose(self) -> ComposeResult:
        with Vertical(id="config-content"):
            self.title_widget = Static("Settings", classes="settings-title")
            yield self.title_widget

            self.content_container = Vertical(id="config-content-body")
            yield self.content_container

            self.help_widget = Static(
                "↑↓ navigate  Space/Enter toggle  ESC exit", classes="settings-help"
            )
            yield self.help_widget

    def on_mount(self) -> None:
        self._show_view(self.view_state)
        self.focus()

    def _show_view(self, view: ConfigViewState) -> None:
        self.view_state = view
        if not self.content_container:
            return

        # Clear existing content
        for widget in list(self.content_container.children):
            widget.remove()
        self.list_widgets.clear()
        self.form_inputs.clear()
        self.current_form_field = 0

        match view:
            case ConfigViewState.MAIN:
                self._show_main_view()
            case ConfigViewState.PROVIDERS_LIST:
                self._show_providers_list()
            case ConfigViewState.PROVIDER_EDIT:
                self._show_provider_edit()
            case ConfigViewState.MODELS_LIST:
                self._show_models_list()
            case ConfigViewState.MODEL_EDIT:
                self._show_model_edit()

        self._update_help_text()

    def _show_main_view(self) -> None:
        if not self.content_container:
            return

        for _setting in self.settings:
            widget = Static("", classes="settings-option")
            self.content_container.mount(widget)
        self._update_main_display()

    def _update_main_display(self) -> None:
        if not self.content_container:
            return

        widgets = list(self.content_container.children)
        for i, (setting, widget) in enumerate(zip(self.settings, widgets, strict=True)):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "

            label: str = setting["label"]
            value: str = self.changes.get(setting["key"], setting["value"])

            if setting["type"] == "navigate":
                text = f"{cursor}{label} {value}"
            else:
                text = f"{cursor}{label}: {value}"

            widget.update(text)

            widget.remove_class("settings-cursor-selected")
            widget.remove_class("settings-value-cycle-selected")
            widget.remove_class("settings-value-cycle-unselected")

            if is_selected:
                widget.add_class("settings-value-cycle-selected")
            else:
                widget.add_class("settings-value-cycle-unselected")

    def _show_providers_list(self) -> None:
        if not self.content_container:
            return

        # Show list of providers
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        self.list_widgets.clear()
        for _provider in providers:
            widget = Static("", classes="settings-option")
            self.content_container.mount(widget)
            self.list_widgets.append(widget)
        self._update_providers_display()

    def _update_providers_display(self) -> None:
        if not self.content_container:
            return

        active_provider = None
        try:
            active_model = self.config.get_active_model()
            active_provider = self.config.get_provider_for_model(active_model).name
        except (ValueError, AttributeError):
            pass

        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]

        # Use tracked widgets instead of querying children
        if len(providers) != len(self.list_widgets):
            return

        for i, (provider, widget) in enumerate(zip(providers, self.list_widgets, strict=True)):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "
            active_marker = " [active]" if provider.name == active_provider else ""
            text = f"{cursor}{provider.name} ({provider.api_base}){active_marker}"
            widget.update(text)

            widget.remove_class("settings-cursor-selected")
            widget.remove_class("settings-value-cycle-selected")
            widget.remove_class("settings-value-cycle-unselected")

            if is_selected:
                widget.add_class("settings-value-cycle-selected")
            else:
                widget.add_class("settings-value-cycle-unselected")

    def _show_provider_edit(self) -> None:
        if not self.content_container:
            return

        from vibe.core.config import Backend

        provider = None
        if self.editing_provider_index is not None:
            providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
            if 0 <= self.editing_provider_index < len(providers):
                provider = providers[self.editing_provider_index]

        is_new = self.editing_provider_new or provider is None

        title = Static(f"{'Add' if is_new else 'Edit'} Provider", classes="settings-title")
        self.content_container.mount(title)

        # Create form fields
        self.form_inputs.clear()

        # Name field
        name_label = Static("Name:", classes="settings-form-label")
        self.content_container.mount(name_label)
        name_input = Input(
            value=provider.name if provider else "new-provider",
            placeholder="Provider name",
            id="provider-name",
            classes="settings-form-input",
        )
        name_input.can_focus = True
        self.content_container.mount(name_input)
        self.form_inputs.append(name_input)

        # API Base field
        api_base_label = Static("API Base:", classes="settings-form-label")
        self.content_container.mount(api_base_label)
        api_base_input = Input(
            value=provider.api_base if provider else "http://localhost:8080/v1",
            placeholder="http://localhost:8080/v1",
            id="provider-api-base",
            classes="settings-form-input",
        )
        api_base_input.can_focus = True
        self.content_container.mount(api_base_input)
        self.form_inputs.append(api_base_input)

        # API Key Env Var field
        api_key_label = Static("API Key Env Var:", classes="settings-form-label")
        self.content_container.mount(api_key_label)
        api_key_input = Input(
            value=provider.api_key_env_var if provider else "",
            placeholder="MISTRAL_API_KEY (leave empty if not needed)",
            id="provider-api-key-env",
            classes="settings-form-input",
        )
        api_key_input.can_focus = True
        self.content_container.mount(api_key_input)
        self.form_inputs.append(api_key_input)

        # Backend field (cycle through options)
        backend_label = Static("Backend:", classes="settings-form-label")
        self.content_container.mount(backend_label)
        backend_value = provider.backend.value if provider else Backend.GENERIC.value
        backend_input = Input(
            value=backend_value,
            placeholder="generic or mistral",
            id="provider-backend",
            classes="settings-form-input",
        )
        backend_input.can_focus = True
        self.content_container.mount(backend_input)
        self.form_inputs.append(backend_input)

        # Focus first field
        if self.form_inputs:
            self.current_form_field = 0
            self.call_after_refresh(lambda: self.form_inputs[0].focus())

    def _show_models_list(self) -> None:
        if not self.content_container:
            return

        models = [m for m in self.models_changes if m.alias not in self.models_deleted]
        self.list_widgets.clear()
        for _model in models:
            widget = Static("", classes="settings-option")
            self.content_container.mount(widget)
            self.list_widgets.append(widget)
        self._update_models_display()

    def _update_models_display(self) -> None:
        if not self.content_container:
            return

        models = [m for m in self.models_changes if m.alias not in self.models_deleted]

        # Use tracked widgets instead of querying children
        if len(models) != len(self.list_widgets):
            return

        for i, (model, widget) in enumerate(zip(models, self.list_widgets, strict=True)):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "
            active_marker = " [active]" if model.alias == self.config.active_model else ""
            text = f"{cursor}{model.alias} ({model.provider}){active_marker}"
            widget.update(text)

            widget.remove_class("settings-cursor-selected")
            widget.remove_class("settings-value-cycle-selected")
            widget.remove_class("settings-value-cycle-unselected")

            if is_selected:
                widget.add_class("settings-value-cycle-selected")
            else:
                widget.add_class("settings-value-cycle-unselected")

    def _show_model_edit(self) -> None:
        if not self.content_container:
            return

        model = None
        if self.editing_model_index is not None:
            models = [m for m in self.models_changes if m.alias not in self.models_deleted]
            if 0 <= self.editing_model_index < len(models):
                model = models[self.editing_model_index]

        is_new = self.editing_model_new or model is None

        title = Static(f"{'Add' if is_new else 'Edit'} Model", classes="settings-title")
        self.content_container.mount(title)

        # Create form fields
        self.form_inputs.clear()

        # Alias field
        alias_label = Static("Alias:", classes="settings-form-label")
        self.content_container.mount(alias_label)
        alias_input = Input(
            value=model.alias if model else "new-model",
            placeholder="Model alias",
            id="model-alias",
            classes="settings-form-input",
        )
        alias_input.can_focus = True
        self.content_container.mount(alias_input)
        self.form_inputs.append(alias_input)

        # Name field
        name_label = Static("Name:", classes="settings-form-label")
        self.content_container.mount(name_label)
        name_input = Input(
            value=model.name if model else "new-model",
            placeholder="Model name",
            id="model-name",
            classes="settings-form-input",
        )
        name_input.can_focus = True
        self.content_container.mount(name_input)
        self.form_inputs.append(name_input)

        # Provider field (cycle through available providers)
        provider_label = Static("Provider:", classes="settings-form-label")
        self.content_container.mount(provider_label)
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        provider_value = model.provider if model else (providers[0].name if providers else "mistral")
        provider_input = Input(
            value=provider_value,
            placeholder="Provider name",
            id="model-provider",
            classes="settings-form-input",
        )
        provider_input.can_focus = True
        self.content_container.mount(provider_input)
        self.form_inputs.append(provider_input)

        # Temperature field
        temp_label = Static("Temperature:", classes="settings-form-label")
        self.content_container.mount(temp_label)
        temp_input = Input(
            value=str(model.temperature) if model else "0.2",
            placeholder="0.2",
            id="model-temperature",
            classes="settings-form-input",
        )
        temp_input.can_focus = True
        self.content_container.mount(temp_input)
        self.form_inputs.append(temp_input)

        # Input Price field
        input_price_label = Static("Input Price:", classes="settings-form-label")
        self.content_container.mount(input_price_label)
        input_price_input = Input(
            value=str(model.input_price) if model else "0.0",
            placeholder="0.0",
            id="model-input-price",
            classes="settings-form-input",
        )
        input_price_input.can_focus = True
        self.content_container.mount(input_price_input)
        self.form_inputs.append(input_price_input)

        # Output Price field
        output_price_label = Static("Output Price:", classes="settings-form-label")
        self.content_container.mount(output_price_label)
        output_price_input = Input(
            value=str(model.output_price) if model else "0.0",
            placeholder="0.0",
            id="model-output-price",
            classes="settings-form-input",
        )
        output_price_input.can_focus = True
        self.content_container.mount(output_price_input)
        self.form_inputs.append(output_price_input)

        # Focus first field
        if self.form_inputs:
            self.current_form_field = 0
            self.call_after_refresh(lambda: self.form_inputs[0].focus())

    def _update_help_text(self) -> None:
        if not self.help_widget:
            return

        match self.view_state:
            case ConfigViewState.MAIN:
                help_text = "↑↓ navigate  Space/Enter toggle  ESC exit"
            case ConfigViewState.PROVIDERS_LIST | ConfigViewState.MODELS_LIST:
                help_text = "↑↓ navigate  Enter edit  [a] add  [d] delete  ESC back"
            case ConfigViewState.PROVIDER_EDIT | ConfigViewState.MODEL_EDIT:
                help_text = "Tab navigate  Enter save  ESC cancel"
            case _:
                help_text = "↑↓ navigate  ESC back"

        self.help_widget.update(help_text)

    def action_move_up(self) -> None:
        max_items = self._get_max_items()
        if max_items == 0:
            return
        self.selected_index = (self.selected_index - 1) % max_items
        self._update_current_view()

    def action_move_down(self) -> None:
        max_items = self._get_max_items()
        if max_items == 0:
            return
        self.selected_index = (self.selected_index + 1) % max_items
        self._update_current_view()

    def _get_max_items(self) -> int:
        match self.view_state:
            case ConfigViewState.MAIN:
                return len(self.settings)
            case ConfigViewState.PROVIDERS_LIST:
                return len([p for p in self.providers_changes if p.name not in self.providers_deleted])
            case ConfigViewState.MODELS_LIST:
                return len([m for m in self.models_changes if m.alias not in self.models_deleted])
            case _:
                return 1

    def _update_current_view(self) -> None:
        match self.view_state:
            case ConfigViewState.MAIN:
                self._update_main_display()
            case ConfigViewState.PROVIDERS_LIST:
                self._update_providers_display()
            case ConfigViewState.MODELS_LIST:
                self._update_models_display()

    def action_select(self) -> None:
        match self.view_state:
            case ConfigViewState.MAIN:
                setting = self.settings[self.selected_index]
                if setting["type"] == "navigate":
                    if setting["key"] == "providers":
                        self._navigate_to_providers_list()
                    elif setting["key"] == "models":
                        self._navigate_to_models_list()
                else:
                    self.action_toggle_setting()
            case ConfigViewState.PROVIDERS_LIST:
                self._edit_provider()
            case ConfigViewState.MODELS_LIST:
                self._edit_model()
            case ConfigViewState.PROVIDER_EDIT | ConfigViewState.MODEL_EDIT:
                # If on last field, save and go back
                # Otherwise, move to next field
                if self.current_form_field < len(self.form_inputs) - 1:
                    self.current_form_field += 1
                    self.form_inputs[self.current_form_field].focus()
                else:
                    # Last field - save and go back
                    self._save_edit()
                    self._go_back()

    def action_toggle_setting(self) -> None:
        if self.view_state != ConfigViewState.MAIN:
            return

        setting = self.settings[self.selected_index]
        key: str = setting["key"]
        current: str = self.changes.get(key, setting["value"])

        options: list[str] = setting["options"]
        new_value: str
        try:
            current_idx = options.index(current)
            next_idx = (current_idx + 1) % len(options)
            new_value = options[next_idx]
        except (ValueError, IndexError):
            new_value = options[0] if options else current

        self.changes[key] = new_value
        self.post_message(self.SettingChanged(key=key, value=new_value))
        self._update_main_display()

    def action_add_item(self) -> None:
        match self.view_state:
            case ConfigViewState.PROVIDERS_LIST:
                self._add_provider()
            case ConfigViewState.MODELS_LIST:
                self._add_model()

    def action_delete_item(self) -> None:
        match self.view_state:
            case ConfigViewState.PROVIDERS_LIST:
                self._delete_provider()
            case ConfigViewState.MODELS_LIST:
                self._delete_model()

    def action_go_back(self) -> None:
        self._go_back()

    def action_next_field(self) -> None:
        """Handle Tab key - move to next field in forms."""
        if self.view_state in (ConfigViewState.PROVIDER_EDIT, ConfigViewState.MODEL_EDIT):
            if self.current_form_field < len(self.form_inputs) - 1:
                self.current_form_field += 1
                self.form_inputs[self.current_form_field].focus()
            # If on last field, Tab does nothing (Enter saves)

    def action_prev_field(self) -> None:
        """Handle Shift+Tab - move to previous field in forms."""
        if self.view_state in (ConfigViewState.PROVIDER_EDIT, ConfigViewState.MODEL_EDIT):
            if self.current_form_field > 0:
                self.current_form_field -= 1
                self.form_inputs[self.current_form_field].focus()

    def _go_back(self) -> None:
        match self.view_state:
            case ConfigViewState.PROVIDERS_LIST | ConfigViewState.MODELS_LIST:
                self.view_state = ConfigViewState.MAIN
                self.selected_index = 0
                self._show_view(ConfigViewState.MAIN)
            case ConfigViewState.PROVIDER_EDIT:
                self._navigate_to_providers_list()
            case ConfigViewState.MODEL_EDIT:
                self._navigate_to_models_list()

    def _navigate_to_providers_list(self) -> None:
        self.view_state = ConfigViewState.PROVIDERS_LIST
        self.selected_index = 0
        self._show_view(ConfigViewState.PROVIDERS_LIST)

    def _navigate_to_models_list(self) -> None:
        self.view_state = ConfigViewState.MODELS_LIST
        self.selected_index = 0
        self._show_view(ConfigViewState.MODELS_LIST)

    def _edit_provider(self) -> None:
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        if 0 <= self.selected_index < len(providers):
            self.editing_provider_index = self.selected_index
            self.editing_provider_new = False
            self.view_state = ConfigViewState.PROVIDER_EDIT
            self._show_view(ConfigViewState.PROVIDER_EDIT)

    def _add_provider(self) -> None:
        from vibe.core.config import Backend, ProviderConfig

        # Create a new default provider
        new_provider = ProviderConfig(
            name="new-provider",
            api_base="http://localhost:8080/v1",
            api_key_env_var="",
            backend=Backend.GENERIC,
        )
        self.providers_changes.append(new_provider)
        # Index is based on filtered list (new item is at the end)
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        self.editing_provider_index = len(providers) - 1
        self.editing_provider_new = True
        self.view_state = ConfigViewState.PROVIDER_EDIT
        self._show_view(ConfigViewState.PROVIDER_EDIT)

    def _delete_provider(self) -> None:
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        if 0 <= self.selected_index < len(providers):
            provider = providers[self.selected_index]
            self.providers_deleted.add(provider.name)
            # Also delete models using this provider
            for model in self.models_changes:
                if model.provider == provider.name:
                    self.models_deleted.add(model.alias)
            # Adjust selected index if needed
            if self.selected_index >= len(providers) - 1:
                self.selected_index = max(0, len(providers) - 2)
            self._update_providers_display()

    def _edit_model(self) -> None:
        models = [m for m in self.models_changes if m.alias not in self.models_deleted]
        if 0 <= self.selected_index < len(models):
            self.editing_model_index = self.selected_index
            self.editing_model_new = False
            self.view_state = ConfigViewState.MODEL_EDIT
            self._show_view(ConfigViewState.MODEL_EDIT)

    def _add_model(self) -> None:
        from vibe.core.config import ModelConfig

        # Create a new default model
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        provider_name = providers[0].name if providers else "mistral"

        new_model = ModelConfig(
            name="new-model",
            provider=provider_name,
            alias="new-model",
            temperature=0.2,
        )
        self.models_changes.append(new_model)
        # Index is based on filtered list (new item is at the end)
        models = [m for m in self.models_changes if m.alias not in self.models_deleted]
        self.editing_model_index = len(models) - 1
        self.editing_model_new = True
        self.view_state = ConfigViewState.MODEL_EDIT
        self._show_view(ConfigViewState.MODEL_EDIT)

    def _delete_model(self) -> None:
        models = [m for m in self.models_changes if m.alias not in self.models_deleted]
        if 0 <= self.selected_index < len(models):
            model = models[self.selected_index]
            # Don't allow deleting the active model
            if model.alias == self.config.active_model:
                return
            self.models_deleted.add(model.alias)
            # Adjust selected index if needed
            if self.selected_index >= len(models) - 1:
                self.selected_index = max(0, len(models) - 2)
            self._update_models_display()

    def _save_edit(self) -> None:
        match self.view_state:
            case ConfigViewState.PROVIDER_EDIT:
                self._save_provider_edit()
            case ConfigViewState.MODEL_EDIT:
                self._save_model_edit()

    def _save_provider_edit(self) -> None:
        from vibe.core.config import Backend, ProviderConfig

        if len(self.form_inputs) < 4:
            return

        name = self.form_inputs[0].value.strip()
        api_base = self.form_inputs[1].value.strip()
        api_key_env_var = self.form_inputs[2].value.strip()
        backend_str = self.form_inputs[3].value.strip().lower()

        if not name or not api_base:
            return  # Invalid - required fields missing

        # Check for duplicate provider names (unless editing existing with same name)
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        if self.editing_provider_index is not None and 0 <= self.editing_provider_index < len(providers):
            old_provider = providers[self.editing_provider_index]
            # Allow keeping same name
            if name != old_provider.name and any(p.name == name for p in providers):
                return  # Duplicate name
        elif any(p.name == name for p in providers):
            return  # Duplicate name

        # Parse backend
        try:
            backend = Backend(backend_str) if backend_str else Backend.GENERIC
        except ValueError:
            backend = Backend.GENERIC

        # Create or update provider
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        if (
            self.editing_provider_index is not None
            and 0 <= self.editing_provider_index < len(providers)
        ):
            # Update existing
            old_provider = providers[self.editing_provider_index]
            # Find in full list and update
            for i, p in enumerate(self.providers_changes):
                if p.name == old_provider.name:
                    self.providers_changes[i] = ProviderConfig(
                        name=name,
                        api_base=api_base,
                        api_key_env_var=api_key_env_var,
                        backend=backend,
                    )
                    break
        else:
            # New provider
            new_provider = ProviderConfig(
                name=name,
                api_base=api_base,
                api_key_env_var=api_key_env_var,
                backend=backend,
            )
            self.providers_changes.append(new_provider)

    def _save_model_edit(self) -> None:
        from vibe.core.config import ModelConfig

        if len(self.form_inputs) < 6:
            return

        alias = self.form_inputs[0].value.strip()
        name = self.form_inputs[1].value.strip()
        provider = self.form_inputs[2].value.strip()
        temp_str = self.form_inputs[3].value.strip()
        input_price_str = self.form_inputs[4].value.strip()
        output_price_str = self.form_inputs[5].value.strip()

        if not alias or not name or not provider:
            return  # Invalid - required fields missing

        # Check provider exists
        providers = [p for p in self.providers_changes if p.name not in self.providers_deleted]
        if not any(p.name == provider for p in providers):
            return  # Provider doesn't exist

        # Check for duplicate model aliases (unless editing existing with same alias)
        models = [m for m in self.models_changes if m.alias not in self.models_deleted]
        if self.editing_model_index is not None and 0 <= self.editing_model_index < len(models):
            old_model = models[self.editing_model_index]
            # Allow keeping same alias
            if alias != old_model.alias and any(m.alias == alias for m in models):
                return  # Duplicate alias
        elif any(m.alias == alias for m in models):
            return  # Duplicate alias

        # Parse numeric fields
        try:
            temperature = float(temp_str) if temp_str else 0.2
        except ValueError:
            temperature = 0.2

        try:
            input_price = float(input_price_str) if input_price_str else 0.0
        except ValueError:
            input_price = 0.0

        try:
            output_price = float(output_price_str) if output_price_str else 0.0
        except ValueError:
            output_price = 0.0

        # Create or update model
        models = [m for m in self.models_changes if m.alias not in self.models_deleted]
        if self.editing_model_index is not None and 0 <= self.editing_model_index < len(models):
            # Update existing
            old_model = models[self.editing_model_index]
            # Find in full list and update
            for i, m in enumerate(self.models_changes):
                if m.alias == old_model.alias:
                    self.models_changes[i] = ModelConfig(
                        name=name,
                        provider=provider,
                        alias=alias,
                        temperature=temperature,
                        input_price=input_price,
                        output_price=output_price,
                    )
                    # Update active_model options if this model's alias changed
                    if old_model.alias != alias and self.config.active_model == old_model.alias:
                        self.changes["active_model"] = alias
                    break
        else:
            # New model
            new_model = ModelConfig(
                name=name,
                provider=provider,
                alias=alias,
                temperature=temperature,
                input_price=input_price,
                output_price=output_price,
            )
            self.models_changes.append(new_model)

    def action_close(self) -> None:
        # Collect all changes
        all_changes: dict = {}

        # Basic settings
        for key, value in self.changes.items():
            all_changes[key] = value

        # Providers and models will be handled separately
        all_changes["_providers"] = [
            p.model_dump() for p in self.providers_changes
            if p.name not in self.providers_deleted
        ]
        all_changes["_models"] = [
            m.model_dump() for m in self.models_changes
            if m.alias not in self.models_deleted
        ]
        all_changes["_providers_deleted"] = list(self.providers_deleted)
        all_changes["_models_deleted"] = list(self.models_deleted)

        # Update active_model options in settings if models changed
        if "_models" in all_changes:
            models = [m for m in self.models_changes if m.alias not in self.models_deleted]
            for setting in self.settings:
                if setting["key"] == "active_model":
                    setting["options"] = [m.alias for m in models]
                    break

        self.post_message(self.ConfigClosed(changes=all_changes))

    def on_blur(self, event: events.Blur) -> None:
        # Don't refocus if we're in a form (let Input widgets handle focus)
        if self.view_state in (ConfigViewState.PROVIDER_EDIT, ConfigViewState.MODEL_EDIT):
            return
        self.call_after_refresh(self.focus)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in Input widgets - move to next field or save."""
        if self.view_state in (ConfigViewState.PROVIDER_EDIT, ConfigViewState.MODEL_EDIT):
            event.stop()
            # Find which input was submitted
            for i, inp in enumerate(self.form_inputs):
                if inp == event.input:
                    self.current_form_field = i
                    if i < len(self.form_inputs) - 1:
                        # Move to next field
                        self.current_form_field = i + 1
                        self.form_inputs[self.current_form_field].focus()
                    else:
                        # Last field - save and go back
                        self._save_edit()
                        self._go_back()
                    break
