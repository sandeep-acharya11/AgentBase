from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """A strongly typed message exchanged in the agent conversation."""

    role: str = Field(..., pattern=r"^(system|user|assistant|tool)$")
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
