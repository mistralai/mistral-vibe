#!/usr/bin/env python3
"""
Telemetry Example for Mistral Vibe

This example demonstrates how to use the OpenTelemetry observability features
in Mistral Vibe, similar to Gemini CLI's telemetry system.
"""

import os
import time
from vibe.core.observability import ObservabilitySDK, ObservabilityConfig
from vibe.core.observability.tracing import trace_agent_execution, trace_tool_execution
from vibe.core.observability.metrics import get_metrics_manager


def main():
    print("Mistral Vibe Telemetry Example")
    print("=" * 50)

    # Configure telemetry via environment variables or programmatically
    # For this example, we'll use programmatic configuration

    config = ObservabilityConfig(
        enabled=True,
        export_target="console",  # Try "console", "file", or "otlp"
        development_mode=True,
        sampling_rate=1.0,
        log_level="DEBUG"
    )

    print(f"Configuration:")
    print(f"   Enabled: {config.enabled}")
    print(f"   Export Target: {config.export_target}")
    print(f"   Development Mode: {config.development_mode}")
    print(f"   Sampling Rate: {config.sampling_rate}")
    print()

    # Initialize SDK
    sdk = ObservabilitySDK(config)
    if sdk.initialize():
        print("Observability SDK initialized successfully")
    else:
        print("Failed to initialize Observability SDK")
        return
    print()

    # Demonstrate traced agent execution
    print("Running traced agent execution...")

    @trace_agent_execution
    def mock_agent_execution(prompt: str, model: str = "mistral-tiny", temperature: float = 0.7):
        """Mock agent execution that will be traced"""
        print(f"   Agent executing with model: {model}")
        time.sleep(0.5)  # Simulate work
        return f"Response to: {prompt}"

    # Execute the traced function
    result = mock_agent_execution("Hello, how are you?", model="mistral-small")
    print(f"   Result: {result}")
    print()

    # Demonstrate traced tool execution
    print("🛠️  Running traced tool execution...")

    class MockTool:
        def __init__(self, name: str):
            self.name = name
            self.tool_type = "mock"

    @trace_tool_execution
    def mock_tool_execution(tool: MockTool, command: str):
        """Mock tool execution that will be traced"""
        print(f"   Tool '{tool.name}' executing: {command}")
        time.sleep(0.3)  # Simulate work
        return f"Tool result: {command.upper()}"

    # Execute the traced tool
    mock_tool = MockTool("file_reader")
    tool_result = mock_tool_execution(mock_tool, "read file.txt")
    print(f"   Result: {tool_result}")
    print()

    # Demonstrate custom metrics
    print("Recording custom metrics...")
    metrics = get_metrics_manager()

    # Record a custom metric
    custom_counter = metrics.get_counter("example.custom_events", "Example custom events")
    custom_counter.add(5, {"source": "example"})
    print("   Recorded 5 custom events")
    print()

    # Keep the program running for a bit to allow metrics to export
    print("Waiting for metrics to export...")
    time.sleep(2)

    # Shutdown SDK
    sdk.shutdown()
    print("Observability SDK shutdown complete")

    print()
    print("Example completed!")
    print("   - Check the console output for telemetry data")
    print("   - Try different export targets by changing the config")
    print("   - Enable development mode for more detailed output")


if __name__ == "__main__":
    main()
