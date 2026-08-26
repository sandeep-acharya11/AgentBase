"""
MasterAgentLanggraph - LangGraph-based Multi-Agent Orchestration

MasterAgentLanggraph implements orchestration using LangGraph instead of sequential execution.
It provides the same interface as MasterAgent but with graph-based workflow control,
better visualization, and more sophisticated state management.

Key advantages:
- Graph visualization of orchestration flow
- Conditional branching and loops via edges
- Natural representation of ReAct evaluation as subgraph
- Better state management for complex workflows
- Checkpoint and resume capabilities
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from BasePackage.AgentConfig import AgentConfig
from BasePackage.AgentResponse import AgentResponse
from BasePackage.BaseAgent import BaseAgent
from BasePackage.OrchestratorMixin import OrchestratorMixin


class OrchestrationState(TypedDict, total=False):
    """State dictionary for LangGraph orchestration workflow."""

    user_input: str
    execution_plan: dict[str, object]
    selected_children: list[str]
    child_outputs: dict[str, str]
    child_metadata: dict[str, dict[str, object]]
    evaluation_results: dict[str, dict[str, object]]
    iteration_counts: dict[str, int]
    refinement_counts: dict[str, int]
    final_text: str
    final_evaluation: dict[str, object]
    routing_metadata: dict[str, object]
    enable_react_evaluation: bool


class MasterAgentLanggraph(OrchestratorMixin, BaseAgent):
    """Master agent that orchestrates child agents using LangGraph."""

    # ReAct configuration constants
    MAX_ITERATIONS_PER_CHILD = 2
    CONFIDENCE_THRESHOLD = 0.75  # 0-1 scale for response quality

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._children: dict[str, BaseAgent] = {}
        self._routing_keywords: dict[str, tuple[str, ...]] = {}
        self.default_child: str | None = None
        self._routing_mode: Literal["keyword", "llm"] = "keyword"
        self._last_routing_metadata: dict[str, object] = {}
        self._enable_react_evaluation: bool = True
        self._graph: StateGraph | None = None

    def initialize(self) -> None:
        """Initialize base agent and child agents, then build the LangGraph."""
        super().initialize()
        for child in self._children.values():
            child.initialize()
        self._build_graph()

    def _build_graph(self) -> None:
        """Build the LangGraph workflow."""
        if self._graph is not None:
            return
        graph = StateGraph(OrchestrationState)

        # Add nodes
        graph.add_node("plan", self._node_plan)
        graph.add_node("execute", self._node_execute)
        graph.add_node("evaluate", self._node_evaluate)
        graph.add_node("refine", self._node_refine)
        graph.add_node("finalize", self._node_finalize)

        # Add edges
        graph.set_entry_point("plan")
        graph.add_edge("plan", "execute")
        graph.add_conditional_edges(
            "execute",
            self._should_evaluate,
            {"evaluate": "evaluate", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "evaluate",
            self._should_refine,
            {"refine": "refine", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "refine",
            self._should_evaluate_after_refine,
            {"evaluate": "evaluate", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)

        self._graph = graph.compile()

    def _emit_stream_event(self, event_name: str, payload: dict[str, object]) -> None:
        callback = getattr(self, "_stream_callback", None)
        if callable(callback):
            callback(event_name, payload)

    def run(self, user_input: str) -> AgentResponse:
        """Execute orchestration using LangGraph."""
        if self.chain is None:
            self.initialize()
        if self._graph is None:
            self._build_graph()
        if self._graph is None:
            raise RuntimeError("LangGraph workflow is not initialized.")

        # Initialize state
        initial_state: OrchestrationState = {
            "user_input": user_input,
            "execution_plan": {},
            "selected_children": [],
            "child_outputs": {},
            "child_metadata": {},
            "evaluation_results": {},
            "iteration_counts": {},
            "refinement_counts": {},
            "final_text": "",
            "final_evaluation": {},
            "routing_metadata": {},
            "enable_react_evaluation": self._enable_react_evaluation,
        }

        # Execute graph
        final_state = self._graph.invoke(initial_state)

        # Build response metadata
        orchestration_metadata: dict[str, object] = {
            "agent_name": self.name,
            "orchestration": {
                "routing_mode": self.routing_mode,
                "routing": final_state.get("routing_metadata", {}),
                "execution_plan": final_state.get("execution_plan", {}),
                "selected_children": final_state.get("selected_children", []),
                "child_outputs": final_state.get("child_outputs", {}),
                "child_metadata": final_state.get("child_metadata", {}),
                "react_evaluation": (
                    {
                        "child_evaluations": final_state.get("evaluation_results", {}),
                        "final_evaluation": final_state.get("final_evaluation", {}),
                        "iteration_counts": final_state.get("iteration_counts", {}),
                    }
                    if self._enable_react_evaluation
                    else None
                ),
            },
        }

        return AgentResponse(
            content=final_state.get("final_text", ""),
            messages=list(self.chat_history),
            metadata=orchestration_metadata,
        )

    # Graph Node Functions

    def _node_plan(self, state: OrchestrationState) -> OrchestrationState:
        """Plan node: Build execution plan."""
        user_input = state["user_input"]
        execution_plan = self._build_execution_plan(user_input)

        state["execution_plan"] = execution_plan
        state["selected_children"] = [str(step["child"]) for step in execution_plan.get("steps", [])]
        state["routing_metadata"] = dict(self._last_routing_metadata)

        steps = execution_plan.get("steps", [])
        print(f"[LangGraph] Plan: {len(steps)} steps")
        for step in steps:
            use_prev = "← uses prior outputs" if step.get("use_previous_outputs") else ""
            print(f"  Step {step['order']}: [{step['child']}] {step.get('purpose', '')} {use_prev}".rstrip())

        self._emit_stream_event(
            "plan",
            {
                "summary": str(execution_plan.get("summary", "")),
                "step_count": len(steps),
                "steps": [
                    {
                        "order": int(step.get("order", 0)),
                        "child": str(step.get("child", "")),
                        "purpose": str(step.get("purpose", "")),
                    }
                    for step in steps
                ],
            },
        )
        return state

    def _node_execute(self, state: OrchestrationState) -> OrchestrationState:
        """Execute node: Run all child agents in sequence."""
        execution_plan = state["execution_plan"]
        child_outputs: dict[str, str] = {}
        child_metadata: dict[str, dict[str, object]] = {}
        iteration_counts: dict[str, int] = {}

        for step in execution_plan.get("steps", []):
            child_name = str(step["child"])
            child_input = str(step["task"])
            step_key = f"{step['order']}.{child_name}"

            self._emit_stream_event(
                "step_started",
                {
                    "step_key": step_key,
                    "child": child_name,
                    "order": int(step.get("order", 0)),
                },
            )

            if child_name not in iteration_counts:
                iteration_counts[child_name] = 0

            if bool(step.get("use_previous_outputs", False)) and child_outputs:
                child_input = self._compose_step_input(child_input, child_outputs)

            response = self._children[child_name].run(child_input)
            child_outputs[step_key] = response.content
            child_metadata[step_key] = {
                **dict(response.metadata),
                "step": dict(step),
                "input": child_input,
            }
            iteration_counts[child_name] += 1

            print(f"[LangGraph] Executed {step_key}")
            self._emit_stream_event(
                "step_completed",
                {
                    "step_key": step_key,
                    "child": child_name,
                    "order": int(step.get("order", 0)),
                    "output_preview": response.content[:240],
                },
            )

        state["child_outputs"] = child_outputs
        state["child_metadata"] = child_metadata
        state["iteration_counts"] = iteration_counts

        return state

    def _node_evaluate(self, state: OrchestrationState) -> OrchestrationState:
        """Evaluate node: Assess child responses and composite."""
        if not state.get("enable_react_evaluation"):
            return state

        child_outputs = state["child_outputs"]
        child_metadata = state["child_metadata"]
        execution_plan = state["execution_plan"]
        evaluation_results: dict[str, dict[str, object]] = {}
        user_input = state["user_input"]

        # Evaluate each child response
        for step in execution_plan.get("steps", []):
            child_name = str(step["child"])
            step_key = f"{step['order']}.{child_name}"
            child_input = str(step["task"])

            if child_name not in state.get("iteration_counts", {}):
                state["iteration_counts"][child_name] = 0

            evaluation = self._evaluate_child_response(
                child_name, child_input, child_outputs[step_key], user_input
            )
            evaluation_results[step_key] = evaluation

            print(
                f"[LangGraph] Evaluated {step_key}: "
                f"Quality={evaluation.get('quality_score', 0):.2f}"
            )
            self._emit_stream_event(
                "step_evaluated",
                {
                    "step_key": step_key,
                    "quality_score": float(evaluation.get("quality_score", 0.0) or 0.0),
                    "needs_refinement": bool(evaluation.get("needs_refinement", False)),
                },
            )

        # Evaluate final composite
        final_text = self._compose_final_response(user_input, execution_plan, child_outputs)
        final_evaluation = self._evaluate_final_response(user_input, final_text, child_outputs)
        final_text = self._append_confidence_score(
            final_text,
            final_evaluation.get("quality_score", 0.5),
        )

        state["evaluation_results"] = evaluation_results
        state["final_text"] = final_text
        state["final_evaluation"] = final_evaluation
        self._emit_stream_event(
            "final_evaluated",
            {
                "quality_score": float(final_evaluation.get("quality_score", 0.0) or 0.0),
                "is_coherent": bool(final_evaluation.get("is_coherent", False)),
            },
        )

        return state

    def _node_refine(self, state: OrchestrationState) -> OrchestrationState:
        """Refine node: Re-run child agents whose responses need improvement."""
        if not state.get("enable_react_evaluation"):
            return state

        execution_plan = state["execution_plan"]
        child_outputs = dict(state["child_outputs"])
        child_metadata = dict(state["child_metadata"])
        evaluation_results = state.get("evaluation_results", {})
        refinement_counts: dict[str, int] = dict(state.get("refinement_counts") or {})
        user_input = state["user_input"]

        for step in execution_plan.get("steps", []):
            child_name = str(step["child"])
            step_key = f"{step['order']}.{child_name}"

            evaluation = evaluation_results.get(step_key, {})
            if not evaluation.get("needs_refinement", False):
                continue

            refinement_counts.setdefault(child_name, 0)
            if refinement_counts[child_name] >= self.MAX_ITERATIONS_PER_CHILD:
                print(f"[LangGraph] Skipping refine for {step_key}: max iterations reached")
                continue

            feedback = str(evaluation.get("feedback", ""))
            original_output = child_outputs.get(step_key, "")
            refine_input = (
                f"{step['task']}\n\n"
                f"Previous response:\n{original_output}\n\n"
                f"Please improve based on this feedback:\n{feedback}"
            )
            if bool(step.get("use_previous_outputs", False)) and child_outputs:
                refine_input = self._compose_step_input(refine_input, {
                    k: v for k, v in child_outputs.items() if k != step_key
                })

            response = self._children[child_name].run(refine_input)
            child_outputs[step_key] = response.content
            child_metadata[step_key] = {
                **dict(response.metadata),
                "step": dict(step),
                "input": refine_input,
                "refined": True,
            }
            refinement_counts[child_name] += 1
            print(f"[LangGraph] Refined {step_key} (iteration {refinement_counts[child_name]})")
            self._emit_stream_event(
                "step_refined",
                {
                    "step_key": step_key,
                    "child": child_name,
                    "iteration": int(refinement_counts[child_name]),
                },
            )

        state["child_outputs"] = child_outputs
        state["child_metadata"] = child_metadata
        state["refinement_counts"] = refinement_counts
        state["evaluation_results"] = {}  # Clear so evaluate re-assesses refined outputs
        state["final_text"] = ""  # Clear so finalize recomposes
        return state

    def _node_finalize(self, state: OrchestrationState) -> OrchestrationState:
        """Finalize node: Compose final response."""
        if not state.get("final_text"):
            user_input = state["user_input"]
            execution_plan = state["execution_plan"]
            child_outputs = state["child_outputs"]
            final_text = self._compose_final_response(user_input, execution_plan, child_outputs)
            final_evaluation = self._evaluate_final_response(user_input, final_text, child_outputs)
            final_text = self._append_confidence_score(
                final_text,
                final_evaluation.get("quality_score", 0.5),
            )
            state["final_text"] = final_text
            state["final_evaluation"] = final_evaluation

        print("[LangGraph] Finalized response")
        self._emit_stream_event(
            "finalizing",
            {
                "has_final_text": bool(state.get("final_text")),
            },
        )
        return state

    # Conditional edge functions

    def _should_evaluate(self, state: OrchestrationState) -> str:
        """Determine if evaluation should occur."""
        if state.get("enable_react_evaluation"):
            return "evaluate"
        return "finalize"

    def _should_refine(self, state: OrchestrationState) -> str:
        """Determine if refinement is needed."""
        refinement_counts = state.get("refinement_counts") or {}
        for step_key, evaluation in state.get("evaluation_results", {}).items():
            if not evaluation.get("needs_refinement", False):
                continue
            child_name = step_key.split(".", 1)[-1]
            if refinement_counts.get(child_name, 0) < self.MAX_ITERATIONS_PER_CHILD:
                return "refine"
        return "finalize"

    def _should_evaluate_after_refine(self, state: OrchestrationState) -> str:
        """After refinement, loop back to evaluate if any child was actually refined."""
        refinement_counts = state.get("refinement_counts") or {}
        if any(count > 0 for count in refinement_counts.values()):
            return "evaluate"
        return "finalize"

    def get_graph_mermaid(self) -> str:
        """Get Mermaid diagram representation of the graph."""
        if self._graph is None:
            return "Graph not initialized"
        try:
            return self._graph.get_graph().draw_mermaid()
        except Exception as exc:
            print(f"Mermaid diagram error: {exc}")
            return "Unable to generate Mermaid diagram"

    def get_graph_ascii(self) -> str:
        """Get ASCII art representation of the graph."""
        if self._graph is None:
            return "Graph not initialized"
        try:
            return self._graph.get_graph().draw_ascii()
        except Exception as exc:
            print(f"ASCII diagram error: {exc}")
            return "Unable to generate ASCII diagram"

    def visualize_workflow(self) -> None:
        """Display the LangGraph workflow visualization."""
        print("\n" + "=" * 80)
        print("LangGraph Orchestration Workflow")
        print("=" * 80)
        print(self.get_graph_ascii())
        print("=" * 80 + "\n")
