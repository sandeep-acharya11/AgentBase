# OrchestratorMixin: Shared Orchestration Contract and Defaults

## Overview

OrchestratorMixin is the shared orchestration layer used by both MasterAgent (sequential) and MasterAgentLanggraph (graph-based). It now contains both:

1. The abstract orchestration contract that concrete orchestrators must satisfy.
2. The shared concrete implementations that were previously duplicated in both classes.

This makes OrchestratorMixin the single source of truth for common orchestration behavior.

---

## Inheritance

```
BaseAgent (ABC)
OrchestratorMixin (ABC + shared concrete orchestration defaults)
    |
    |-- MasterAgent(OrchestratorMixin, BaseAgent)
    |-- MasterAgentLanggraph(OrchestratorMixin, BaseAgent)
```

---

## Required State Initialized by Concrete Classes

Concrete orchestrators must initialize these fields in __init__:

- self._children: dict[str, BaseAgent]
- self._routing_keywords: dict[str, tuple[str, ...]]
- self.default_child: str | None
- self._routing_mode: Literal["keyword", "llm"]
- self._last_routing_metadata: dict[str, object]
- self._enable_react_evaluation: bool

OrchestratorMixin reads and updates these fields in its concrete methods.

---

## Abstract Members (Still Required)

These remain abstract because they are strategy- or domain-specific:

- setup_child_agents()
  - Domain wiring point (which child agents to register and how).
- run(user_input: str) -> AgentResponse
  - Execution-engine specific entrypoint (sequential vs graph workflow).

---

## Shared Concrete Implementations (Now Centralized Here)

The following implementations are concrete in OrchestratorMixin and should not be re-implemented in MasterAgent or MasterAgentLanggraph unless intentionally overriding behavior.

### 1. Child registry and routing configuration properties

- children (getter/setter)
- routing_keywords (getter/setter)
- routing_mode (getter/setter with validation)
- enable_react_evaluation (getter/setter)

### 2. Child registration

- add_child(child_name, child_agent, keywords=None, set_default=False)

Behavior:
- Registers/replaces a child.
- Normalizes keywords to lowercase.
- Sets default_child when requested or when first child is added.

Example:

```python
orchestrator.add_child(
    "planner",
    planner_agent,
    keywords=["plan", "roadmap", "break down"],
    set_default=True,
)

assert orchestrator.routing_keywords["planner"] == (
    "plan", "roadmap", "break down"
)
```

### 3. Default BaseAgent hooks

- build_tools() -> list[BaseTool] (default empty list)
- build_system_prompt() -> str (default orchestration prompt)

### 4. Shared planning and routing helpers

- _build_execution_plan(user_input)
- _build_keyword_execution_plan(user_input, selected_children)
- _build_llm_execution_plan(user_input)
- _select_children_keyword(user_input)
- _fallback_children()
- _validate_execution_plan(parsed_plan)

Keyword routing selects every registered child whose configured keyword occurs in the
lowercase user input. If there are no matches, routing falls back to `default_child`,
then to the first registered child. If no child exists, it raises `ValueError`.

With `routing_mode="llm"`, the mixin asks the configured model for a JSON execution
plan. Plans are accepted only when they contain valid steps, known child names, and
string tasks. Invalid JSON, invalid steps, planning errors, or an unavailable model
fall back to keyword routing.

Each plan step has this shape:

```python
{
    "order": 1,
    "child": "planner",
    "task": "Break the request into implementation steps.",
    "purpose": "Create an actionable plan.",
    "use_previous_outputs": False,
}
```

### 5. Shared response composition helpers

- _compose_step_input(task, child_outputs)
- _compose_final_response(user_input, execution_plan, outputs)

`_compose_step_input()` adds earlier child outputs to a later task when the step has
`use_previous_outputs=True`. Final synthesis condenses each child output to at most
1,400 characters and the combined synthesis context to at most 7,000 characters.

If a model is available, `_compose_final_response()` asks it to produce a structured
answer. If synthesis fails or no model is available, it returns a deterministic
fallback containing the original ask, short answer, key points, recommendations,
next steps, and a follow-up question.

When a concrete orchestrator provides a callable `_stream_callback` and its model
supports `.stream()`, synthesized tokens are sent as `("token", {"text": text})`
events before the final response is returned.

### 6. Shared evaluation helpers

- _evaluate_child_response(child_name, task_input, response_content, user_request)
- _evaluate_final_response(user_input, final_response, child_outputs)

When a model is unavailable, child and final evaluation return deterministic defaults.
Model-backed child evaluation scores completeness, clarity, relevance, and coherence;
final evaluation scores overall quality and coherence. The concrete `MasterAgent`
uses `CONFIDENCE_THRESHOLD = 0.75` and its own iteration limit to decide whether to
refine a child response. The mixin itself exposes the evaluation helpers and toggle,
but does not implement the concrete sequential or graph execution loop.

### 7. Shared JSON parsing helper

- _parse_json_response(raw_content)

### 8. Shared FastAPI wrapper

- build_default_cors_options() -> dict[str, object]
- register_api_middlewares(app: FastAPI) -> None
- configure_api_app(app: FastAPI, enable_cors=True, cors_options=None) -> None
- _prepare_api_request(request: OrchestratorAPIRequest)
- _execute_api_request(request: OrchestratorAPIRequest) -> AgentResponse
- _build_api_response(response: AgentResponse) -> OrchestratorAPIResponse
- _run_api_request(request: OrchestratorAPIRequest) -> OrchestratorAPIResponse
- _format_sse_event(event_name: str, payload: Any) -> str
- _run_api_request_stream(request: OrchestratorAPIRequest)
- build_health_response() -> OrchestratorHealthResponse
- create_api_router(prefix="", tags=None, include_health=True) -> APIRouter
- create_api_app(..., prefix="/api/agent", tags=None, include_health=True, enable_cors=True, cors_options=None) -> FastAPI

Behavior summary:
- Lazily wires child agents by calling setup_child_agents() when needed.
- Supports orchestrator-state reset by default with reset_history=True. Concrete
    orchestrators should reset child agents too if child conversation state must also be
    isolated between requests.
- Supports per-request routing_mode override (keyword or llm).
- Supports per-request enable_react_evaluation override.
- Executes run() via run_in_threadpool so sync orchestrators work in async FastAPI routes.
- Returns typed response payloads via OrchestratorAPIResponse.
- Exposes both synchronous POST /run and Server-Sent Events POST /stream routes.
- Exposes optional health endpoint payload via OrchestratorHealthResponse.
- Applies default CORS middleware at app creation time (unless disabled).
- Adds default HTTP middleware that appends X-Process-Time-Ms response header.
- Allows concrete orchestrators to override CORS and middleware behavior without changing route wiring.

Streaming behavior summary:
- Stream route response type is text/event-stream.
- Stream starts with connected.
- keepalive is emitted every second while waiting for worker output.
- Stream forwards graph/orchestrator events as they occur (for example plan, step_started, step_completed).
- final always carries the full OrchestratorAPIResponse payload.
- done marks stream completion with success=true or success=false.
- error carries status_code and detail for failures.
- If no token events were emitted by the agent, the wrapper synthesizes token events by splitting final content on whitespace.

Errors are mapped consistently: `ValueError` becomes HTTP/SSE status 400, while other
execution failures become status 500 with an `Agent execution failed: ...` detail.
The request model rejects empty input, unknown fields, and invalid routing modes before
the orchestrator executes.

Default CORS configuration:
- allow_origins = ["*"]
- allow_credentials = True
- allow_methods = ["*"]
- allow_headers = ["*"]

Override points:
- Override build_default_cors_options() to tighten CORS defaults.
- Override register_api_middlewares(app) to add/replace middleware behavior.
- Override configure_api_app(app, ...) for full middleware-stack control.

## What Belongs in Concrete Orchestrators

Keep only execution-strategy logic in concrete classes.

MasterAgent:
- Sequential run paths (_run_with_react_evaluation, _run_without_react_evaluation)
- Sequential refinement loop helpers (_execute_and_refine_child, _refine_child_response)
- Optional selection-specific helpers only used by sequential class

MasterAgentLanggraph:
- Graph build and node functions (_build_graph, _node_*, _should_*)
- Graph visualization helpers

Concrete `run()` implementations should generally call `_build_execution_plan()`, pass
previous outputs only when requested by a plan step, call child agents, compose the
final response, and return an `AgentResponse` whose metadata contains an
`orchestration` object. The mixin intentionally does not dictate how sequential and
graph execution store intermediate state.

---

## FastAPI Wrapper Advantages

Using the wrapper built into OrchestratorMixin gives these practical benefits:

1. Zero duplication across master agents
    - Any orchestrator inheriting OrchestratorMixin gets the same API surface without rewriting routes.

2. Uniform contract for frontend integration
    - All orchestrators can expose consistent /run, /stream, and /health behavior with the same typed request/response schema.

3. Safer request handling
    - Pydantic validation rejects malformed payloads early (extra fields, missing input, invalid routing_mode values).

4. Reset behavior
        - reset_history=True clears the orchestrator state before each request by default.
            Concrete orchestrators should also reset child agents when child conversation
            state must be isolated between web requests.

5. Async compatibility with current sync agents
    - run_in_threadpool allows existing sync run() implementations to be served from async FastAPI handlers.

6. Better operability and observability
    - /health reports initialization state, registered child agents, active routing mode, and ReAct toggle state.

7. Cleaner extension path
    - You can add auth, rate limiting, CORS, or middleware at app level without touching orchestration logic.

## Minimal Example

```python
class MyOrchestrator(OrchestratorMixin, BaseAgent):
    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._children = {}
        self._routing_keywords = {}
        self.default_child = None
        self._routing_mode = "keyword"
        self._last_routing_metadata = {}
        self._enable_react_evaluation = True

    def setup_child_agents(self) -> None:
        # Only domain-specific child wiring is required here.
        # add_child is inherited from OrchestratorMixin.
        worker_config = AgentConfig(name="worker")
        worker = WorkerAgent(worker_config)
        self.add_child("worker", worker, keywords=["task", "work"], set_default=True)

    def run(self, user_input: str) -> AgentResponse:
        # Implement execution strategy (sequential, graph-based, etc.).
        execution_plan = self._build_execution_plan(user_input)
        outputs: dict[str, str] = {}

        for step in execution_plan["steps"]:
            child_name = str(step["child"])
            child_input = str(step["task"])
            if bool(step.get("use_previous_outputs", False)) and outputs:
                child_input = self._compose_step_input(child_input, outputs)

            response = self.children[child_name].run(child_input)
            outputs[f"{step['order']}.{child_name}"] = response.content

        return AgentResponse(
            content=self._compose_final_response(user_input, execution_plan, outputs),
            messages=list(self.chat_history),
            metadata={"orchestration": {"execution_plan": execution_plan}},
        )
```

Notes:
- You do not need to implement `add_child`, `children`, `routing_mode`, `routing_keywords`,
  or `enable_react_evaluation` in `MyOrchestrator` unless you want custom behavior.
- Those are shared concrete implementations provided by `OrchestratorMixin`.

---

### Routing and orchestration metadata

After a concrete orchestrator runs, consumers can inspect the normalized routing and
execution plan without parsing the response text:

```python
response = orchestrator.run("Create a deployment plan and explain the tradeoffs.")
orchestration = response.metadata["orchestration"]

print(orchestration["routing_mode"])
print(orchestration["routing"])
print(orchestration["selected_children"])
print(orchestration["execution_plan"]["steps"])
print(orchestration["child_outputs"])
```

For `MasterAgent` with ReAct enabled, the same metadata also contains
`react_evaluation.child_evaluations`, `react_evaluation.final_evaluation`, and
`react_evaluation.iteration_counts`. With ReAct disabled, `react_evaluation` is
`None` and `final_evaluation` remains available.

## Consumer Example: Create Wrapper, Run Server, Consume Endpoint

The example below shows how a consumer class can inherit the mixin, create its own API wrapper, run it, and consume it.

### 1. Consumer orchestrator class

```python
from BasePackage.AgentConfig import AgentConfig
from BasePackage.AgentResponse import AgentResponse
from BasePackage.BaseAgent import BaseAgent
from BasePackage.OrchestratorMixin import OrchestratorMixin


class ConsumerOrchestrator(OrchestratorMixin, BaseAgent):
    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._children: dict[str, BaseAgent] = {}
        self._routing_keywords: dict[str, tuple[str, ...]] = {}
        self.default_child: str | None = None
        self._routing_mode = "keyword"
        self._last_routing_metadata: dict[str, object] = {}
        self._enable_react_evaluation = True

    def setup_child_agents(self) -> None:
        # Register at least one child agent.
        # Use add_child(...) or set children/routing_keywords directly.
        pass

    def run(self, user_input: str) -> AgentResponse:
        # Your orchestration strategy implementation.
        # This can be sequential or graph-based.
        return AgentResponse(content=f"Handled: {user_input}", metadata={"agent_name": self.name})
```

### 2. Build FastAPI app from the consumer class

```python
import uvicorn

config = AgentConfig(name="consumer-orchestrator", description="Consumer API demo")
agent = ConsumerOrchestrator(config)

app = agent.create_api_app(
    title="Consumer Orchestrator API",
    description="API wrapper generated from OrchestratorMixin",
    prefix="/api/consumer",
    tags=["ConsumerOrchestrator"],
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

### 3. Consume the API

```bash
curl -X POST http://127.0.0.1:8000/api/consumer/run \
  -H "Content-Type: application/json" \
  -d "{\"input\":\"Investigate intermittent API failures\",\"routing_mode\":\"keyword\",\"enable_react_evaluation\":true,\"reset_history\":true}"
```

Expected endpoint behavior:
- POST /api/consumer/run executes the orchestrator and returns OrchestratorAPIResponse.
- POST /api/consumer/stream executes the orchestrator and returns SSE events.
- GET /api/consumer/health returns current wrapper health and orchestrator readiness.

Streaming event contract (current implementation):
- connected: Initial handshake event with UTC timestamp.
- keepalive: Heartbeat event with UTC timestamp while waiting for queued worker events.
- token: Incremental model output chunks when emitted by orchestration callbacks.
- final: Final OrchestratorAPIResponse payload serialized as JSON.
- error: Error payload with status_code and detail.
- done: Terminal event with success boolean.

Notes for frontend consumers:
- Request body for /stream is the same OrchestratorAPIRequest used by /run.
- reset_history defaults to true unless explicitly set to false.
- routing_mode and enable_react_evaluation can be overridden per request.
- For graph-based orchestrators, additional progress events may be emitted before final.

### Concrete wrapper pattern

Any concrete orchestrator can expose the shared API surface by creating a custom app wrapper with `create_api_app()`. The endpoint prefix is chosen by the concrete app, while the request/response contract remains the same across orchestrators.

Example:

```python
config = AgentConfig(name="demo-orchestrator", description="Demo API")
agent = MyOrchestrator(config)

app = agent.create_api_app(
    title="Demo Orchestrator API",
    prefix="/api/demo",
    tags=["DemoOrchestrator"],
)
```

This yields standard routes such as:

- POST /api/demo/run
- POST /api/demo/stream
- GET /api/demo/health

The important part is the wrapper pattern, not a repo-specific URL.

---

## CORS and Middleware Override Example

Use this pattern when a concrete orchestrator (for example, a MasterAgent variant)
needs stricter CORS and custom middleware while keeping the shared router behavior.

```python
from fastapi import FastAPI, Request


class EnterpriseOrchestrator(ConsumerOrchestrator):
    def build_default_cors_options(self) -> dict[str, object]:
        # Restrict to trusted frontends in production.
        return {
            "allow_origins": [
                "https://portal.contoso.com",
                "https://ops.contoso.com",
            ],
            "allow_credentials": True,
            "allow_methods": ["GET", "POST"],
            "allow_headers": ["Authorization", "Content-Type", "X-Request-ID"],
        }

    def register_api_middlewares(self, app: FastAPI) -> None:
        # Keep default process-time header middleware.
        super().register_api_middlewares(app)

        @app.middleware("http")
        async def add_request_id_header(request: Request, call_next):
            response = await call_next(request)
            request_id = request.headers.get("X-Request-ID")
            if request_id:
                response.headers["X-Request-ID"] = request_id
            return response
```

You can also disable CORS from the caller when needed:

```python
app = agent.create_api_app(
    prefix="/api/enterprise",
    include_health=True,
    enable_cors=False,
)
```

---

## Related Docs

- [MasterAgent.md](./MasterAgent.md) - Sequential-specific behavior only
- [MasterAgentLanggraph.md](./MasterAgentLanggraph.md) - Graph-specific behavior only
- [BaseAgent.md](./BaseAgent.md) - Base chat agent lifecycle and chain behavior
