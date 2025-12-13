"""
Basic functionality tests for Mistral Vibe Observability
"""

import pytest
from vibe.core.observability.config import ObservabilityConfig, load_config
from vibe.core.observability.sdk import ObservabilitySDK


def test_config_defaults():
    """Test that configuration has correct defaults"""
    config = ObservabilityConfig()
    
    assert config.enabled == False
    assert config.export_target == "console"
    assert config.development_mode == False
    assert config.sampling_rate == 1.0
    assert config.log_level == "INFO"
    assert config.otlp_endpoint == "http://localhost:4318"
    assert config.file_path == "mistral-vibe-telemetry.jsonl"
    assert config.metrics_enabled == True
    assert config.tracing_enabled == True
    assert config.logging_enabled == True


def test_config_creation():
    """Test custom configuration creation"""
    config = ObservabilityConfig(
        enabled=True,
        export_target="otlp",
        development_mode=True,
        sampling_rate=0.5,
        log_level="DEBUG"
    )
    
    assert config.enabled == True
    assert config.export_target == "otlp"
    assert config.development_mode == True
    assert config.sampling_rate == 0.5
    assert config.log_level == "DEBUG"


def test_sdk_initialization_disabled():
    """Test SDK initialization when disabled"""
    config = ObservabilityConfig(enabled=False)
    sdk = ObservabilitySDK(config)
    
    result = sdk.initialize()
    assert result == False
    assert sdk.is_initialized() == False


def test_sdk_initialization_enabled():
    """Test SDK initialization when enabled (mocked)"""
    # This test would normally require OpenTelemetry dependencies
    # For now, we'll just test the basic flow
    config = ObservabilityConfig(enabled=True, export_target="none")
    sdk = ObservabilitySDK(config)
    
    # Initialize and check state
    sdk.initialize()
    assert sdk.is_initialized() == True
    
    # Test shutdown
    sdk.shutdown()
    assert sdk.is_initialized() == False


def test_config_validation():
    """Test configuration validation"""
    # Test valid sampling rate
    config = ObservabilityConfig(sampling_rate=0.5)
    assert config.sampling_rate == 0.5
    
    # Test invalid sampling rate (should raise validation error)
    with pytest.raises(Exception):
        ObservabilityConfig(sampling_rate=1.5)  # > 1.0
    
    with pytest.raises(Exception):
        ObservabilityConfig(sampling_rate=-0.1)  # < 0.0


def test_context_manager():
    """Test SDK context manager functionality"""
    config = ObservabilityConfig(enabled=True, export_target="none")
    
    with ObservabilitySDK(config) as sdk:
        assert sdk.is_initialized() == True
    
    # After context manager, should be shutdown
    assert sdk.is_initialized() == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])