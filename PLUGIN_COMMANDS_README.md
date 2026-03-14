# Adding Custom Commands to Vibe via Plugins

Vibe allows plugins to register custom slash commands (`/command`) that users can invoke in the chat interface.

## Architecture Overview

```
User types "/greet" → CommandRegistry.find_command() → Plugin's handler method → Response displayed
```

## How It Works

1. **CommandPlugin base class** - Provides `register_commands()` method
2. **CommandRegistry** - Central registry that stores all commands and their handlers
3. **PluginManager** - Calls `register_commands()` on each plugin during setup
4. **Handler methods** - Plugin defines async methods that return strings

## Creating a Plugin with Commands

### Step 1: Inherit from CommandPlugin

```python
from vibe.core.plugins.base import VibePlugin, PluginContext
from vibe.core.plugins.command_plugin import CommandPlugin
from vibe.cli.commands import Command

class MyCommandPlugin(VibePlugin, CommandPlugin):
    # Your plugin implementation
```

### Step 2: Implement register_commands()

```python
async def register_commands(self, registry: CommandRegistry) -> None:
    """Register custom commands into the global command registry."""
    registry.register_command(
        name="mycommand",
        command=Command(
            aliases=frozenset(["/mycommand", "/mc"]),  # Multiple aliases
            description="My custom command",
            handler="_my_handler_method",  # Method name without 'self.'
        ),
    )
```

### Step 3: Implement Handler Methods

```python
async def _my_handler_method(self) -> str:
    """Handler for /mycommand."""
    return "Response from my custom command!"
```

## Complete Example

See `example_plugin.py` in the repository for a full working example that adds:
- `/greet` - Shows a friendly greeting
- `/time` - Displays current time

## Key Points

1. **Handler methods** must be async and return strings
2. **Method names** are referenced by their name (without `self.`)
3. **Aliases** use frozenset for uniqueness
4. **Commands appear automatically** in the autocompletion menu
5. **No special registration needed** - PluginManager handles it all

## Testing Your Commands

1. Place your plugin in one of the discovery paths:
   - `{VIBE_HOME}/plugins/`
   - `{workdir}/.vibe/plugins/`
   - Or add to `config.plugin_paths`

2. Restart Vibe
3. Type `/` and see your commands in autocompletion
4. Test by invoking them with `/yourcommand`
