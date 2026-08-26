from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from BasePackage.AgentMessage import AgentMessage


class OrchestratorAPIRequest(BaseModel):
    """Validated request body for invoking an orchestrator over HTTP."""

    model_config = ConfigDict(extra="forbid")
    # Some sample comment added to test the code generation capabilities of the model.
    # Adding some more comments to see how the model handles them. This is a test comment.
    input: str = Field(..., min_length=1)
    routing_mode: Literal["keyword", "llm"] | None = None
    enable_react_evaluation: bool | None = None
    reset_history: bool = True


class OrchestratorAPIResponse(BaseModel):
    """Frontend-friendly wrapper around an agent execution result."""

    content: str
    messages: list[AgentMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    agent_name: str
    success: bool = True
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrchestratorHealthResponse(BaseModel):
    """Lightweight health payload for default orchestrator API routes."""

    agent_name: str
    initialized: bool
    child_agents: list[str] = Field(default_factory=list)
    routing_mode: Literal["keyword", "llm"]
    react_evaluation_enabled: bool