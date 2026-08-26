# MasterAgent: Sequential Orchestration

This document describes the current implementation in [MasterAgent.py](./MasterAgent.py).
`MasterAgent` is the sequential concrete orchestrator. Shared registration,
routing, planning, synthesis, evaluation, and FastAPI behavior is provided by
[OrchestratorMixin.py](./OrchestratorMixin.py).

Shared orchestration implementations (properties, child registration, planning/routing defaults, composition, evaluation helpers) are defined in [OrchestratorMixin.md](./OrchestratorMixin.md) and intentionally omitted here.

---

## Overview

MasterAgent executes a validated plan in order and combines the child responses
into one `AgentResponse`.

- Inherits: MasterAgent(OrchestratorMixin, BaseAgent)
- Uses shared orchestration defaults from OrchestratorMixin
- Adds sequential run paths and iterative refinement loop behavior
- Initializes every registered child when the master is initialized
- Appends a normalized final confidence score to the response text

---

## What MasterAgent Implements

### Sequential execution entrypoint

- run(user_input: str) -> AgentResponse

Behavior:
- Initializes lazily on first run.
- Dispatches to one of two sequential paths based on enable_react_evaluation.

### Sequential paths

- _run_with_react_evaluation(user_input)
  - Executes each planned step in sequence.
  - Evaluates each child output and refines when needed.
  - Evaluates final composed output.

- _run_without_react_evaluation(user_input)
  - Executes each planned step in sequence once.
  - No evaluation/refinement loop.

### Sequential refinement internals

- _execute_and_refine_child(...)
  - Executes child response.
  - Runs evaluation.
  - Repeats refinement up to MAX_ITERATIONS_PER_CHILD.

- _refine_child_response(...)
  - Constructs refinement prompt and re-runs the target child.

### Class-local routing helpers

- _select_children(...)
- _select_children_llm(...)
- _validate_selected_children(...)

These helpers validate direct LLM child selection and retain compatibility with
older callers. The normal `run()` path is plan-driven: it calls
`OrchestratorMixin._build_execution_plan()`, which uses
`_build_llm_execution_plan()` for LLM planning and keyword planning as its
fallback. The local selection helpers are not the primary planning path.

---

## Constants

- MAX_ITERATIONS_PER_CHILD = 2
- CONFIDENCE_THRESHOLD = 0.75

These constants are read by shared evaluation helpers in OrchestratorMixin and by sequential refinement logic in MasterAgent.

`MAX_ITERATIONS_PER_CHILD` includes the initial child call. With the default
value of `2`, a child can run once initially and at most once more after
evaluation requests refinement. `CONFIDENCE_THRESHOLD` is the minimum score
used by the evaluation helper when deciding whether refinement is needed.

---

## Initialization State

MasterAgent __init__ initializes the required shared state expected by OrchestratorMixin plus sequential counters:

- _children
- _routing_keywords
- default_child
- _routing_mode
- _last_routing_metadata
- _enable_react_evaluation
- _iteration_counts (sequential run bookkeeping)

---

## Metadata Shape

run() returns AgentResponse where metadata["orchestration"] includes:

- routing_mode
- routing
- execution_plan
- selected_children
- child_outputs
- child_metadata
- react_evaluation

When ReAct is enabled, react_evaluation includes child_evaluations, final_evaluation, iteration_counts.
When ReAct is disabled, react_evaluation is None.

The final response text also ends with `Confidence score: NN`, where `NN` is
the final evaluation score clamped to the range `0.00` to `1.00`. The score is
present in both execution modes because the final response is evaluated in both
paths.

Example metadata inspection:

```python
response = agent.run("Explain the incident and propose a recovery plan")
orchestration = response.metadata["orchestration"]

print(orchestration["routing_mode"])       # keyword or llm
print(orchestration["routing"])            # match/fallback details
print(orchestration["selected_children"])
print(orchestration["execution_plan"]["steps"])
print(orchestration["child_outputs"])
print(orchestration["child_metadata"])
print(orchestration["react_evaluation"])
```

Each output key is formatted as `"<order>.<child_name>"`, for example
`"1.analyzer"`. A later step receives prior outputs only when its plan step
has `use_previous_outputs=True`.

---

## What Is Not Documented Here

The following are shared in OrchestratorMixin and should be read there:

- children, routing_keywords, routing_mode, enable_react_evaluation
- add_child
- build_tools, build_system_prompt
- _build_execution_plan, _build_keyword_execution_plan, _build_llm_execution_plan
- _select_children_keyword, _fallback_children, _validate_execution_plan
- _compose_step_input, _compose_final_response
- _evaluate_child_response, _evaluate_final_response
- _parse_json_response

---

## Sample Orchestration Code

```python
from BasePackage.AgentConfig import AgentConfig
from BasePackage.BaseAgent import BaseAgent
from BasePackage.MasterAgent import MasterAgent


class EchoChildAgent(BaseAgent):
    def build_tools(self):
        return []

    def build_system_prompt(self) -> str:
        return "You are a focused helper for one orchestration step."


class DemoSequentialOrchestrator(MasterAgent):
    def setup_child_agents(self) -> None:
        analyzer = EchoChildAgent(
            AgentConfig(name="analyzer", description="Analyze the request")
        )
        planner = EchoChildAgent(
            AgentConfig(name="planner", description="Create an action plan")
        )

        self.children = {
            "analyzer": analyzer,
            "planner": planner,
        }
        self.routing_keywords = {
            "analyzer": ["analyze", "diagnose", "root cause"],
            "planner": ["plan", "steps", "solution"],
        }
        self.default_child = "analyzer"


config = AgentConfig(
    name="demo-sequential-master",
    description="Sequential orchestration demo",
)

agent = DemoSequentialOrchestrator(config)
agent.setup_child_agents()
agent.routing_mode = "keyword"  # or "llm"
agent.enable_react_evaluation = True  # set False for one-pass execution
agent.initialize()

response = agent.run("Analyze the incident and provide a recovery plan")
print(response.content)
print(response.metadata["orchestration"]["execution_plan"])
```

What this demonstrates:

- Subclassing MasterAgent and implementing setup_child_agents().
- Registering child agents, keywords, and a default fallback child.
- Running sequential orchestration with optional ReAct evaluation.

### Registering children incrementally

`add_child()` is usually less error-prone than assigning three separate
dictionaries. It also lowercases routing keywords and automatically assigns the
first child as the default unless another child is explicitly marked as the
default.

```python
agent = DemoSequentialOrchestrator(
  AgentConfig(name="demo-master", description="Sequential demo")
)

agent.add_child(
  "analyzer",
  EchoChildAgent(AgentConfig(name="analyzer", description="Analyze incidents")),
  keywords=["analyze", "diagnose"],
  set_default=True,
)
agent.add_child(
  "planner",
  EchoChildAgent(AgentConfig(name="planner", description="Plan recovery")),
  keywords=["plan", "steps", "recovery"],
)
agent.initialize()
```

### Keyword routing behavior

Keyword routing selects every registered child with at least one keyword found
in the lowercased request. If nothing matches, routing uses `default_child`.
If no valid default exists, it uses the first registered child. With multiple
matches, the children execute in registration order and later steps can receive
earlier outputs.

```python
agent.routing_mode = "keyword"
agent.routing_keywords = {
  "analyzer": ("analyze", "diagnose"),
  "planner": ("plan", "steps", "recovery"),
}
agent.default_child = "analyzer"

response = agent.run("Diagnose the payment failure and create recovery steps")
assert response.metadata["orchestration"]["selected_children"] == [
  "analyzer",
  "planner",
]
```

### LLM planning and fallback

Set `routing_mode` to `"llm"` to ask the initialized master model for an
ordered JSON execution plan. The plan is accepted only when it refers to
registered children and contains valid step data. Invalid JSON, an unavailable
model, or a planning exception falls back to keyword planning automatically.

```python
agent.routing_mode = "llm"
response = agent.run("Investigate the outage, assess risk, and recommend next steps")

plan = response.metadata["orchestration"]["execution_plan"]
print(plan["strategy"])  # "llm" when accepted, otherwise "keyword"
print(plan["summary"])
for step in plan["steps"]:
  print(step["order"], step["child"], step["task"])
```

LLM planning requires an initialized master model. Calling
`agent.initialize()` explicitly is useful when configuring routing, while
`run()` initializes lazily if `chain` is still `None`.

### Turning ReAct evaluation on or off

Evaluation is enabled by default. The enabled path evaluates every child,
refines responses below the confidence threshold, evaluates the final answer,
and records iteration counts. The disabled path makes one child call per plan
step and skips child evaluation/refinement, which reduces latency and model
usage.

```python
agent.enable_react_evaluation = True
quality_checked = agent.run("Analyze the incident and propose a fix")
react_details = quality_checked.metadata["orchestration"]["react_evaluation"]
print(react_details["iteration_counts"])

agent.enable_react_evaluation = False
fast = agent.run("Summarize the incident in three bullets")
assert fast.metadata["orchestration"]["react_evaluation"] is None
print(fast.metadata["orchestration"]["final_evaluation"])
```

### Running without an available model

Child agents still need to return `AgentResponse` objects, but the shared
composition and evaluation helpers have deterministic fallbacks when the
master model is unavailable. This is useful for tests and local wiring checks.

```python
agent.model = None
response = agent.run("Run the configured child")

assert "Original Ask:" in response.content
assert "Suggested Next Steps:" in response.content
assert "Confidence score:" in response.content
```

### Exposing the sequential orchestrator over FastAPI

The inherited API wrapper provides `/run`, `/stream`, and `/health` routes.
The request model accepts `input`, optional per-request `routing_mode`, optional
per-request `enable_react_evaluation`, and `reset_history` (default `True`).

```python
import uvicorn

api_agent = DemoSequentialOrchestrator(
  AgentConfig(name="demo-api", description="Sequential API demo")
)
api_agent.setup_child_agents()

app = api_agent.create_api_app(
  title="MasterAgent Demo",
  prefix="/api/master",
  tags=["MasterAgent"],
)

if __name__ == "__main__":
  uvicorn.run(app, host="127.0.0.1", port=8000)
```

Example request:

```bash
curl -X POST http://127.0.0.1:8000/api/master/run \
  -H "Content-Type: application/json" \
  -d "{\"input\":\"Analyze the incident\",\"routing_mode\":\"keyword\",\"enable_react_evaluation\":false}"
```

`/stream` returns Server-Sent Events. It starts with `connected`, may emit
`keepalive` and `token`, then emits `final` and `done`. `/health` reports the
agent name, initialization state, registered children, routing mode, and ReAct
toggle state.

---

## Related Docs

- [OrchestratorMixin.md](./OrchestratorMixin.md)
- [MasterAgentLanggraph.md](./MasterAgentLanggraph.md)
- [REACT_TOGGLE_GUIDE.md](./REACT_TOGGLE_GUIDE.md)
