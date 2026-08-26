# ReAct Evaluation Toggle Guide

## Overview

You can toggle ReAct-style evaluation on/off using the `enable_react_evaluation` property.

This property is implemented in `OrchestratorMixin`, so it is available across orchestrators that inherit from it, including:

- `MasterAgent` (sequential)
- `MasterAgentLanggraph` (graph-based)
- any custom orchestrator built on `OrchestratorMixin` + `BaseAgent`

**Default**: `True` (ReAct evaluation enabled)

---

## Usage

### Enable ReAct Evaluation (Default)

```python
from BasePackage.AgentConfig import AgentConfig
from BasePackage.MasterAgent import MasterAgent


class DemoWorkerAgent(BaseAgent):
    def build_tools(self):
        return []

    def build_system_prompt(self) -> str:
        return "You are a helpful worker agent."


class DemoOrchestrator(MasterAgent):
    def setup_child_agents(self) -> None:
        self.add_child(
            "worker",
            DemoWorkerAgent(AgentConfig(name="worker", description="Worker agent")),
            keywords=["task", "analyze", "plan"],
            set_default=True,
        )


agent = DemoOrchestrator(AgentConfig(name="demo-orchestrator"))
agent.setup_child_agents()
agent.initialize()

response = agent.run("Analyze this request and propose a plan")
react_eval = response.metadata["orchestration"]["react_evaluation"]
print(react_eval["final_evaluation"]["quality_score"])
```

### Disable ReAct Evaluation

```python
agent.enable_react_evaluation = False
response = agent.run("Summarize the issue in three bullet points")
print(response.metadata["orchestration"]["react_evaluation"])  # None
```

### Dynamic Toggle

```python
agent.enable_react_evaluation = False
quick_response = agent.run("Quick triage request")

agent.enable_react_evaluation = True
quality_response = agent.run("Critical review request")
```

---

## Behavior Comparison

### With ReAct Evaluation Enabled (`True`)

✅ **Pros:**
- Quality assessment on each child response
- Optional refinement loop up to the configured iteration limit
- Final-response evaluation metadata
- Better transparency for complex orchestration runs

❌ **Cons:**
- Additional model calls
- Higher latency and cost

**Execution Flow:**
```
Execute Child 1
  ↓ Evaluate
  ↓ If quality below threshold: refine and re-evaluate
Collect Output

Execute Child 2
  ↓ Evaluate
  ↓ If quality below threshold: refine and re-evaluate
Collect Output

Final evaluation
```

### With ReAct Evaluation Disabled (`False`)

✅ **Pros:**
- Single-pass execution
- Fewer model calls
- Faster turnaround and lower cost

❌ **Cons:**
- No child quality scoring
- No iterative refinement
- Less telemetry in the orchestration metadata

**Execution Flow:**
```
Execute Child 1 → Collect Output
Execute Child 2 → Collect Output
Compose final response
```

---

## Metadata Differences

### With ReAct Enabled

```python
response.metadata["orchestration"]["react_evaluation"] = {
    "child_evaluations": {
        "1.worker": {
            "quality_score": 0.82,
            "needs_refinement": False,
            "feedback": "...",
            "criteria": {
                "completeness": 0.85,
                "clarity": 0.80,
                "relevance": 0.82,
                "coherence": 0.80,
            },
        }
    },
    "final_evaluation": {
        "quality_score": 0.88,
        "is_coherent": True,
        "assessment": "...",
    },
    "iteration_counts": {
        "worker": 1,
    },
}
```

### With ReAct Disabled

```python
response.metadata["orchestration"]["react_evaluation"] = None
```

---

## When to Use Each Mode

### Use **Enabled** (ReAct On) When:

- Critical or high-stakes work needs stronger validation
- You want explicit quality metadata
- Cost and latency are acceptable

### Use **Disabled** (ReAct Off) When:

- You need fast, cheap execution
- The orchestration is straightforward and deterministic enough without refinement
- The response quality threshold is intentionally relaxed

---

## Example: Hybrid Approach

```python
agent.enable_react_evaluation = False
quick_response = agent.run("Quick triage request")

if needs_deeper_review:
    agent.enable_react_evaluation = True
    detailed_response = agent.run("Critical review request")
    print(detailed_response.metadata["orchestration"]["react_evaluation"])
else:
    print(quick_response.content)
```

---

## Property Details

### `enable_react_evaluation` Property

**Type:** `bool`

**Default:** `True`

**Declared in:** `BasePackage/OrchestratorMixin.py`

**Getter:**
```python
is_enabled = agent.enable_react_evaluation
```

**Setter:**
```python
agent.enable_react_evaluation = True
agent.enable_react_evaluation = False

agent.enable_react_evaluation = 1
agent.enable_react_evaluation = 0
```

The setter normalizes all truthy/falsy values to `bool`.

---

## Configuration Summary

| Setting | Default | Effect |
|---------|---------|--------|
| `enable_react_evaluation` | `True` | Toggle the ReAct loop |
| `MAX_ITERATIONS_PER_CHILD` | `2` | Maximum refinement iterations per child |
| `CONFIDENCE_THRESHOLD` | `0.75` | Minimum score before a response is refined |

---

## See Also

- [OrchestratorMixin.md](./OrchestratorMixin.md) - Shared orchestration contract and common implementations
- [MasterAgent.md](./MasterAgent.md) - Sequential orchestration behavior
- [MasterAgentLanggraph.md](./MasterAgentLanggraph.md) - Graph orchestration behavior
- [BaseAgent.md](./BaseAgent.md) - Base agent implementation
