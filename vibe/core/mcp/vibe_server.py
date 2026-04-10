"""
MCP Server for Vibe - Enables remote subagent execution

This module provides an MCP server implementation that allows Vibe instances
to execute subagents remotely via the MCP protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator, Any

from mcp.server.lowlevel.server import Server, NotificationOptions
from mcp.types import (
    CallToolRequest,
    GetPromptRequest,
    GetPromptResult,
    ListPromptsRequest,
    ListPromptsResult,
    ListToolsRequest,
    ListToolsResult,
    ServerCapabilities,
    Tool,
    TextContent,
    CallToolResult,
)
from vibe.core.agents.models import AgentType

from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.manager import AgentManager
from vibe.core.config import VibeConfig
from vibe.core.types import AssistantEvent, BaseEvent, ToolResultEvent

if TYPE_CHECKING:
    from mcp.types import InitializationOptions


class VibeMCPServer(Server):
    """
    MCP Server implementation for Vibe that enables remote subagent execution.

    This server exposes Vibe's subagent capabilities via the MCP protocol,
    allowing remote Vibe instances to delegate tasks to this server.
    """

    def __init__(self, config: VibeConfig, agent_mode: str = "subagents"):
        """
        Initialize Vibe MCP Server.

        Args:
            config: Vibe configuration to use for subagent execution
            agent_mode: Mode for agent exposure. Can be 'agents', 'subagents',
                       or comma-separated list of specific agent names.
        """
        super().__init__(
            name="vibe-subagent",
            version="1.0.0",
            instructions="Vibe subagent execution server",
        )
        self.config = config
        self.agent_manager = AgentManager(lambda: config)
        self.agent_mode = agent_mode

        # Register MCP handlers
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register MCP request handlers."""
        # Standard MCP handlers
        self.list_prompts()(self._handle_list_prompts)
        self.get_prompt()(self._handle_get_prompt)
        self.list_tools()(self._handle_list_tools)
        self.call_tool()(self._handle_call_tool)

    async def _handle_list_prompts(
        self, request: ListPromptsRequest
    ) -> ListPromptsResult:
        """Handle MCP list_prompts request."""
        # Return available system prompts
        from vibe.core.prompts import SystemPrompt

        prompts = []
        for prompt in SystemPrompt:
            prompts.append({
                "name": prompt.value,
                "description": f"Vibe {prompt.value} system prompt",
            })

        return ListPromptsResult(prompts=prompts)

    def _get_agents_for_mode(self) -> list[AgentProfile]:
        """Get agents to expose based on the specified mode."""
        all_agents = list(self.agent_manager.available_agents.values())
        
        if self.agent_mode == "agents":
            # Expose all agents
            return all_agents
        elif self.agent_mode == "subagents":
            # Expose only subagents (original behavior)
            return [a for a in all_agents if a.agent_type == AgentType.SUBAGENT]
        else:
            # Expose specific agents by name
            agent_names = [name.strip() for name in self.agent_mode.split(",")]
            return [a for a in all_agents if a.name in agent_names]

    async def _handle_get_prompt(
        self, name: str, arguments: dict[str, str] | None
    ) -> GetPromptResult:
        """Handle MCP get_prompt request."""
        from vibe.core.prompts import get_system_prompt

        try:
            prompt_content = get_system_prompt(name)
            return GetPromptResult(
                prompt=prompt_content,
                arguments=arguments or {},
            )
        except Exception as e:
            return GetPromptResult(
                prompt=f"Error loading prompt: {e}",
                arguments=arguments or {},
            )

    async def _handle_list_tools(
        self, request: ListToolsRequest
    ) -> ListToolsResult:
        """Handle MCP list_tools request."""
        # Get agents based on the specified mode
        agents_to_expose = self._get_agents_for_mode()

        tools = []
        for agent in agents_to_expose:
            # Use agent description if available, otherwise generate one
            description = agent.description if agent.description else f"Execute {agent.name} agent"
            
            tools.append(
                Tool(
                    name=f"{agent.name}",  # Use agent name directly
                    description=description,
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "Task to delegate to the agent",
                            }
                        },
                        "required": ["task"],
                    },
                )
            )

        return ListToolsResult(tools=tools)

    async def _handle_call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        """Handle MCP call_tool request for agent execution."""
        # Get agents available for this mode
        available_agents = self._get_agents_for_mode()
        agent_names = [agent.name for agent in available_agents]
        
        if tool_name not in agent_names:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error: Unknown tool {tool_name}. Available tools: {', '.join(agent_names)}")]
            )

        task = arguments.get("task", "") if arguments else ""

        if not task:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Task parameter is required")]
            )

        try:
            # Execute the agent and collect results
            content_blocks = []
            async for event in self._execute_agent(tool_name, task):
                if isinstance(event, str):
                    content_blocks.append(TextContent(type="text", text=event))
                else:
                    content_blocks.append(TextContent(type="text", text=str(event)))
            
            return CallToolResult(content=content_blocks)
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Agent execution failed: {e}")]
            )

    async def _execute_agent(
        self, agent_name: str, task: str
    ) -> AsyncGenerator[BaseEvent | str, None]:
        """
        Execute an agent task and yield events.

        Args:
            agent_name: Name of the agent to execute
            task: Task description to delegate to the agent

        Yields:
            Events from agent execution (AssistantEvent, ToolResultEvent, etc.)
            or string messages for MCP compatibility
        """
        try:
            # Create isolated subagent loop
            subagent_loop = self._create_subagent_loop(agent_name)

            # Execute the task and stream events
            async for event in subagent_loop.act(task):
                yield event

        except Exception as e:
            yield f"Error executing subagent {agent_name}: {e}"

    def _create_subagent_loop(self, agent_name: str) -> AgentLoop:
        """
        Create an isolated AgentLoop for subagent execution.

        Args:
            agent_name: Name of the subagent to create loop for

        Returns:
            Configured AgentLoop instance
        """
        # Get agent profile and apply overrides
        agent_profile = self.agent_manager.get_agent(agent_name)

        # Create base config with minimal logging for remote execution
        from vibe.core.config import SessionLoggingConfig
        session_logging = SessionLoggingConfig(
            save_dir="",  # No local logging for remote execution
            session_prefix=f"remote-{agent_name}",
            enabled=False,
        )

        base_config = VibeConfig.load(session_logging=session_logging)
        final_config = agent_profile.apply_to_config(base_config)

        return AgentLoop(
            config=final_config,
            agent_name=agent_name,
            entrypoint_metadata={},
        )

    def get_capabilities(
        self,
        notification_options: NotificationOptions | None = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
    ) -> ServerCapabilities:
        """Get server capabilities."""
        capabilities = super().get_capabilities(
            notification_options or NotificationOptions(),
            experimental_capabilities or {},
        )

        # Add Vibe-specific capabilities
        capabilities.experimental["vibe"] = {
            "subagent_execution": True,
            "available_subagents": [agent.name for agent in self.agent_manager.get_subagents()],
        }

        return capabilities


def create_vibe_mcp_server(config: VibeConfig, agent_mode: str = "subagents") -> VibeMCPServer:
    """
    Factory function to create a Vibe MCP Server.

    Args:
        config: Vibe configuration
        agent_mode: Mode for agent exposure. Can be 'agents', 'subagents',
                   or comma-separated list of specific agent names.

    Returns:
        Configured VibeMCPServer instance
    """
    return VibeMCPServer(config, agent_mode=agent_mode)
