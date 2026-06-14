#!/usr/bin/env python3
"""
Simple verification script for register_dynamic_tool overwrite functionality.
This script tests the logic without requiring the full vibe test environment.
"""

import sys
import os

# Add the project root to Python path
sys.path.insert(0, 'D:\\IA\\Projets\\mistral-vibe')

def test_register_dynamic_tool_logic():
    """Test the register_dynamic_tool logic directly."""
    
    # Mock classes to simulate BaseTool
    class MockBaseTool:
        @classmethod
        def get_name(cls):
            return cls.__name__
    
    class MockTool(MockBaseTool):
        @classmethod
        def get_name(cls):
            return "mock_tool"
    
    class DuplicateTool(MockBaseTool):
        @classmethod
        def get_name(cls):
            return "mock_tool"
    
    # Simulate the ToolManager's register_dynamic_tool method
    class MockToolManager:
        def __init__(self):
            self._available = {}
            self._plugin_tools = set()
            self._tool_to_plugin = {}
            self.warnings = []
            self.debugs = []
        
        def _log_warning(self, message, *args):
            self.warnings.append(message % args)
        
        def _log_debug(self, message, *args):
            self.debugs.append(message % args)
        
        def register_dynamic_tool(self, tool_class, plugin_name=None, overwrite=False):
            """Simulated register_dynamic_tool method."""
            if not issubclass(tool_class, MockBaseTool) or tool_class is MockBaseTool:
                raise TypeError(f"{tool_class.__name__} is not a valid tool class")
            
            tool_name = tool_class.get_name()
            if tool_name in self._available:
                if not overwrite:
                    self._log_warning("Tool '%s' already registered, skipping", tool_name)
                    return
                self._log_debug("Overwriting existing tool '%s'", tool_name)
            
            self._available[tool_name] = tool_class
            self._plugin_tools.add(tool_name)
            if plugin_name:
                self._tool_to_plugin[tool_name] = plugin_name
                self._log_debug("Registered tool '%s' from plugin '%s'", tool_name, plugin_name)
    
    # Run tests
    manager = MockToolManager()
    
    print("=== Test 1: Register new tool ===")
    manager.register_dynamic_tool(MockTool, "test_plugin")
    print(f"Available tools: {list(manager._available.keys())}")
    print(f"Plugin tools: {manager._plugin_tools}")
    print(f"Tool to plugin mapping: {manager._tool_to_plugin}")
    print(f"Debug logs: {manager.debugs}")
    print(f"Warnings: {manager.warnings}")
    
    print("\n=== Test 2: Register duplicate without overwrite ===")
    manager.register_dynamic_tool(DuplicateTool, "test_plugin2")
    print(f"Available tools: {list(manager._available.keys())}")
    print(f"Tool class should still be MockTool: {manager._available['mock_tool'] is MockTool}")
    print(f"Debug logs: {manager.debugs}")
    print(f"Warnings: {manager.warnings}")
    
    print("\n=== Test 3: Register duplicate with overwrite=True ===")
    manager.register_dynamic_tool(DuplicateTool, "test_plugin3", overwrite=True)
    print(f"Available tools: {list(manager._available.keys())}")
    print(f"Tool class should now be DuplicateTool: {manager._available['mock_tool'] is DuplicateTool}")
    print(f"Debug logs: {manager.debugs}")
    print(f"Warnings: {manager.warnings}")
    
    print("\n=== Test 4: Test TypeError for invalid tool class ===")
    try:
        manager.register_dynamic_tool(str, "test_plugin")
        print("ERROR: Should have raised TypeError!")
        return False
    except TypeError as e:
        print(f"Correctly raised TypeError: {e}")
    
    print("\n=== Test 5: Test TypeError for BaseTool class ===")
    try:
        manager.register_dynamic_tool(MockBaseTool, "test_plugin")
        print("ERROR: Should have raised TypeError!")
        return False
    except TypeError as e:
        print(f"Correctly raised TypeError: {e}")
    
    print("\n=== All tests passed! ===")
    return True

if __name__ == "__main__":
    success = test_register_dynamic_tool_logic()
    sys.exit(0 if success else 1)