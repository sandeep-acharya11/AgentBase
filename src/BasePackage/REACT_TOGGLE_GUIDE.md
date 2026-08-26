# ReAct Evaluation Toggle Guide

## Overview

You can toggle ReAct-style evaluation on/off using the `enable_react_evaluation` property.

This property is implemented in `OrchestratorMixin`, so it is available across orchestrators that inherit from it, including:

- MasterAgent (sequential)
- MasterAgentLanggraph (graph-based)
- IRRAgent
- IRRAgentLanggraph

**Default**: `True` (ReAct evaluation enabled)

---

## Usage

### Enable ReAct Evaluation (Default)

```python
from BasePackage.AgentConfig import AgentConfig
from IncResolverAgent import IRRAgent

irr_agent = IRRAgent(AgentConfig(name="test"))
irr_agent.setup_child_agents()
irr_agent.initialize()

# ReAct is enabled by default
response = irr_agent.run("incident description")

# Access evaluation results
react_eval = response.metadata["orchestration"]["react_evaluation"]
print(f"Quality: {react_eval['child_evaluations']['1.analyzer']['quality_score']}")
```

### Disable ReAct Evaluation

```python
from BasePackage.AgentConfig import AgentConfig
from IncResolverAgent import IRRAgent

irr_agent = IRRAgent(AgentConfig(name="test"))
irr_agent.setup_child_agents()

# Disable ReAct evaluation for faster execution
irr_agent.enable_react_evaluation = False

irr_agent.initialize()
response = irr_agent.run("incident description")

# react_evaluation will be None
print(response.metadata["orchestration"]["react_evaluation"])  # None
```

### Dynamic Toggle

```python
# Start with ReAct disabled for quick response
irr_agent.enable_react_evaluation = False
response1 = irr_agent.run("quick incident")

# Switch to ReAct enabled for quality assurance
irr_agent.enable_react_evaluation = True
response2 = irr_agent.run("critical incident")
```

---

## Behavior Comparison

### With ReAct Evaluation Enabled (`True`)

✅ **Pros:**
- Automatic quality assessment on each child response
- Iterative refinement up to 2 iterations per agent
- Quality scores on 4 criteria (completeness, clarity, relevance, coherence)
- Final composite response evaluation
- Detailed metadata for transparency

❌ **Cons:**
- More LLM API calls (depends on number of children and refinement loops)
- Slower execution (15-60 seconds depending on refinements)
- Higher costs

**Execution Flow:**
```
Execute Child 1
  ↓ Evaluate
  ↓ If quality < 0.75: Refine & Re-evaluate
Collect Output

Execute Child 2
  ↓ Evaluate
  ↓ If quality < 0.75: Refine & Re-evaluate
Collect Output

[...more children...]

Final Evaluation
```

### With ReAct Evaluation Disabled (`False`)

✅ **Pros:**
- Single-pass execution
- Fewer LLM API calls than ReAct-enabled runs
- Fast execution (5-15 seconds)
- Lower costs

❌ **Cons:**
- No quality assurance
- No iterative refinement
- No evaluation metadata
- May accept lower-quality responses

**Execution Flow:**
```
Execute Child 1 → Collect Output
Execute Child 2 → Collect Output
Execute Child 3 → Collect Output
Execute Child 4 → Collect Output

Compose Response
```

---

## Metadata Differences

### With ReAct Enabled

```python
response.metadata["orchestration"]["react_evaluation"] = {
    "child_evaluations": {
        "1.analyzer": {
            "quality_score": 0.82,
            "needs_refinement": False,
            "feedback": "...",
            "criteria": {
                "completeness": 0.85,
                "clarity": 0.80,
                "relevance": 0.82,
                "coherence": 0.80
            }
        },
        # ... more children
    },
    "final_evaluation": {
        "quality_score": 0.88,
        "is_coherent": True,
        "assessment": "..."
    },
    "iteration_counts": {
        "analyzer": 1,
        "generator": 2,
        "assessor": 1,
        "verifier": 1
    }
}
```

### With ReAct Disabled

```python
response.metadata["orchestration"]["react_evaluation"] = None
```

---

## When to Use Each Mode

### Use **Enabled** (ReAct On) When:

- ✅ Processing critical incidents where quality is paramount
- ✅ You need transparency into decision-making
- ✅ Cost is not a major constraint
- ✅ Execution time is not critical
- ✅ Detailed evaluation metadata is needed

**Examples:**
- Production incident resolution
- Security incident triage
- High-impact decision-making
- Regulatory/compliance scenarios

### Use **Disabled** (ReAct Off) When:

- ✅ You need fast turnaround
- ✅ Cost optimization is critical
- ✅ Low-stakes decisions
- ✅ Real-time requirements
- ✅ High-volume processing

**Examples:**
- Routine operational queries
- Bulk incident categorization
- Pre-filtering before detailed analysis
- Quick triage before escalation

---

## Example: Hybrid Approach

```python
from BasePackage.AgentConfig import AgentConfig
from IncResolverAgent import IRRAgent

irr_agent = IRRAgent(AgentConfig(name="hybrid"))
irr_agent.setup_child_agents()
irr_agent.initialize()

# Stage 1: Quick triage with ReAct disabled
irr_agent.enable_react_evaluation = False
quick_response = irr_agent.run(incident_description)

# Determine severity/impact...
if is_critical:
    # Stage 2: Detailed analysis with ReAct enabled
    irr_agent.enable_react_evaluation = True
    detailed_response = irr_agent.run(incident_description)
    # Use detailed_response with quality metrics
else:
    # Use quick_response for non-critical
    pass
```

---

## Property Details

### `enable_react_evaluation` Property

**Type:** `bool`

**Default:** `True`

**Declared in:** `BasePackage/OrchestratorMixin.py`

**Getter:**
```python
is_enabled = agent.enable_react_evaluation  # Returns bool
```

**Setter:**
```python
agent.enable_react_evaluation = True   # Enable ReAct
agent.enable_react_evaluation = False  # Disable ReAct

# Any truthy/falsy value is converted to bool
agent.enable_react_evaluation = 1      # Converted to True
agent.enable_react_evaluation = 0      # Converted to False
```

---

## Configuration Summary

| Setting | Default | Effect |
|---------|---------|--------|
| `enable_react_evaluation` | `True` | Toggle entire ReAct loop |
| `MAX_ITERATIONS_PER_CHILD` | `2` | Max refinement iterations (when enabled) |
| `CONFIDENCE_THRESHOLD` | `0.75` | Quality threshold for refinement (when enabled) |

---

## Code Examples

### Example 1: Conditional Toggle Based on Incident Severity

```python
def resolve_incident(irr_agent, incident):
    severity = extract_severity(incident)
    
    if severity == "critical":
        irr_agent.enable_react_evaluation = True
        print("Running with full quality assurance (ReAct enabled)")
    else:
        irr_agent.enable_react_evaluation = False
        print("Running in fast mode (ReAct disabled)")
    
    return irr_agent.run(incident)
```

### Example 2: A/B Testing

```python
# Get same incident with both modes
irr_agent.enable_react_evaluation = True
response_with_react = irr_agent.run(incident)
quality_with_react = response_with_react.metadata["orchestration"]["react_evaluation"]["final_evaluation"]["quality_score"]

irr_agent.enable_react_evaluation = False
response_without_react = irr_agent.run(incident)

print(f"With ReAct: {quality_with_react:.2f}")
print(f"Without ReAct: No quality metrics")
print(f"Time saved: Estimated 40-50 seconds")
```

### Example 3: Adaptive Mode

```python
class AdaptiveIRRAgent(IRRAgent):
    def run(self, user_input: str, quality_required=False):
        self.enable_react_evaluation = quality_required
        return super().run(user_input)

# Usage
agent = AdaptiveIRRAgent(config)
agent.setup_child_agents()
agent.initialize()

quick_response = agent.run(incident, quality_required=False)
quality_response = agent.run(incident, quality_required=True)
```

---

## See Also

- [OrchestratorMixin.md](./OrchestratorMixin.md) - Shared orchestration contract and common implementations
- [MasterAgent.md](./MasterAgent.md) - Sequential orchestration behavior
- [MasterAgentLanggraph.md](./MasterAgentLanggraph.md) - Graph orchestration behavior
- [IRRAgent.md](../IncResolverAgent/IRRAgent.md) - IRRAgent documentation
- [BaseAgent.md](./BaseAgent.md) - Base agent implementation
