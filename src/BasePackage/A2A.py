"""A2A protocol support for agents built with :class:`BaseAgent`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, Field

from a2a.client import A2AClientError as SDKClientError
from a2a.client import ClientConfig, create_client
from a2a.helpers.proto_helpers import get_artifact_text, get_message_text, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.types import Role, SendMessageRequest
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol

if TYPE_CHECKING:
    from fastapi import FastAPI

    from BasePackage.BaseAgent import BaseAgent


class A2AAgentSkill(BaseModel):
    """One user-visible capability published in an A2A Agent Card."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class A2AAgentConfig(BaseModel):
    """Protocol metadata used to expose a ``BaseAgent`` as an A2A service."""

    model_config = ConfigDict(extra="forbid")

    public_url: str = Field(..., min_length=1)
    rpc_path: str = "/a2a"
    version: str = "1.0.0"
    documentation_url: str | None = None
    input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    skills: list[A2AAgentSkill] = Field(default_factory=list)

    def rpc_url(self) -> str:
        return f"{self.public_url.rstrip('/')}/{self.rpc_path.lstrip('/')}"


class A2AClientError(RuntimeError):
    """Raised when an A2A agent cannot provide a completed text response."""


class A2AClient:
    """Small client facade for one text request to an A2A JSON-RPC agent."""

    def __init__(self, service_url: str, *, timeout_seconds: float = 30.0) -> None:
        self._service_url = service_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def invoke(self, user_input: str) -> str:
        text = user_input.strip()
        if not text:
            raise ValueError("A2A input must not be empty.")

        httpx_client = httpx.AsyncClient(timeout=self._timeout_seconds)
        client = None
        try:
            client = await create_client(
                self._service_url,
                client_config=ClientConfig(
                    streaming=False,
                    httpx_client=httpx_client,
                    supported_protocol_bindings=[TransportProtocol.JSONRPC],
                ),
            )
            request = SendMessageRequest(
                message=new_text_message(text, role=Role.ROLE_USER)
            )
            async for response in client.send_message(request):
                if response.HasField("message"):
                    output = get_message_text(response.message).strip()
                    if output:
                        return output
                if response.HasField("task"):
                    task = response.task
                    if task.status.state == TaskState.TASK_STATE_FAILED:
                        raise A2AClientError("Remote A2A agent reported a failed task.")
                    output = "\n".join(
                        get_artifact_text(artifact).strip()
                        for artifact in task.artifacts
                    ).strip()
                    if output:
                        return output
                    raise A2AClientError("Remote A2A agent returned no text artifact.")
            raise A2AClientError("Remote A2A agent returned no response.")
        except (SDKClientError, httpx.HTTPError, ValueError) as exc:
            raise A2AClientError(f"A2A request failed: {exc}") from exc
        finally:
            if client is not None:
                await client.close()
            else:
                await httpx_client.aclose()

    def invoke_sync(self, user_input: str) -> str:
        """Invoke from synchronous callers such as LangChain tool functions."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.invoke(user_input))
        raise RuntimeError("invoke_sync cannot run inside an active event loop; use await invoke(...).")


class BaseAgentA2AExecutor(AgentExecutor):
    """Adapts the synchronous ``BaseAgent.run`` contract to A2A task events."""

    def __init__(self, agent: BaseAgent) -> None:
        self._agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input().strip()
        if not user_input:
            raise ValueError("A2A messages must include non-empty text content.")
        if not context.task_id or not context.context_id:
            raise RuntimeError("A2A task and context identifiers are required.")

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
        await updater.start_work()

        try:
            response = await asyncio.to_thread(self._agent.run, user_input)
        except Exception:
            await updater.failed(
                updater.new_agent_message([Part(text="Agent execution failed.")])
            )
            return

        result_parts = [Part(text=response.content)]
        await updater.add_artifact(
            result_parts,
            name="agent-response",
            last_chunk=True,
        )
        await updater.complete(updater.new_agent_message(result_parts))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.task_id or not context.context_id:
            return
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def build_agent_card(agent: BaseAgent, config: A2AAgentConfig) -> AgentCard:
    """Build the standard A2A Agent Card for a concrete BaseAgent instance."""
    return AgentCard(
        name=agent.name,
        description=agent.description,
        version=config.version,
        documentation_url=config.documentation_url or "",
        supported_interfaces=[
            AgentInterface(
                url=config.rpc_url(),
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        ],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=config.input_modes,
        default_output_modes=config.output_modes,
        skills=[
            AgentSkill(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                tags=skill.tags,
                examples=skill.examples,
                input_modes=config.input_modes,
                output_modes=config.output_modes,
            )
            for skill in config.skills
        ],
    )


def mount_a2a_routes(
    app: FastAPI,
    agent: BaseAgent,
    config: A2AAgentConfig,
) -> DefaultRequestHandler:
    """Mount Agent Card and JSON-RPC A2A routes on an existing FastAPI app."""
    agent_card = build_agent_card(agent, config)
    request_handler = DefaultRequestHandler(
        agent_executor=BaseAgentA2AExecutor(agent),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, config.rpc_path),
    )
    return request_handler