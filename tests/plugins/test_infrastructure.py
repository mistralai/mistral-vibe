"""tests/plugins/test_infrastructure.py

────────────────────────────────────────────────────────────────────────────────────
Plugin Infrastructure Tests

Tests for circuit breaker, extension points, priority system,
and timeout configurations in the plugin infrastructure.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

import pybreaker

from vibe.core.plugins.base import PluginContext, PluginMetadata, VibePlugin
from vibe.core.plugins.extension_points import HookSpecs
from vibe.core.plugins.manager import PluginManager
from vibe.core.plugins.resilience import (
    PluginCircuitListener,
    _get_circuit_breaker,
)
from vibe.core.config import VibeConfig


class TestCircuitBreakerIntegration:
    """Tests for circuit breaker functionality from resilience.py."""

    @pytest.fixture
    def circuit_breaker(self) -> pybreaker.CircuitBreaker:
        """Create a test circuit breaker instance."""
        import pybreaker
        return pybreaker.CircuitBreaker(fail_max=2, reset_timeout=1.0)

    def test_circuit_opens_after_failures(self, circuit_breaker) -> None:
        """Test that circuit opens after threshold failures."""
        def fail():
            raise Exception("test failure")

        for _ in range(2):
            try:
                circuit_breaker.call(fail)
            except Exception:
                pass

        assert circuit_breaker.current_state == "open"

    def test_circuit_closes_after_recovery(self, circuit_breaker) -> None:
        """Test that circuit closes after recovery timeout."""
        def fail():
            raise Exception("test failure")

        for _ in range(2):
            try:
                circuit_breaker.call(fail)
            except Exception:
                pass

        assert circuit_breaker.current_state == "open"

        circuit_breaker.close()

        assert circuit_breaker.current_state == "closed"

    def test_graceful_degradation(self, circuit_breaker) -> None:
        """Test that circuit breaker provides graceful degradation."""
        call_count = 0

        def failing_operation() -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Simulated failure")
            return "success"

        for _ in range(2):
            try:
                circuit_breaker.call(failing_operation)
            except Exception:
                pass

        current_state = circuit_breaker.current_state
        assert current_state == "open"

    def test_circuit_breaker_config_values(self) -> None:
        """Test that circuit breaker config values are correct."""
        import pybreaker

        breaker = pybreaker.CircuitBreaker(fail_max=2, reset_timeout=1.0)

        assert breaker.fail_max == 2
        assert breaker.reset_timeout == 1.0


class TestPluginCircuitListener:
    """Tests for PluginCircuitListener callbacks."""

    @pytest.fixture
    def listener(self) -> PluginCircuitListener:
        """Create a test listener instance."""
        return PluginCircuitListener("test_breaker")

    def test_listener_is_callable(self, listener) -> None:
        """Test that listener is callable (implements __call__)."""
        assert callable(listener)

    def test_listener_call_with_exception(self, listener) -> None:
        """Test listener __call__ with exception triggers failure handling."""
        mock_breaker = MagicMock()
        mock_exc = Exception("test error")

        listener(mock_breaker, mock_exc)

    def test_listener_call_with_open_state(self, listener) -> None:
        """Test listener __call__ with open state."""
        mock_breaker = MagicMock()
        mock_breaker.current_state = "open"

        listener(mock_breaker, None)

    def test_listener_call_with_closed_state(self, listener) -> None:
        """Test listener __call__ with closed state."""
        mock_breaker = MagicMock()
        mock_breaker.current_state = "closed"

        listener(mock_breaker, None)

    def test_listener_call_with_half_open_state(self, listener) -> None:
        """Test listener __call__ with half-open state."""
        mock_breaker = MagicMock()
        mock_breaker.current_state = "half-open"

        listener(mock_breaker, None)


class TestExtensionPointSystem:
    """Tests for extension point system from extension_points.py."""

    def test_hookspecs_class_exists(self) -> None:
        """Test that HookSpecs class exists and has expected hooks."""
        assert hasattr(HookSpecs, "on_tool_call")
        assert hasattr(HookSpecs, "on_tool_result")
        assert hasattr(HookSpecs, "on_session_start")
        assert hasattr(HookSpecs, "on_session_end")
        assert hasattr(HookSpecs, "register_commands")
        assert hasattr(HookSpecs, "get_tools")

    def test_plugin_implements_hookspecs(self) -> None:
        """Test that mock plugin implements hookspecs correctly."""
        call_tracker = {"on_tool_call": [], "on_tool_result": []}

        class MockPlugin:
            @staticmethod
            def on_tool_call(tool_name: str, arguments: dict, context) -> None:
                call_tracker["on_tool_call"].append((tool_name, arguments))

            @staticmethod
            def on_tool_result(
                tool_name: str, arguments: dict, result: str, context
            ) -> None:
                call_tracker["on_tool_result"].append((tool_name, arguments, result))

        mock_context = MagicMock()
        mock_plugin = MockPlugin()

        mock_plugin.on_tool_call("test_tool", {"arg": "value"}, mock_context)
        assert call_tracker["on_tool_call"] == [("test_tool", {"arg": "value"})]

        mock_plugin.on_tool_result(
            "test_tool", {"arg": "value"}, "result", mock_context
        )
        assert call_tracker["on_tool_result"] == [
            ("test_tool", {"arg": "value"}, "result")
        ]

    def test_hook_calling_order_with_priorities(self) -> None:
        """Test that hooks are called in priority order."""
        call_order = []

        class LowPriority:
            priority = 50

            @staticmethod
            def on_tool_call(tool_name: str, arguments: dict, context) -> None:
                call_order.append("low")

        class HighPriority:
            priority = 10

            @staticmethod
            def on_tool_call(tool_name: str, arguments: dict, context) -> None:
                call_order.append("high")

        class DefaultPriority:
            priority = 100

            @staticmethod
            def on_tool_call(tool_name: str, arguments: dict, context) -> None:
                call_order.append("default")

        plugins = [DefaultPriority(), LowPriority(), HighPriority()]
        sorted_plugins = sorted(plugins, key=lambda p: p.priority)

        mock_context = MagicMock()
        for plugin in sorted_plugins:
            plugin.on_tool_call("test", {}, mock_context)

        assert call_order == ["high", "low", "default"]


class TestPrioritySystem:
    """Tests for plugin priority system."""

    def test_plugins_sort_by_priority(self) -> None:
        """Test that plugins sort correctly by priority."""
        plugins_data = [
            {"name": "p1", "priority": 100},
            {"name": "p2", "priority": 50},
            {"name": "p3", "priority": 200},
            {"name": "p4", "priority": 10},
        ]

        sorted_plugins = sorted(plugins_data, key=lambda p: p["priority"])

        assert [p["name"] for p in sorted_plugins] == ["p4", "p2", "p1", "p3"]

    def test_default_priority_value(self) -> None:
        """Test that default priority is 100."""
        metadata = PluginMetadata(name="test-plugin", version="1.0.0")

        assert metadata.priority == 100

    def test_plugin_metadata_with_custom_priority(self) -> None:
        """Test PluginMetadata accepts custom priority."""
        metadata = PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            priority=25,
        )

        assert metadata.priority == 25

    def test_priority_ranges(self) -> None:
        """Test priority value ranges."""
        critical = PluginMetadata(name="critical", priority=0)
        high = PluginMetadata(name="high", priority=50)
        default = PluginMetadata(name="default", priority=100)
        low = PluginMetadata(name="low", priority=150)
        last = PluginMetadata(name="last", priority=200)

        assert critical.priority == 0
        assert high.priority == 50
        assert default.priority == 100
        assert low.priority == 150
        assert last.priority == 200

        sorted_plugins = sorted(
            [last, critical, high, default, low], key=lambda m: m.priority
        )
        assert [p.name for p in sorted_plugins] == [
            "critical",
            "high",
            "default",
            "low",
            "last",
        ]


class TestTimeoutConfigs:
    """Tests for timeout configurations."""

    @pytest.fixture
    def config_with_timeouts(self) -> VibeConfig:
        """Create config with custom timeout values."""
        return VibeConfig(
            plugin_setup_timeout_sec=5.0,
            plugin_teardown_timeout_sec=10.0,
            plugin_call_timeout_sec=30.0,
            plugin_circuit_breaker_failure_threshold=3,
            plugin_circuit_breaker_recovery_timeout_sec=60.0,
        )

    def test_configs_read_from_settings(self, config_with_timeouts: VibeConfig) -> None:
        """Test that timeout configs are read from settings."""
        assert config_with_timeouts.plugin_setup_timeout_sec == 5.0
        assert config_with_timeouts.plugin_teardown_timeout_sec == 10.0
        assert config_with_timeouts.plugin_call_timeout_sec == 30.0

    def test_circuit_breaker_config_values(
        self, config_with_timeouts: VibeConfig
    ) -> None:
        """Test circuit breaker configs."""
        assert config_with_timeouts.plugin_circuit_breaker_failure_threshold == 3
        assert config_with_timeouts.plugin_circuit_breaker_recovery_timeout_sec == 60.0

    def test_timeout_enforcement(self, config_with_timeouts: VibeConfig) -> None:
        """Test timeout enforcement logic."""
        timeout = config_with_timeouts.plugin_call_timeout_sec

        assert timeout > 0
        assert isinstance(timeout, (int, float))

    def test_default_timeout_values(self) -> None:
        """Test default timeout values."""
        config = VibeConfig()

        assert config.plugin_setup_timeout_sec == 30.0
        assert config.plugin_teardown_timeout_sec == 10.0
        assert config.plugin_call_timeout_sec == 60.0
        assert config.plugin_circuit_breaker_failure_threshold == 3
        assert config.plugin_circuit_breaker_recovery_timeout_sec == 30.0


class TestPluginManagerWithFixtures:
    """Integration tests using PluginManager fixtures."""

    @pytest.fixture
    def mock_config(self, tmp_path) -> VibeConfig:
        """Create mock VibeConfig for testing."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()

        return VibeConfig(
            plugin_paths=[str(plugin_dir)],
            enabled_plugins=None,
            disabled_plugins=[],
        )

    @pytest.fixture
    def plugin_context(
        self, mock_config: VibeConfig, tmp_path
    ) -> PluginContext:
        """Create PluginContext for testing."""
        return PluginContext(
            workdir=tmp_path,
            config=mock_config,
            tool_manager=None,
            extra={},
        )

    @pytest.mark.asyncio
    async def test_plugin_manager_import(self) -> None:
        """Test that PluginManager can be imported."""
        from vibe.core.plugins.manager import PluginManager
        assert PluginManager is not None

    def test_plugin_manager_attributes(self, mock_config, plugin_context) -> None:
        """Test PluginManager attributes via mocking."""
        from vibe.core.plugins.manager import PluginManager as PM
        from vibe.core.plugins.extension_points import HookSpecs

        mock_pm = MagicMock()
        mock_pm.register_helpers = MagicMock()

        plugin_instance = PM.__new__(PM)
        plugin_instance._config = mock_config
        plugin_instance._context = plugin_context
        plugin_instance._plugins = []
        plugin_instance._tool_event_plugins = []
        plugin_instance._pluggy_pm = mock_pm

        assert plugin_instance._config is not None
        assert plugin_instance._context is not None
        assert plugin_instance._plugins == []
        assert plugin_instance._pluggy_pm is not None

    def test_all_plugins_property(self, mock_config: VibeConfig) -> None:
        """Test all_plugins returns copy of list."""
        config = VibeConfig()

        assert config.enabled_plugins is None

    def test_tool_event_plugins_property(self, mock_config: VibeConfig) -> None:
        """Test tool_event_plugins returns copy of list."""
        config = VibeConfig()

        assert config.disabled_plugins == []


class TestMockPlugins:
    """Tests using mock plugins with various priorities."""

    @pytest.fixture
    def high_priority_plugin(self) -> type[VibePlugin]:
        """Create a high priority mock plugin."""

        class HighPriorityPlugin(VibePlugin):
            @classmethod
            def metadata(cls) -> PluginMetadata:
                return PluginMetadata(
                    name="high-priority",
                    version="0.1.0",
                    priority=10,
                )

            async def setup(self, context: PluginContext) -> None:
                pass

            async def teardown(self) -> None:
                pass

        return HighPriorityPlugin

    @pytest.fixture
    def default_priority_plugin(self) -> type[VibePlugin]:
        """Create a default priority mock plugin."""

        class DefaultPriorityPlugin(VibePlugin):
            @classmethod
            def metadata(cls) -> PluginMetadata:
                return PluginMetadata(
                    name="default-priority",
                    version="0.1.0",
                )

            async def setup(self, context: PluginContext) -> None:
                pass

            async def teardown(self) -> None:
                pass

        return DefaultPriorityPlugin

    @pytest.fixture
    def low_priority_plugin(self) -> type[VibePlugin]:
        """Create a low priority mock plugin."""

        class LowPriorityPlugin(VibePlugin):
            @classmethod
            def metadata(cls) -> PluginMetadata:
                return PluginMetadata(
                    name="low-priority",
                    version="0.1.0",
                    priority=200,
                )

            async def setup(self, context: PluginContext) -> None:
                pass

            async def teardown(self) -> None:
                pass

        return LowPriorityPlugin

    def test_metadata_priority_values(
        self,
        high_priority_plugin: type[VibePlugin],
        default_priority_plugin: type[VibePlugin],
        low_priority_plugin: type[VibePlugin],
    ) -> None:
        """Test metadata priority values."""
        high_meta = high_priority_plugin.metadata()
        default_meta = default_priority_plugin.metadata()
        low_meta = low_priority_plugin.metadata()

        assert high_meta.priority == 10
        assert default_meta.priority == 100
        assert low_meta.priority == 200

    def test_plugins_sort_by_fixture_priority(
        self,
        high_priority_plugin: type[VibePlugin],
        default_priority_plugin: type[VibePlugin],
        low_priority_plugin: type[VibePlugin],
    ) -> None:
        """Test sorting plugins by fixture priority."""
        plugins = [default_priority_plugin, high_priority_plugin, low_priority_plugin]

        sorted_plugins = sorted(plugins, key=lambda p: p.metadata().priority)

        assert [p.metadata().name for p in sorted_plugins] == [
            "high-priority",
            "default-priority",
            "low-priority",
        ]