# BaseAgent Class Documentation

This document describes the current implementation in `BasePackage/BaseAgent.py`.
`BaseAgent` is an abstract, LangChain-first scaffold for chat-style agents. It owns
the common setup, conversation history, tool execution, and response metadata while
subclasses provide their tools and system prompt.

The base class provides:

- Typed config, message, and response models (`AgentConfig`, `AgentMessage`, `AgentResponse`)
- Lazy or explicit initialization through `initialize()`
- A default single-turn execution flow through `run()`
- LangChain prompt and runnable-chain construction
- Tool binding, multi-round tool-call execution, and tool execution logging
- In-memory conversation history
- Per-turn and cumulative token accounting

Because `BaseAgent` inherits from `ABC`, concrete agents must implement
`build_tools()` and `build_system_prompt()`.

## Data Models

### `AgentConfig`

Purpose:
Defines configuration values shared by all agents.

Why it is written this way:
- Uses Pydantic for validation and type safety.
- `extra="forbid"` prevents unknown fields, reducing silent misconfiguration.
- `arbitrary_types_allowed=True` supports non-Pydantic-native types when needed.

Key fields:
- `name`: required, non-empty identifier.
- `description`: optional descriptive text.
- `model_provider`: default provider fallback (`"openai"` or `"ollama"`).
- `model_name`, `temperature`, `max_tokens`: model behavior.
- `system_prompt`: base instruction text.
- `tags`, `verbose`: metadata/flags for caller use.

Features:
- Validation constraints (`min_length`, numeric bounds) enforce safer defaults.
- `max_tokens` may be `None`; otherwise it must be greater than zero.

### `AgentMessage`

Purpose:
Represents each conversation message in internal history.

Why it is written this way:
- Normalizes all message items to a consistent shape.
- Restricts `role` to one of `system|user|assistant|tool` via regex.

Features:
- `metadata` field allows attaching extra per-message details without changing the schema.

### `AgentResponse`

Purpose:
Defines the standard output contract for one agent turn.

Why it is written this way:
- Keeps response content separate from full message history and metadata.
- Gives downstream callers one predictable response shape.

Features:
- Includes `metadata` for model name, token usage, and other execution details.

## BaseAgent Methods (Detailed)

### `__init__(self, config: AgentConfig) -> None`

Initializes core runtime state without creating a model or chain.

- Stores the supplied config.
- Creates mutable containers for `tools`, `memory`, `chat_history`, and
  `tool_execution_log`.
- Sets `_model` and `chain` to `None`, allowing setup to be lazy.

Features:
- Supports lazy initialization by setting `_model` and `chain` to `None` initially.

### `name` (property)

Purpose:
Returns `config.name` via property access.

Purpose:
- Convenience accessor so callers use `agent.name` directly.

Features:
- Keeps external usage clean while preserving central config ownership.

### `description` (property)

Purpose:
Returns `config.description`.

Purpose:
- Same reason as `name`: convenient read access from config.

### `model` (property + setter)

Purpose:
Encapsulates read/write access to `_model`.

Purpose:
- Provides a controlled place to set model instance(s).
- Keeps internal storage in `_model` while exposing `model` as public API.

Features:
- Type allows both concrete `ChatOpenAI` and generic `RunnableSerializable`.

### `initialize(self) -> None`

Purpose:
Builds the MCP client, tools, system prompt, memory, model, and chain before first run.

Purpose:
- Centralizes one-time setup in correct order.
- Ensures `run` can lazily call setup only when needed.

1. Loads environment variables via `load_dotenv()`.
2. Connects the MCP client (`_setup_mcp_client`), creating one via `default_mcp_client()` if none is set.
3. Builds tools from subclass (`build_tools`) merged with any MCP-provided tools (`_mcp_tools`).
4. Refreshes config with subclass system prompt (`build_system_prompt`).
5. Initializes memory defaults.
6. Initializes model if missing.
7. Builds runnable chain.

Calling `initialize()` again rebuilds the tool list and chain, but preserves existing
memory values because `_setup_memory()` uses `setdefault`. Usually initialize once
and then call `run()` for subsequent turns.

### `default_mcp_client(self) -> BaseMCPClient | None`

Purpose:
Builds the default MCP client from `config.mcp_servers`.

Purpose:
- Gives out-of-the-box MCP connectivity without requiring every agent to wire it up manually.
- Leaves a clear extension point for agents that need a custom transport, auth, or connection strategy.

Features:
- Returns `None` when `config.mcp_servers` is empty, so agents without MCP needs pay no cost.
- See [MCP.md](MCP.md) for the full client implementation and override examples.

### `shutdown(self) -> None`

Purpose:
Releases the MCP client connection(s) held by this agent.

Purpose:
- Gives callers an explicit teardown hook for long-lived MCP sessions (stdio subprocess, sse/http connections).

### `_setup_memory(self) -> None`

Ensures these memory entries exist:

- `history`: an application-reserved list, initialized to `[]`.
- `token_usage`: cumulative counters for `input_tokens`, `output_tokens`, and
  `total_tokens`.
- `tool_execution_log`: a reference to the agent's current tool log.

Existing values are preserved with `setdefault`. `BaseAgent` uses `chat_history` as
the source of conversation messages; callers that need persistence must implement it
outside this class.

### `_setup_model(self) -> None`

Purpose:
Ensures a model exists.

Purpose:
- Keeps model bootstrap logic separate and testable.
- Only creates default model when `_model` is still `None`.

### `default_model(self) -> ChatOpenAI | RunnableSerializable[Any, Any]`

Purpose:
Creates the default model instance for the agent.

Purpose:
- Gives out-of-the-box behavior for OpenAI provider.
- Leaves extension point for other providers by override.

Features:
- Validates provider (`openai` only in default implementation).
- Binds tools to model when tools are present (`model.bind_tools(self.tools)`).

To support another provider, override this method and assign a compatible runnable to
`self.model` before `build_chain()` is called.

### `build_prompt(self) -> ChatPromptTemplate`

Purpose:
Constructs the default chat prompt template.

Purpose:
- Standardizes message structure:
  - system prompt
  - prior history
  - current user input

Features:
- Uses `MessagesPlaceholder("history")` to inject structured prior context.

### `build_chain(self) -> RunnableSerializable[Any, Any]`

Purpose:
Builds runnable pipeline for prompt + model.

Purpose:
- Isolates chain composition in one method.
- Allows override for custom chains.

Features:
- Raises explicit error if model is missing.

### `add_message(self, message: AgentMessage) -> None`

Purpose:
Appends a typed message to chat history.

Purpose:
- Small dedicated helper keeps history mutations explicit.

### `reset_history(self) -> None`

Resets all per-conversation state without recreating the agent:

- Clears `chat_history`.
- Clears `tool_execution_log` and updates `memory["tool_execution_log"]`.
- Resets cumulative `memory["token_usage"]` counters to zero.

It does not rebuild the model, tools, or chain.

### `run(self, user_input: str) -> AgentResponse`

Purpose:
Default single-turn execution entrypoint.

Purpose:
- Implements common orchestration:
  - lazy initialization
  - parse input
  - append user message
  - generate response
  - append assistant message

Features:
- Can be overridden by subclasses for custom turn handling.
- Calls `initialize()` automatically when `chain` is `None`.

### `parse_user_input(self, user_input: str) -> AgentMessage`

Purpose:
Converts raw user text into internal typed message format.

Purpose:
- Keeps input normalization in one method.
- Makes subclass customization easy (e.g., metadata enrichment).

### `generate_response(self, messages: Sequence[AgentMessage]) -> AgentResponse`

Purpose:
Generates one assistant response from conversation state.

Purpose:
- Encapsulates core response logic separate from orchestration in `run`.

Detailed behavior:
1. Ensures chain is initialized.
2. Finds last user message.
3. Converts prior messages to LangChain message classes.
4. Invokes chain with `system_prompt`, `history`, and current `input`.
5. Extracts response content.
6. If the model requests tools, executes them and continues the model loop, up to
  five rounds.
7. Extracts token usage for this turn from the final model response.
8. Updates cumulative token usage.
9. Returns standardized `AgentResponse` including metadata.

Features:
- Returns both per-turn and cumulative token usage metadata:
  - `token_usage`
  - `conversation_token_usage`
  - `total_tokens_consumed`
- Returns the current tool log as `metadata["tool_execution_log"]`.

If there is no user message in `messages`, the method raises `ValueError`. If the
agent has no initialized chain, it raises `RuntimeError` and asks the caller to
initialize the agent first.

### `_wrap_tool(self, tool: BaseTool) -> BaseTool`

Creates an agent-local copy of a tool and wraps its callable so every execution is
recorded by `log_tool_execution()`. The implementation first tries Pydantic's
`model_copy(deep=True)` and then falls back to `deepcopy`; if neither is possible,
it uses the original object. Tools without a callable `func` are returned unchanged.

The copy is important when the same LangChain tool object is supplied to multiple
agents: logging behavior should not leak from one agent instance into another.

### `log_tool_execution(self, tool_name, tool_input, tool_output) -> None`

Appends a UTC-timestamped entry containing `agent`, `tool`, `input`, and `output` to
`tool_execution_log`, mirrors the list into `memory["tool_execution_log"]`, and prints
a compact execution line for debugging.

### `_execute_tool_calls(self, model_response, history, last_user_message, *, max_rounds=5) -> Any`

Handles tool calls requested by the model. For each call it finds the matching tool by
name, invokes it with the supplied arguments, adds a `ToolMessage`, and invokes the
prompt/model chain again. The loop stops when the model no longer requests tools, when
no matching tool can be invoked, or after `max_rounds` rounds. The default model binds
the initialized tools, so the normal path requires no extra caller code.

### `_to_langchain_message(self, message: AgentMessage) -> BaseMessage`

Purpose:
Maps internal `AgentMessage` objects to LangChain message objects.

Purpose:
- Internal history format is framework-neutral.
- Chain invocation expects LangChain message instances.

Features:
- Handles `system`, `assistant`, `tool`, and user fallback.
- For tool messages, uses metadata name and `tool_call_id`.

### `_extract_token_usage(self, model_response: Any) -> dict[str, int]`

Purpose:
Extracts usage counters from model response metadata.

Purpose:
- Different providers/chains may expose usage in different metadata shapes.

Features:
- Checks `usage_metadata` first.
- Falls back to `response_metadata["token_usage"]` shape.
- Normalizes output to:
  - `input_tokens`
  - `output_tokens`
  - `total_tokens`

### `_update_total_token_usage(self, turn_usage: dict[str, int]) -> dict[str, int]`

Purpose:
Accumulates token usage across turns.

Purpose:
- Conversation-level token tracking is needed in long-running sessions.

Features:
- Defensive default if memory structure is missing/corrupted.
- Returns a copied totals dict after update.

### `build_tools(self) -> list[BaseTool]` (abstract)

Purpose:
Subclass must define its available tools.

Why it is abstract:
- Toolset is agent-specific and should not be guessed in base class.

Features:
- Returned tools automatically influence default model behavior in `default_model`.

### `build_system_prompt(self) -> str` (abstract)

Purpose:
Subclass must define system prompt text.

Why it is abstract:
- Role and behavior instructions are agent-specific.

Features:
- Called during `initialize` and stored back into `config.system_prompt`.

## Feature Summary

Implemented features in current `BaseAgent`:
- Typed configuration and message contracts
- Lazy initialization
- Prompt/model chain composition
- Per-agent tool cloning and logging
- Tool binding and automatic multi-round tool execution
- Turn-level and conversation-level token accounting
- Clear subclass extension points (`build_tools`, `build_system_prompt`, optional overrides of public methods)

## Sample Code Snippets

### 1) Minimal subclass

```python
from langchain_core.tools import BaseTool
from BasePackage import BaseAgent, AgentConfig


class MyAgent(BaseAgent):
    def build_tools(self) -> list[BaseTool]:
        return []

    def build_system_prompt(self) -> str:
        return "You are a concise assistant for AIStudy demos."


agent = MyAgent(AgentConfig(name="my-agent"))
agent.initialize()
result = agent.run("Explain transformer models in two bullets.")
print(result.content)
print(result.metadata["total_tokens_consumed"])
```

`run()` may be called without an explicit `initialize()` because it initializes lazily.
Explicit initialization is useful when you want setup errors to occur before accepting
user input.

### 2) Subclass with a tool

```python
from BasePackage import AgentConfig, BaseAgent
from langchain_core.tools import BaseTool, tool


@tool
def lookup_status(service: str) -> str:
  """Return the current status for a service."""
  return f"{service}: operational"


class OperationsAgent(BaseAgent):
  def build_tools(self) -> list[BaseTool]:
    return [lookup_status]

  def build_system_prompt(self) -> str:
    return "You are an operations assistant. Use lookup_status when needed."


agent = OperationsAgent(AgentConfig(name="operations-agent"))
response = agent.run("Is the payments service operational?")
print(response.content)
print(agent.tool_execution_log)
```

During `initialize()`, `lookup_status` is cloned and wrapped, then bound to the
OpenAI model. When the model requests it, `BaseAgent` invokes the tool, logs the
execution, and sends the tool result back to the model for the final answer.

### 3) Custom model or chain

Assign a compatible LangChain runnable before initialization, or override
`default_model()` when provider-specific setup is needed:

```python
from langchain_core.runnables import RunnableSerializable


class CustomModelAgent(MyAgent):
  def default_model(self) -> RunnableSerializable:
    # Return a provider-specific chat model or another compatible runnable.
    return build_my_chat_model()
```

The default `default_model()` supports OpenAI and Ollama. At initialization, `.env`
values override the corresponding `AgentConfig` provider and model values:

```dotenv
# OpenAI (default)
AGENT_MODEL_PROVIDER=openai
AGENT_MODEL_NAME=gpt-4o-mini
OPENAI_API_KEY=sk-...

# Local Ollama, using its OpenAI-compatible API
AGENT_MODEL_PROVIDER=ollama
AGENT_MODEL_NAME=llama3.2:3b
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_API_KEY=ollama
```

For Ollama, `OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434/v1` and
`OLLAMA_API_KEY` defaults to `ollama`. The placeholder key is needed by the
OpenAI-compatible client but is not validated by a standard local Ollama server.
An unsupported provider raises `ValueError` unless this method is overridden.

### 4) Custom `run` override (when special orchestration is needed)

```python
from BasePackage.AgentResponse import AgentResponse


class AuditedAgent(MyAgent):
    def run(self, user_input: str) -> AgentResponse:
        # Example custom behavior before default flow
        print("[audit] user input received")
        response = super().run(user_input)
        # Example custom behavior after default flow
        response.metadata["audited"] = True
        return response
```

### 5) Reading token usage and resetting a conversation

```python
response = agent.run("Give me one-line summary of LangChain.")
print(response.metadata["token_usage"])                # current turn
print(response.metadata["conversation_token_usage"])   # running total
print(response.metadata["total_tokens_consumed"])      # running total (int)

agent.reset_history()
assert agent.chat_history == []
assert agent.tool_execution_log == []
assert agent.memory["token_usage"]["total_tokens"] == 0
```

## Notes and Constraints (from current code)

- Default provider path supports only `openai` unless `default_model` is overridden.
- Token usage extraction depends on metadata fields present in model response.
- Base class does not persist history to disk; it is in-memory for the process lifetime.
- MCP connectivity is opt-in via `config.mcp_servers`; see [MCP.md](MCP.md) for the client contract, default implementation, and override patterns.
