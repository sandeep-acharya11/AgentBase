from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Sequence
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool as tool_decorator
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableSerializable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from BasePackage.AgentConfig import AgentConfig
from BasePackage.AgentMessage import AgentMessage
from BasePackage.AgentResponse import AgentResponse
from BasePackage.MCPClient import BaseMCPClient, DefaultMCPClient
from dotenv import load_dotenv

class BaseAgent(ABC):
    """Reusable LangChain-first scaffold for building chat agents."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.tools: list[BaseTool] = []
        self.memory: dict[str, Any] = {}
        self.chat_history: list[AgentMessage] = []
        self.tool_execution_log: list[dict[str, Any]] = []
        self._model: ChatOpenAI | RunnableSerializable[Any, Any] | None = None
        self.chain: RunnableSerializable[Any, Any] | None = None
        self.mcp_client: BaseMCPClient | None = None

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def description(self) -> str:
        return self.config.description

    @property
    def model(self) -> ChatOpenAI | RunnableSerializable[Any, Any] | None:
        return self._model

    @model.setter
    def model(self, model: ChatOpenAI | RunnableSerializable[Any, Any]) -> None:
        self._model = model

    def initialize(self) -> None:
        """Initialize MCP client, tools, model, and chain once before running turns."""
        load_dotenv()
        self._setup_mcp_client()
        self.tools = [self._wrap_tool(tool) for tool in (*self.build_tools(), *self._mcp_tools())]
        self.config = self.config.model_copy(
            update={"system_prompt": self.build_system_prompt()}
        )
        self._setup_memory()
        self._setup_model()
        self.chain = self.build_chain()

    def default_mcp_client(self) -> BaseMCPClient | None:
        """Build the default MCP client from config.mcp_servers. Override to customize."""
        if not self.config.mcp_servers:
            return None
        return DefaultMCPClient(self.config.mcp_servers)

    def _setup_mcp_client(self) -> None:
        if self.mcp_client is None:
            self.mcp_client = self.default_mcp_client()
        if self.mcp_client is not None:
            self.mcp_client.connect()

    def _mcp_tools(self) -> list[BaseTool]:
        return self.mcp_client.get_tools() if self.mcp_client is not None else []

    def shutdown(self) -> None:
        """Release the MCP client connection(s) held by this agent."""
        if self.mcp_client is not None:
            self.mcp_client.disconnect()

    def _wrap_tool(self, tool: BaseTool) -> BaseTool:
        """Wrap a tool with logging so each execution is tracked by this agent."""
        try:
            wrapped_tool = tool.model_copy(deep=True)
        except Exception:
            try:
                wrapped_tool = deepcopy(tool)
            except Exception:
                wrapped_tool = tool

        original_func = getattr(wrapped_tool, "func", None)
        if not callable(original_func):
            return wrapped_tool

        def logged_func(*args: Any, **kwargs: Any) -> Any:
            result = original_func(*args, **kwargs)
            self.log_tool_execution(
                getattr(wrapped_tool, "name", wrapped_tool.__class__.__name__),
                args[0] if args else kwargs,
                result,
            )
            return result

        wrapped_tool.func = logged_func
        return wrapped_tool

    def _setup_memory(self) -> None:
        self.memory.setdefault("history", [])
        self.memory.setdefault(
            "token_usage",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        )
        self.memory.setdefault("tool_execution_log", self.tool_execution_log)

    def _setup_model(self) -> None:
        if self._model is None:
            self.model = self.default_model()

    def default_model(self) -> ChatOpenAI | RunnableSerializable[Any, Any]:
        """Build a default chat model based on the configured provider."""
        if self.config.model_provider.lower() != "openai":
            raise ValueError(
                "Only 'openai' provider is supported in this scaffold. "
                "Override default_model() to add other providers."
            )

        model = ChatOpenAI(
            model=self.config.model_name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        if self.tools:
            return model.bind_tools(self.tools)
        return model

    def build_prompt(self) -> ChatPromptTemplate:
        """Default prompt template used for each turn."""
        return ChatPromptTemplate.from_messages(
            [
                ("system", "{system_prompt}"),
                MessagesPlaceholder(variable_name="history"),
                ("human", "{input}"),
            ]
        )

    def build_chain(self) -> RunnableSerializable[Any, Any]:
        """Create the runnable chain for user-input -> assistant response object."""
        if self.model is None:
            raise RuntimeError("Model must be initialized before building chain.")
        return self.build_prompt() | self.model

    def add_message(self, message: AgentMessage) -> None:
        self.chat_history.append(message)

    def reset_history(self) -> None:
        self.chat_history.clear()
        self.tool_execution_log.clear()
        self.memory["tool_execution_log"] = []
        self.memory["token_usage"] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

    def run(self, user_input: str) -> AgentResponse:
        """The base implementation of a single turn of the agent conversation."""
        if self.chain is None:
            self.initialize()

        user_message = self.parse_user_input(user_input)
        self.add_message(user_message)

        response = self.generate_response(list(self.chat_history))
        assistant_message = AgentMessage(
            role="assistant",
            content=response.content,
            metadata=response.metadata,
        )
        self.add_message(assistant_message)
        return response

    def parse_user_input(self, user_input: str) -> AgentMessage:
        """Convert raw user text to the internal typed message format."""
        return AgentMessage(role="user", content=user_input)

    def generate_response(self, messages: Sequence[AgentMessage]) -> AgentResponse:
        """Default generation path powered by the initialized LangChain chain."""
        if self.chain is None:
            raise RuntimeError("Agent is not initialized. Call initialize() first.")

        last_user_message = next(
            (message for message in reversed(messages) if message.role == "user"),
            None,
        )
        if last_user_message is None:
            raise ValueError("No user message found in conversation.")

        history = [
            self._to_langchain_message(message)
            for message in messages
            if message is not last_user_message
        ]
        model_response = self.chain.invoke(
            {
                "system_prompt": self.config.system_prompt,
                "history": history,
                "input": last_user_message.content,
            }
        )

        if hasattr(model_response, "tool_calls") and getattr(model_response, "tool_calls"):
            model_response = self._execute_tool_calls(model_response, history, last_user_message)

        content = str(getattr(model_response, "content", model_response))

        turn_usage = self._extract_token_usage(model_response)
        total_usage = self._update_total_token_usage(turn_usage)

        return AgentResponse(
            content=content,
            messages=list(messages),
            metadata={
                "agent_name": self.name,
                "model": self.config.model_name,
                "token_usage": turn_usage,
                "conversation_token_usage": total_usage,
                "total_tokens_consumed": total_usage["total_tokens"],
                "tool_execution_log": list(self.tool_execution_log),
            },
        )

    def log_tool_execution(self, tool_name: str, tool_input: Any, tool_output: Any) -> None:
        """Record which tools were executed by this agent and when."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": self.name,
            "tool": tool_name,
            "input": tool_input,
            "output": tool_output,
        }
        self.tool_execution_log.append(entry)
        self.memory["tool_execution_log"] = list(self.tool_execution_log)
        print(f"[tool-execution] agent={self.name} tool={tool_name} input={tool_input}")

    def _execute_tool_calls(
        self,
        model_response: Any,
        history: list[BaseMessage],
        last_user_message: AgentMessage,
        *,
        max_rounds: int = 5,
    ) -> Any:
        """Execute model-requested tool calls and continue until the model stops requesting tools."""
        current_response = model_response
        running_history: list[BaseMessage] = [
            *history,
            HumanMessage(content=last_user_message.content),
        ]

        for _ in range(max_rounds):
            tool_calls = getattr(current_response, "tool_calls", None) or []
            if not tool_calls:
                return current_response

            tool_messages: list[BaseMessage] = []
            for tool_call in tool_calls:
                tool_name = (
                    tool_call.get("name")
                    if isinstance(tool_call, dict)
                    else getattr(tool_call, "name", None)
                )
                tool_args = (
                    tool_call.get("args")
                    if isinstance(tool_call, dict)
                    else getattr(tool_call, "args", {})
                )
                if not tool_name:
                    continue

                tool = next(
                    (
                        candidate
                        for candidate in self.tools
                        if getattr(candidate, "name", None) == tool_name
                    ),
                    None,
                )
                if tool is None:
                    continue

                tool_result = tool.invoke(tool_args)
                tool_messages.append(
                    ToolMessage(
                        content=str(tool_result),
                        name=tool_name,
                        tool_call_id=str(
                            tool_call.get("id")
                            if isinstance(tool_call, dict)
                            else getattr(tool_call, "id", tool_name)
                        ),
                    )
                )

            if not tool_messages:
                return current_response

            running_history.extend([current_response, *tool_messages])
            follow_up_chain = self.build_prompt() | self.model
            current_response = follow_up_chain.invoke(
                {
                    "system_prompt": self.config.system_prompt,
                    "history": running_history,
                    "input": last_user_message.content,
                }
            )

        return current_response

    def _to_langchain_message(self, message: AgentMessage) -> BaseMessage:
        if message.role == "system":
            return SystemMessage(content=message.content)
        if message.role == "assistant":
            return AIMessage(content=message.content)
        if message.role == "tool":
            tool_name = str(message.metadata.get("name", "tool"))
            return ToolMessage(content=message.content, name=tool_name, tool_call_id=tool_name)
        return HumanMessage(content=message.content)

    def _extract_token_usage(self, model_response: Any) -> dict[str, int]:
        usage = getattr(model_response, "usage_metadata", None)
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            total_tokens = int(usage.get("total_tokens", 0) or 0)
            if total_tokens == 0:
                total_tokens = input_tokens + output_tokens
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }

        response_metadata = getattr(model_response, "response_metadata", None)
        token_usage = (
            response_metadata.get("token_usage", {})
            if isinstance(response_metadata, dict)
            else {}
        )
        prompt_tokens = int(token_usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(token_usage.get("completion_tokens", 0) or 0)
        total_tokens = int(token_usage.get("total_tokens", 0) or 0)
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        return {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _update_total_token_usage(self, turn_usage: dict[str, int]) -> dict[str, int]:
        totals = self.memory.get("token_usage")
        if not isinstance(totals, dict):
            totals = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }

        totals["input_tokens"] = int(totals.get("input_tokens", 0)) + int(
            turn_usage.get("input_tokens", 0)
        )
        totals["output_tokens"] = int(totals.get("output_tokens", 0)) + int(
            turn_usage.get("output_tokens", 0)
        )
        totals["total_tokens"] = int(totals.get("total_tokens", 0)) + int(
            turn_usage.get("total_tokens", 0)
        )

        self.memory["token_usage"] = totals
        return dict(totals)

    @abstractmethod
    def build_tools(self) -> list[BaseTool]:
        """Return the tool functions available to this agent."""
    
    @abstractmethod
    def build_system_prompt(self) -> str:
        """Return the system prompt tailored to this agent."""