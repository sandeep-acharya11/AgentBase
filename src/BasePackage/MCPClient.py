"""
MCPClient - Default MCP (Model Context Protocol) client for agents.

Provides BaseMCPClient, the contract every MCP client implementation must
satisfy, and DefaultMCPClient, a ready-to-use implementation backed by the
official `mcp` SDK. Agents pick this up automatically via
BaseAgent.default_mcp_client() and can override it for custom transports,
auth, or connection pooling without touching BaseAgent/MasterAgent code.
"""

from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, create_model

from BasePackage.AgentConfig import MCPServerConfig

# Minimal JSON-schema -> Python type mapping used to build tool arg schemas.
_JSON_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class BaseMCPClient(ABC):
    """Contract every MCP client implementation must satisfy."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connections to all configured MCP servers."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down all MCP server connections."""

    @abstractmethod
    def get_tools(self) -> list[BaseTool]:
        """Return MCP server tools adapted to LangChain BaseTool instances."""

    @abstractmethod
    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool on a specific MCP server and return its result."""


class DefaultMCPClient(BaseMCPClient):
    """Default MCP client backed by the official `mcp` SDK.

    Runs a single background event loop thread for the lifetime of the
    client so synchronous agent code can call async MCP sessions without
    re-spawning a server process/connection on every tool call.
    """

    def __init__(self, server_configs: list[MCPServerConfig]) -> None:
        self._server_configs = list(server_configs)
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stack: AsyncExitStack | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if self._loop is not None or not self._server_configs:
            return

        ready = threading.Event()
        loop = asyncio.new_event_loop()
        self._loop = loop

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._async_connect_all())
            ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=_run_loop, name="mcp-client-loop", daemon=True)
        self._thread.start()
        ready.wait()

    def disconnect(self) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._async_disconnect_all(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None

    async def _async_connect_all(self) -> None:
        self._exit_stack = AsyncExitStack()
        for server_config in self._server_configs:
            self._sessions[server_config.name] = await self._connect_one(server_config)

    async def _connect_one(self, server_config: MCPServerConfig) -> ClientSession:
        assert self._exit_stack is not None
        transport = server_config.transport

        if transport == "stdio":
            if not server_config.command:
                raise ValueError(f"MCP server '{server_config.name}' requires 'command' for stdio transport.")
            params = StdioServerParameters(
                command=server_config.command,
                args=server_config.args,
                env=server_config.env or None,
            )
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))
        elif transport == "sse":
            if not server_config.url:
                raise ValueError(f"MCP server '{server_config.name}' requires 'url' for sse transport.")
            read_stream, write_stream = await self._exit_stack.enter_async_context(sse_client(server_config.url))
        elif transport == "streamable_http":
            if not server_config.url:
                raise ValueError(f"MCP server '{server_config.name}' requires 'url' for streamable_http transport.")
            read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                streamablehttp_client(server_config.url)
            )
        else:
            raise ValueError(f"Unsupported MCP transport '{transport}' for server '{server_config.name}'.")

        session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        return session

    async def _async_disconnect_all(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._sessions.clear()

    # ------------------------------------------------------------------
    # Tool access
    # ------------------------------------------------------------------

    def get_tools(self) -> list[BaseTool]:
        if self._loop is None:
            return []
        tool_specs = asyncio.run_coroutine_threadsafe(self._async_list_all_tools(), self._loop).result()
        return [self._build_langchain_tool(server_name, tool) for server_name, tool in tool_specs]

    async def _async_list_all_tools(self) -> list[tuple[str, Any]]:
        results: list[tuple[str, Any]] = []
        for server_name, session in self._sessions.items():
            listed = await session.list_tools()
            results.extend((server_name, tool) for tool in listed.tools)
        return results

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        if self._loop is None:
            raise RuntimeError("MCP client is not connected. Call connect() first.")
        return asyncio.run_coroutine_threadsafe(
            self._async_call_tool(server_name, tool_name, arguments), self._loop
        ).result()

    async def _async_call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        session = self._sessions.get(server_name)
        if session is None:
            raise ValueError(f"Unknown MCP server '{server_name}'.")
        result = await session.call_tool(tool_name, arguments)
        return "\n".join(getattr(block, "text", str(block)) for block in result.content)

    def _build_langchain_tool(self, server_name: str, mcp_tool: Any) -> BaseTool:
        def _invoke(**kwargs: Any) -> Any:
            return self.call_tool(server_name, mcp_tool.name, kwargs)

        return StructuredTool.from_function(
            func=_invoke,
            name=mcp_tool.name,
            description=mcp_tool.description or "",
            args_schema=_json_schema_to_model(mcp_tool.name, getattr(mcp_tool, "inputSchema", None)),
        )


def _json_schema_to_model(tool_name: str, schema: dict[str, Any] | None) -> type[BaseModel]:
    """Build a minimal pydantic model from an MCP tool's JSON input schema."""
    schema = schema or {}
    properties: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _JSON_SCHEMA_TYPE_MAP.get(prop_schema.get("type"), Any)
        default = ... if prop_name in required else None
        fields[prop_name] = (py_type, default)

    return create_model(f"{tool_name}Args", **fields)
