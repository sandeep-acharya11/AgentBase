from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    """Connection details for a single MCP server an agent can talk to."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    transport: Literal["stdio", "sse", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Typed configuration shared by all concrete agents."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = ""
    model_provider: str = "openai"
    model_name: str = "gpt-4o-mini"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=512, gt=0)
    system_prompt: str = "You are a helpful AI agent."
    tags: list[str] = Field(default_factory=list)
    verbose: bool = False
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
