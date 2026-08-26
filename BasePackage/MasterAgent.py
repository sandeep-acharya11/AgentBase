from __future__ import annotations

from typing import Literal

from BasePackage.AgentConfig import AgentConfig
from BasePackage.AgentResponse import AgentResponse
from BasePackage.BaseAgent import BaseAgent
from BasePackage.OrchestratorMixin import OrchestratorMixin


class MasterAgent(OrchestratorMixin, BaseAgent):
    """Master agent that orchestrates child agents with optional ReAct-like evaluation and iterative refinement."""

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
        self._iteration_counts: dict[str, int] = {}  # Track iterations per child
        self._enable_react_evaluation: bool = True  # Toggle ReAct evaluation on/off

    def initialize(self) -> None:
        super().initialize()
        for child in self._children.values():
            child.initialize()

    def run(self, user_input: str) -> AgentResponse:
        if self.chain is None:
            self.initialize()

        if self._enable_react_evaluation:
            return self._run_with_react_evaluation(user_input)
        else:
            return self._run_without_react_evaluation(user_input)

    def _run_with_react_evaluation(self, user_input: str) -> AgentResponse:
        """Execute with ReAct-style evaluation and iterative refinement (default)."""
        # Reset iteration counts for new execution
        self._iteration_counts = {}

        execution_plan = self._build_execution_plan(user_input)
        child_outputs: dict[str, str] = {}
        child_metadata: dict[str, dict[str, object]] = {}
        evaluation_results: dict[str, dict[str, object]] = {}

        # ReAct Loop: Execute and Evaluate
        for step in execution_plan["steps"]:
            child_name = str(step["child"])
            child_input = str(step["task"])
            step_key = f"{step['order']}.{child_name}"

            # Initialize iteration count for this child
            if child_name not in self._iteration_counts:
                self._iteration_counts[child_name] = 0

            # Execute child and evaluate response (with potential refinement loop)
            response, evaluation = self._execute_and_refine_child(
                child_name, child_input, user_input, child_outputs, step
            )

            child_outputs[step_key] = response.content
            child_metadata[step_key] = {
                **dict(response.metadata),
                "step": dict(step),
                "input": child_input,
            }
            evaluation_results[step_key] = evaluation

        # Evaluate final composite response
        final_text = self._compose_final_response(user_input, execution_plan, child_outputs)
        final_evaluation = self._evaluate_final_response(user_input, final_text, child_outputs)
        final_text = self._append_confidence_score(
            final_text,
            final_evaluation.get("quality_score", 0.5),
        )

        orchestration_metadata: dict[str, object] = {
            "agent_name": self.name,
            "orchestration": {
                "routing_mode": self.routing_mode,
                "routing": dict(self._last_routing_metadata),
                "execution_plan": execution_plan,
                "selected_children": [str(step["child"]) for step in execution_plan["steps"]],
                "child_outputs": child_outputs,
                "child_metadata": child_metadata,
                "react_evaluation": {
                    "child_evaluations": evaluation_results,
                    "final_evaluation": final_evaluation,
                    "iteration_counts": dict(self._iteration_counts),
                },
            },
        }

        return AgentResponse(
            content=final_text,
            messages=list(self.chat_history),
            metadata=orchestration_metadata,
        )

    def _run_without_react_evaluation(self, user_input: str) -> AgentResponse:
        """Execute without ReAct evaluation - fast single-pass execution."""
        execution_plan = self._build_execution_plan(user_input)
        child_outputs: dict[str, str] = {}
        child_metadata: dict[str, dict[str, object]] = {}

        # Single-pass execution without evaluation loop
        for step in execution_plan["steps"]:
            child_name = str(step["child"])
            child_input = str(step["task"])
            step_key = f"{step['order']}.{child_name}"

            if bool(step.get("use_previous_outputs", False)) and child_outputs:
                child_input = self._compose_step_input(child_input, child_outputs)

            response = self._children[child_name].run(child_input)
            child_outputs[step_key] = response.content
            child_metadata[step_key] = {
                **dict(response.metadata),
                "step": dict(step),
                "input": child_input,
            }

        final_text = self._compose_final_response(user_input, execution_plan, child_outputs)
        final_evaluation = self._evaluate_final_response(user_input, final_text, child_outputs)
        final_text = self._append_confidence_score(
            final_text,
            final_evaluation.get("quality_score", 0.5),
        )

        orchestration_metadata: dict[str, object] = {
            "agent_name": self.name,
            "orchestration": {
                "routing_mode": self.routing_mode,
                "routing": dict(self._last_routing_metadata),
                "execution_plan": execution_plan,
                "selected_children": [str(step["child"]) for step in execution_plan["steps"]],
                "child_outputs": child_outputs,
                "child_metadata": child_metadata,
                "final_evaluation": final_evaluation,
                "react_evaluation": None,  # No evaluation when disabled
            },
        }

        return AgentResponse(
            content=final_text,
            messages=list(self.chat_history),
            metadata=orchestration_metadata,
        )

    def _select_children(self, user_input: str) -> list[str]:
        if self.routing_mode == "llm":
            return self._select_children_llm(user_input)
        return self._select_children_keyword(user_input)

    def _select_children_llm(self, user_input: str) -> list[str]:
        if not self._children:
            raise ValueError("No child agents configured. Set MasterAgent.children or use add_child()")

        if self.model is None:
            self._last_routing_metadata = {
                "strategy": "llm",
                "match_type": "fallback",
                "reason": "model_not_initialized",
            }
            return self._fallback_children()

        child_descriptions = "\n".join(
            f"- {name}: {child.description or 'No description'}"
            for name, child in self._children.items()
        )
        router_prompt = (
            "You are a routing assistant for a multi-agent system.\n"
            "Select one or more child agents that should handle the user request.\n"
            "Only select names from the available child agents list.\n"
            "Return only JSON in this exact schema:\n"
            '{"selected_children": ["child_name"], "reason": "short reason"}\n\n'
            f"Available child agents:\n{child_descriptions}\n\n"
            f"User request:\n{user_input}"
        )

        try:
            router_response = self.model.invoke(router_prompt)
            raw_content = str(getattr(router_response, "content", router_response))
            parsed = self._parse_json_response(raw_content)
            selected = self._validate_selected_children(parsed.get("selected_children", []))

            if selected:
                self._last_routing_metadata = {
                    "strategy": "llm",
                    "match_type": "llm_match",
                    "reason": str(parsed.get("reason", "")),
                }
                print(f"LLM routing selected child agents: {selected}")
                return selected

            self._last_routing_metadata = {
                "strategy": "llm",
                "match_type": "fallback",
                "reason": "llm_returned_no_valid_children",
            }
        except Exception as exc:
            self._last_routing_metadata = {
                "strategy": "llm",
                "match_type": "fallback",
                "reason": f"llm_router_error: {exc}",
            }

        print("LLM routing fallback triggered. Using keyword/default routing.")
        return self._select_children_keyword(user_input)

    def _validate_selected_children(self, selected_children: object) -> list[str]:
        if not isinstance(selected_children, list):
            return []

        validated: list[str] = []
        for child_name in selected_children:
            if not isinstance(child_name, str):
                continue
            if child_name not in self._children:
                continue
            if child_name not in validated:
                validated.append(child_name)
        return validated


    def _execute_and_refine_child(
        self,
        child_name: str,
        child_input: str,
        user_input: str,
        prior_outputs: dict[str, str],
        step: dict[str, object],
    ) -> tuple[AgentResponse, dict[str, object]]:
        """Execute child agent and iteratively refine response if quality is insufficient."""
        if bool(step.get("use_previous_outputs", False)) and prior_outputs:
            child_input = self._compose_step_input(child_input, prior_outputs)

        # Initial execution
        response = self._children[child_name].run(child_input)
        self._iteration_counts[child_name] += 1

        # Evaluate initial response
        evaluation = self._evaluate_child_response(
            child_name, child_input, response.content, user_input
        )

        print(f"[{child_name}] Evaluation - Quality: {evaluation.get('quality_score', 0):.2f}, Needs Refinement: {evaluation.get('needs_refinement', False)}")

        # ReAct refinement loop
        while (
            evaluation.get("needs_refinement", False)
            and self._iteration_counts[child_name] < self.MAX_ITERATIONS_PER_CHILD
        ):
            feedback = evaluation.get("feedback", "")
            print(f"[{child_name}] Refining response... (Iteration {self._iteration_counts[child_name] + 1}/{self.MAX_ITERATIONS_PER_CHILD})")

            # Ask child to refine with feedback
            response = self._refine_child_response(
                child_name, child_input, response.content, feedback
            )
            self._iteration_counts[child_name] += 1

            # Re-evaluate refined response
            evaluation = self._evaluate_child_response(
                child_name, child_input, response.content, user_input
            )
            print(f"[{child_name}] Re-evaluation - Quality: {evaluation.get('quality_score', 0):.2f}, Needs Refinement: {evaluation.get('needs_refinement', False)}")

        return response, evaluation


    def _refine_child_response(
        self,
        child_name: str,
        original_task: str,
        current_response: str,
        feedback: str,
    ) -> AgentResponse:
        """Ask a child agent to refine its response based on feedback."""
        refinement_prompt = (
            f"Your previous response had the following feedback for improvement:\n"
            f"{feedback}\n\n"
            f"Original task: {original_task}\n\n"
            f"Your previous response:\n{current_response}\n\n"
            f"Please provide an improved response that addresses the feedback above. "
            f"Focus on: completeness, clarity, and direct relevance to the original task."
        )

        return self._children[child_name].run(refinement_prompt)
