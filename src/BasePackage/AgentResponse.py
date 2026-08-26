from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from BasePackage.AgentMessage import AgentMessage


class AgentResponse(BaseModel):
    """Standard response shape returned by any agent implementation."""

    content: str
    messages: list[AgentMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
