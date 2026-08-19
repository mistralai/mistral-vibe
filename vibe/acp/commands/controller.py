from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from acp import Client, PromptResponse
from acp.helpers import update_available_commands
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    AvailableCommand,
    AvailableCommandInput,
    TextContentBlock,
    UnstructuredCommandInput,
    UserMessageChunk,
)

from vibe.acp.commands.registry import AcpCommand, AcpCommandContext, AcpCommandKind
from vibe.acp.commands.teleport import (
    TELEPORT_PUSH_OPTION_ID,
    teleport_event_update,
    teleport_failed_update,
    teleport_field_meta,
    teleport_push_options,
    teleport_push_request,
    teleport_start_update,
)
from vibe.acp.exceptions import InternalError, from_app_server_error
from vibe.acp.session import AcpSession
from vibe.acp.utils import (
    compact_end_update,
    compact_error_update,
    compact_start_update,
    get_proxy_help_text,
)
from vibe.app_server.models import (
    TeleportComplete,
    TeleportFailed,
    TeleportPushRequired,
)
from vibe.app_server.protocol import AppServerResponseError
from vibe.utils.data_retention import DATA_RETENTION_MESSAGE
from vibe.utils.retry_prompt import build_retry_prompt

_TELEPORT_NO_HISTORY_MESSAGE = "No conversation history to teleport."
_RETRY_NO_HISTORY_MESSAGE = "No interrupted response to continue."

type SendConfigOptions = Callable[[AcpSession], Awaitable[None]]


@dataclass(frozen=True)
class InjectedPrompt:
    """Command outcome that runs a turn on a prompt the user never typed."""

    text: str


class AcpCommandController:
    def __init__(
        self, client: Callable[[], Client], send_config_options: SendConfigOptions
    ) -> None:
        self._client = client
        self._send_config_options = send_config_options

    async def send_commands(self, session: AcpSession) -> None:
        commands = [
            self._available_command(command)
            for command in session.commands.commands.values()
        ]
        builtin = set(session.commands.commands)
        commands.extend(
            AvailableCommand(
                name=skill.name,
                description=skill.description,
                input=AvailableCommandInput(
                    root=UnstructuredCommandInput(hint="instructions for the skill")
                ),
            )
            for skill in session.app_server.resources.runtime.skills
            if skill.user_invocable and skill.name not in builtin
        )
        commands.sort(key=lambda command: command.name)
        await self._client().session_update(
            session_id=session.id, update=update_available_commands(commands)
        )

    async def execute(
        self, session: AcpSession, text: str, message_id: str
    ) -> PromptResponse | InjectedPrompt | None:
        parts = text.strip().split(None, 1)
        if not parts or not parts[0].startswith("/"):
            return None
        name = parts[0][1:].lower()
        command = session.commands.get(name)
        if command is None:
            return None
        arguments = parts[1] if len(parts) > 1 else ""
        await self._user_message(session, text, message_id)
        session.app_server.resources.telemetry.record(
            "vibe.slash_command_used", {"command": name, "command_type": "builtin"}
        )
        match command.kind:
            case AcpCommandKind.HELP:
                response = await self._help(session)
            case AcpCommandKind.COMPACT:
                response = await self._compact(session, arguments)
            case AcpCommandKind.RELOAD:
                response = await self._reload(session)
            case AcpCommandKind.LOG:
                response = await self._log(session)
            case AcpCommandKind.MCP:
                response = await self._mcp(session, arguments)
            case AcpCommandKind.TELEPORT:
                response = await self._teleport(session)
            case AcpCommandKind.PROXY_SETUP:
                response = await self._proxy_setup(session, arguments)
            case AcpCommandKind.RETRY:
                response = await self._retry(session, arguments)
            case AcpCommandKind.LEANSTALL:
                response = await self._set_lean(session, installed=True)
            case AcpCommandKind.UNLEANSTALL:
                response = await self._set_lean(session, installed=False)
            case AcpCommandKind.DATA_RETENTION:
                response = await self._reply(session, DATA_RETENTION_MESSAGE)
        return response

    async def message(
        self,
        session: AcpSession,
        text: str,
        *,
        field_meta: dict[str, object] | None = None,
    ) -> None:
        await self._client().session_update(
            session_id=session.id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
                message_id=str(uuid4()),
                field_meta=field_meta,
            ),
        )

    @staticmethod
    def _available_command(command: AcpCommand) -> AvailableCommand:
        input_spec = (
            AvailableCommandInput(
                root=UnstructuredCommandInput(hint=command.input_hint)
            )
            if command.input_hint
            else None
        )
        return AvailableCommand(
            name=command.name, description=command.description, input=input_spec
        )

    async def _help(self, session: AcpSession) -> PromptResponse:
        lines = ["### Available Commands", ""]
        for command in sorted(
            session.commands.commands.values(), key=lambda item: item.name
        ):
            hint = f" `<{command.input_hint}>`" if command.input_hint else ""
            lines.append(f"- `/{command.name}`{hint}: {command.description}")
        skills = [
            skill
            for skill in session.app_server.resources.runtime.skills
            if skill.user_invocable and skill.name not in session.commands.commands
        ]
        if skills:
            lines.extend(["", "### Available Skills", ""])
            lines.extend(
                f"- `/{skill.name}`: {skill.description}"
                for skill in sorted(skills, key=lambda item: item.name)
            )
        return await self._reply(session, "\n".join(lines))

    async def _compact(self, session: AcpSession, instructions: str) -> PromptResponse:
        if not session.app_server.history:
            return await self._reply(session, "No conversation history to compact yet.")
        call_id = str(uuid4())
        await self._client().session_update(
            session_id=session.id, update=compact_start_update(call_id)
        )
        try:
            await session.app_server.compact(instructions)
        except AppServerResponseError as exc:
            error = from_app_server_error(exc.error)
            await self._client().session_update(
                session_id=session.id,
                update=compact_error_update(call_id, exc.error.message),
            )
            raise error from exc
        await self._client().session_update(
            session_id=session.id,
            update=compact_end_update(call_id, "Conversation context compacted"),
        )
        return PromptResponse(stop_reason="end_turn")

    async def _reload(self, session: AcpSession) -> PromptResponse:
        try:
            await session.app_server.resources.config.reload(reload_runtime=True)
        except Exception as exc:
            return await self._reply(session, f"Failed to reload config: {exc}")
        session.commands.refresh(
            AcpCommandContext(
                vibe_code_enabled=session.app_server.resources.config.current.vibe_code_enabled
            )
        )
        try:
            await self.send_commands(session)
        except Exception as exc:
            return await self._reply(
                session,
                "Configuration reloaded, but failed to advertise updated commands: "
                f"{exc}",
            )
        return await self._reply(
            session, "Configuration reloaded (includes agent instructions and skills)."
        )

    async def _log(self, session: AcpSession) -> PromptResponse:
        log = session.app_server.resources.runtime.session_log
        message = (
            f"## Current Log Directory\n\n`{log.path}`\n\n"
            "You can send this directory to share your interaction."
            if log.enabled and log.path is not None
            else "Session logging is disabled in configuration."
        )
        return await self._reply(session, message)

    async def _mcp(self, session: AcpSession, arguments: str) -> PromptResponse:
        parts = arguments.split(None, 1)
        subcommand = parts[0].lower() if parts else "status"
        alias = parts[1] if len(parts) > 1 else ""
        match subcommand:
            case "status" if alias:
                response = await self._reply(session, "Usage: `/mcp status`")
            case "status":
                response = await self._mcp_status(session)
            case "logout" if alias:
                response = await self._mcp_logout(session, alias)
            case "logout":
                response = await self._reply(session, "Usage: `/mcp logout <alias>`")
            case "login" if alias:
                response = await self._mcp_login(session, alias)
            case "login":
                response = await self._reply(session, "Usage: `/mcp login <alias>`")
            case _:
                response = await self._reply(
                    session,
                    "Usage: `/mcp status`, `/mcp login <alias>`, "
                    "or `/mcp logout <alias>`",
                )
        return response

    async def _mcp_status(self, session: AcpSession) -> PromptResponse:
        try:
            state = await session.app_server.resources.mcp.read()
        except AppServerResponseError as exc:
            return await self._reply(session, exc.error.message)
        if not state.sources:
            return await self._reply(session, "No MCP servers configured.")
        lines = ["### MCP auth status", ""]
        lines.extend(
            f"- `{source.name}`: `{source.status.value}`"
            for source in sorted(state.sources, key=lambda item: item.name)
        )
        return await self._reply(session, "\n".join(lines))

    async def _mcp_login(self, session: AcpSession, alias: str) -> PromptResponse:
        try:
            state = await session.app_server.resources.mcp.read()
            if not any(source.name == alias for source in state.sources):
                return await self._reply(session, f"Unknown MCP server: `{alias}`")
            async for event in session.app_server.resources.mcp.login(alias):
                await self.message(
                    session, f"Authenticate MCP server `{alias}`: {event.url}"
                )
        except AppServerResponseError as exc:
            return await self._reply(session, exc.error.message)
        return await self._reply(session, f"MCP server `{alias}` authenticated.")

    async def _mcp_logout(self, session: AcpSession, alias: str) -> PromptResponse:
        try:
            await session.app_server.resources.mcp.logout(alias)
        except AppServerResponseError as exc:
            return await self._reply(session, exc.error.message)
        return await self._reply(session, f"MCP server `{alias}` logged out.")

    async def _teleport(self, session: AcpSession) -> PromptResponse:
        try:
            _, project_id = await session.app_server.resources.vibe_code.open_projects(
                for_teleport=True
            )
        except AppServerResponseError as exc:
            status = (
                "no_history"
                if exc.error.message == _TELEPORT_NO_HISTORY_MESSAGE
                else "unavailable"
            )
            return await self._reply(
                session, exc.error.message, field_meta=teleport_field_meta(status)
            )
        if project_id is None:
            return await self._reply(
                session,
                "No Vibe Code project is linked to this repository.",
                field_meta=teleport_field_meta("unavailable"),
            )
        call_id = str(uuid4())
        await self._client().session_update(
            session_id=session.id, update=teleport_start_update(call_id)
        )
        try:
            async for event in session.app_server.resources.vibe_code.teleport(
                None, project_id=project_id
            ):
                await self._client().session_update(
                    session_id=session.id, update=teleport_event_update(call_id, event)
                )
                match event:
                    case TeleportPushRequired():
                        permission = await self._client().request_permission(
                            session_id=session.id,
                            tool_call=teleport_push_request(call_id, event),
                            options=teleport_push_options(),
                        )
                        approved = (
                            isinstance(permission.outcome, AllowedOutcome)
                            and permission.outcome.option_id == TELEPORT_PUSH_OPTION_ID
                        )
                        await session.app_server.resources.vibe_code.respond_to_push(
                            event.operation_id, approved=approved
                        )
                    case TeleportFailed():
                        return PromptResponse(
                            stop_reason="end_turn",
                            field_meta=teleport_field_meta("failed"),
                        )
                    case TeleportComplete(url=url):
                        return PromptResponse(
                            stop_reason="end_turn",
                            field_meta=teleport_field_meta("completed", url=url),
                        )
        except AppServerResponseError as exc:
            await self._client().session_update(
                session_id=session.id,
                update=teleport_failed_update(call_id, exc.error.message),
            )
            return PromptResponse(
                stop_reason="end_turn", field_meta=teleport_field_meta("failed")
            )
        raise InternalError("Teleport ended without a result")

    async def _proxy_setup(self, session: AcpSession, arguments: str) -> PromptResponse:
        settings = await session.app_server.resources.config.read_proxy()
        if not arguments:
            return await self._reply(session, get_proxy_help_text(settings))
        parts = arguments.split(None, 1)
        key = parts[0].upper()
        value = parts[1] if len(parts) > 1 else ""
        if key not in settings.values:
            return await self._reply(
                session, f"Error: Unsupported proxy variable: {key}"
            )
        resolved_value = value.strip() or None
        try:
            await session.app_server.resources.config.update_proxy({
                key: resolved_value
            })
        except AppServerResponseError as exc:
            return await self._reply(session, f"Error: {exc.error.message}")
        if resolved_value is None:
            message = (
                f"Removed `{key}` from ~/.vibe/.env\n\n"
                "Please start a new chat for changes to take effect."
            )
        else:
            message = (
                f"Set `{key}={resolved_value}` in ~/.vibe/.env\n\n"
                "Please start a new chat for changes to take effect."
            )
        return await self._reply(session, message)

    async def _retry(
        self, session: AcpSession, instructions: str
    ) -> PromptResponse | InjectedPrompt:
        if not session.app_server.history:
            return await self._reply(session, _RETRY_NO_HISTORY_MESSAGE)
        return InjectedPrompt(build_retry_prompt(instructions))

    async def _set_lean(
        self, session: AcpSession, *, installed: bool
    ) -> PromptResponse:
        await session.app_server.resources.agents.set_installed(
            "lean", installed=installed
        )
        await self._send_config_options(session)
        action = "installed" if installed else "uninstalled"
        return await self._reply(session, f"Lean agent {action}.")

    async def _reply(
        self,
        session: AcpSession,
        text: str,
        *,
        field_meta: dict[str, object] | None = None,
    ) -> PromptResponse:
        await self.message(session, text, field_meta=field_meta)
        return PromptResponse(stop_reason="end_turn", field_meta=field_meta)

    async def _user_message(
        self, session: AcpSession, text: str, message_id: str
    ) -> None:
        await self._client().session_update(
            session_id=session.id,
            update=UserMessageChunk(
                session_update="user_message_chunk",
                content=TextContentBlock(type="text", text=text),
                message_id=message_id,
            ),
        )
