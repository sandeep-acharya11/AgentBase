"""
OrchestratorMixin - The abstract orchestration interface.

Defines the full contract that every orchestrator agent must satisfy,
regardless of its underlying execution model (sequential, graph-based, etc.).

Any concrete orchestrator class must:
  1. Inherit from OrchestratorMixin alongside a BaseAgent subclass.
  2. Implement all @abstractmethod members defined here.

This is the single source of truth for the orchestration API. Adding a new
orchestration capability (e.g. a new routing strategy) starts here.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any, Iterable, Literal

from BasePackage.ApiModels import (
    OrchestratorAPIRequest,
    OrchestratorAPIResponse,
    OrchestratorHealthResponse,
)

if TYPE_CHECKING:
    from BasePackage.AgentResponse import AgentResponse
    from BasePackage.BaseAgent import BaseAgent
    from fastapi import APIRouter, FastAPI
    from langchain_core.tools import BaseTool


class OrchestratorMixin(ABC):
    """
    Abstract interface for all orchestrator agents.

    Orchestrators coordinate a set of child agents to decompose, route, and
    combine responses to a user request. This interface enforces the minimum
    contract that any orchestration strategy must expose, making it possible
    to swap sequential and graph-based implementations transparently.

    Inherit alongside BaseAgent (or any subclass of it):

        class MyOrchestrator(OrchestratorMixin, BaseAgent):
            ...

    Required state fields initialized by concrete classes:
    - self._children: dict[str, BaseAgent]
    - self._routing_keywords: dict[str, tuple[str, ...]]
    - self.default_child: str | None
    - self._routing_mode: Literal["keyword", "llm"]
    - self._last_routing_metadata: dict[str, object]
    - self._enable_react_evaluation: bool

    Only strategy-specific behavior should remain abstract here (run and
    setup_child_agents). Shared routing/planning/evaluation defaults are
    implemented in this mixin.
    """

    # ------------------------------------------------------------------
    # Child agent registry
    # ------------------------------------------------------------------

    @property
    def children(self) -> dict[str, BaseAgent]:
        """Return the registered child agents keyed by name."""
        return self._children

    @children.setter
    def children(self, value: dict[str, BaseAgent]) -> None:
        """Replace the full child agent registry."""
        self._children = dict(value)

    def add_child(
        self,
        child_name: str,
        child_agent: BaseAgent,
        keywords: Iterable[str] | None = None,
        set_default: bool = False,
    ) -> None:
        """
        Register a single child agent.

        Args:
            child_name:   Unique identifier used to reference this child.
            child_agent:  The BaseAgent instance to register.
            keywords:     Optional routing keywords that trigger this child.
            set_default:  If True, make this child the fallback when no
                          keywords match.
        """
        self._children[child_name] = child_agent
        if keywords is not None:
            self._routing_keywords[child_name] = tuple(keyword.lower() for keyword in keywords)
        if set_default or self.default_child is None:
            self.default_child = child_name

    @abstractmethod
    def setup_child_agents(self) -> None:
        """
        Instantiate and register all child agents for this orchestrator.

        Called once before the first run. Implementations should:
        - Create each child agent with an appropriate AgentConfig.
        - Register children via add_child() or the children/routing_keywords
          setters.
        - Assign self.default_child if a fallback is needed.

        This method is the primary extension point for domain-specific
        orchestrators (e.g. IRRAgent, IRRAgentLanggraph).
        """

    # ------------------------------------------------------------------
    # Routing configuration
    # ------------------------------------------------------------------

    @property
    def routing_mode(self) -> Literal["keyword", "llm"]:
        """
        Active routing strategy.

        - ``"keyword"``: Match user input against per-child keyword lists.
        - ``"llm"``:     Delegate routing decisions to the language model.
        """
        return self._routing_mode

    @routing_mode.setter
    def routing_mode(self, value: str) -> None:
        """Set the routing strategy. Must be ``"keyword"`` or ``"llm"``."""
        normalized = value.lower().strip()
        if normalized not in {"keyword", "llm"}:
            raise ValueError("routing_mode must be either 'keyword' or 'llm'")
        self._routing_mode = normalized

    @property
    def routing_keywords(self) -> dict[str, tuple[str, ...]]:
        """Return the per-child keyword tuples used for keyword routing."""
        return self._routing_keywords

    @routing_keywords.setter
    def routing_keywords(self, value: dict[str, Iterable[str]]) -> None:
        """Replace the full routing keyword mapping."""
        normalized: dict[str, tuple[str, ...]] = {}
        for child_name, keywords in value.items():
            normalized[child_name] = tuple(keyword.lower() for keyword in keywords)
        self._routing_keywords = normalized

    # ------------------------------------------------------------------
    # Quality evaluation (ReAct toggle)
    # ------------------------------------------------------------------

    @property
    def enable_react_evaluation(self) -> bool:
        """
        Whether the ReAct-style evaluate → refine loop is active.

        When True, each child response is scored and may be refined
        iteratively before the final answer is composed.
        When False, child outputs are accepted as-is (faster, fewer API calls).
        """
        return self._enable_react_evaluation

    @enable_react_evaluation.setter
    def enable_react_evaluation(self, value: bool) -> None:
        """Enable or disable the ReAct evaluation loop."""
        self._enable_react_evaluation = bool(value)

    # ------------------------------------------------------------------
    # Shared defaults and helpers
    # ------------------------------------------------------------------

    def build_tools(self) -> list[BaseTool]:
        return []

    def build_system_prompt(self) -> str:
        return (
            "You are a master orchestration agent. "
            "Decompose requests into execution steps, route them to the right child agents, "
            "and return a combined final answer."
        )

    def _build_execution_plan(self, user_input: str) -> dict[str, object]:
        if self.routing_mode == "llm":
            llm_plan = self._build_llm_execution_plan(user_input)
            if llm_plan is not None:
                return llm_plan

        selected_children = self._select_children_keyword(user_input)
        return self._build_keyword_execution_plan(user_input, selected_children)

    def _build_keyword_execution_plan(
        self,
        user_input: str,
        selected_children: list[str],
    ) -> dict[str, object]:
        steps: list[dict[str, object]] = []
        for index, child_name in enumerate(selected_children, start=1):
            steps.append(
                {
                    "order": index,
                    "child": child_name,
                    "task": user_input,
                    "purpose": "keyword-selected execution",
                    "use_previous_outputs": index > 1,
                }
            )

        plan = {
            "strategy": "keyword",
            "summary": "Keyword-selected execution plan",
            "steps": steps,
        }
        self._last_routing_metadata = {
            "strategy": "keyword",
            "match_type": "keyword_match" if selected_children else "fallback",
            "plan_type": "keyword",
        }
        return plan

    def _build_llm_execution_plan(self, user_input: str) -> dict[str, object] | None:
        if not self._children or self.model is None:
            return None

        child_descriptions = "\n".join(
            f"- {name}: {child.description or 'No description'}"
            for name, child in self._children.items()
        )
        planner_prompt = (
            "You are a planning assistant for a multi-agent system.\n"
            "Decompose the user request into an ordered execution plan.\n"
            "Choose only from available child agents. Use the fewest steps needed.\n"
            "Return only JSON in this exact schema:\n"
            '{"summary": "short summary", "steps": [{"order": 1, "child": "child_name", '
            '"task": "what the child should do", "purpose": "why", "use_previous_outputs": false}]}\n\n'
            f"Available child agents:\n{child_descriptions}\n\n"
            f"User request:\n{user_input}"
        )

        try:
            planner_response = self.model.invoke(planner_prompt)
            raw_content = str(getattr(planner_response, "content", planner_response))
            parsed = self._parse_json_response(raw_content)
            plan = self._validate_execution_plan(parsed)
            if plan is not None:
                self._last_routing_metadata = {
                    "strategy": "llm",
                    "match_type": "llm_plan",
                    "reason": str(parsed.get("summary", "")),
                }
                return plan
        except Exception as exc:
            print(f"LLM planning error: {exc}")

        return None

    def _select_children_keyword(self, user_input: str) -> list[str]:
        lowered = user_input.lower()
        matched: list[str] = []

        for child_name, keywords in self._routing_keywords.items():
            if child_name not in self._children:
                continue
            if any(keyword in lowered for keyword in keywords):
                matched.append(child_name)

        if matched:
            self._last_routing_metadata = {
                "strategy": "keyword",
                "match_type": "keyword_match",
            }
            return matched

        return self._fallback_children()

    def _fallback_children(self) -> list[str]:
        if self.default_child and self.default_child in self._children:
            return [self.default_child]
        if self._children:
            return [next(iter(self._children))]
        raise ValueError("No child agents configured.")

    def _compose_step_input(self, task: str, child_outputs: dict[str, str]) -> str:
        context_lines = ["Prior child outputs:"]
        for step_name, output in child_outputs.items():
            context_lines.append(f"[{step_name}] {output}")
        return f"{task}\n\n" + "\n".join(context_lines)

    def _compose_final_response(
        self,
        user_input: str,
        execution_plan: dict[str, object],
        outputs: dict[str, str],
    ) -> str:
        fallback_response = self._build_structured_fallback_response(
            user_input,
            execution_plan,
            outputs,
        )

        # If no model is available, return the deterministic fallback.
        if self.model is None:
            return fallback_response

        # Build a bounded context window from child outputs to reduce prompt size.
        condensed_outputs: list[str] = []
        total_chars = 0
        max_total_chars = 7000
        max_per_output_chars = 1400

        for step_name, text in outputs.items():
            snippet = text.strip()
            if len(snippet) > max_per_output_chars:
                snippet = snippet[:max_per_output_chars].rstrip() + "..."
            entry = f"[{step_name}] {snippet}"

            if total_chars + len(entry) > max_total_chars:
                break

            condensed_outputs.append(entry)
            total_chars += len(entry)

        synthesis_prompt = (
            "You are a master orchestration assistant.\n"
            "Given the original user request and child-agent outputs, produce a concise,"
            " context-aware final answer in the exact format below.\n"
            "Requirements:\n"
            "- Keep the tone clear, concise, and actionable.\n"
            "- Focus on directly answering the user's request in context.\n"
            "- Merge overlapping points and remove repetition.\n"
            "- Do not mention internal steps, agent names, or evaluation mechanics.\n"
            "- If information is incomplete or conflicting, state that briefly.\n"
            "- Use markdown headers and bullets exactly as shown.\n"
            "- Limit 'Top 5 Key Points' to at most 5 bullets.\n"
            "- Include recommendations only when justified by the evidence.\n"
            "- Include 3-5 concrete next steps.\n\n"
            "Output format:\n"
            "Original Ask:\n"
            "<repeat the user ask in one line>\n\n"
            "Short Answer:\n"
            "<one short paragraph, 3-5 sentences>\n\n"
            "Top 5 Key Points:\n"
            "- <point 1>\n"
            "- <point 2>\n"
            "...\n\n"
            "Recommendations (if any):\n"
            "- <recommendation 1>\n"
            "- <recommendation 2>\n"
            "(If none, write: - None at this time.)\n\n"
            "Suggested Next Steps:\n"
            "1. <step 1>\n"
            "2. <step 2>\n"
            "3. <step 3>\n\n"
            "Proactive Next-Step Question:\n"
            "<ask one specific follow-up question that helps move the task forward>\n\n"
            f"User request:\n{user_input}\n\n"
            f"Plan summary:\n{execution_plan.get('summary', '')}\n\n"
            "Child outputs:\n"
            + "\n".join(condensed_outputs)
        )

        stream_callback = getattr(self, "_stream_callback", None)
        if callable(stream_callback) and hasattr(self.model, "stream"):
            try:
                streamed_chunks: list[str] = []
                for chunk in self.model.stream(synthesis_prompt):
                    text = str(getattr(chunk, "content", ""))
                    if not text:
                        continue
                    streamed_chunks.append(text)
                    stream_callback("token", {"text": text})

                streamed_content = "".join(streamed_chunks).strip()
                if streamed_content:
                    return streamed_content
            except Exception as exc:
                print(f"Final synthesis stream error: {exc}")

        try:
            synthesis_response = self.model.invoke(synthesis_prompt)
            final_content = str(getattr(synthesis_response, "content", synthesis_response)).strip()
            if final_content:
                return final_content
        except Exception as exc:
            print(f"Final synthesis error: {exc}")

        return fallback_response

    def _build_structured_fallback_response(
        self,
        user_input: str,
        execution_plan: dict[str, object],
        outputs: dict[str, str],
    ) -> str:
        """Build a deterministic, structured response when synthesis is unavailable."""
        combined_text = "\n".join(text.strip() for text in outputs.values() if text.strip())
        if not combined_text:
            combined_text = "No child output was produced for this request."

        candidate_points: list[str] = []
        for raw_line in combined_text.splitlines():
            cleaned = raw_line.strip().lstrip("-*").strip()
            if not cleaned:
                continue
            if len(cleaned) > 220:
                cleaned = cleaned[:217].rstrip() + "..."
            if cleaned not in candidate_points:
                candidate_points.append(cleaned)
            if len(candidate_points) >= 10:
                break

        if len(candidate_points) < 10:
            for sentence in re.split(r"(?<=[.!?])\s+", combined_text):
                cleaned = sentence.strip().replace("\n", " ")
                if len(cleaned) < 20:
                    continue
                if len(cleaned) > 220:
                    cleaned = cleaned[:217].rstrip() + "..."
                if cleaned not in candidate_points:
                    candidate_points.append(cleaned)
                if len(candidate_points) >= 10:
                    break

        if not candidate_points:
            candidate_points = ["No material points could be extracted from child outputs."]

        bullets = "\n".join(f"- {point}" for point in candidate_points[:10])
        plan_summary = str(execution_plan.get("summary", "")).strip() or "No plan summary available"
        short_answer = (
            "Based on the available child responses, this answer summarizes the request context and "
            "highlights the most relevant points. "
            f"The plan intent was: {plan_summary}. "
            "Some details may need refinement if additional constraints or data are provided."
        )

        return (
            "Original Ask:\n"
            f"{user_input}\n\n"
            "Short Answer:\n"
            f"{short_answer}\n\n"
            "Top 10 Key Points:\n"
            f"{bullets}\n\n"
            "Recommendations (if any):\n"
            "- Review the top points for gaps and validate assumptions before execution.\n"
            "- Prioritize the first 2-3 actionable items with the highest user impact.\n\n"
            "Suggested Next Steps:\n"
            "1. Confirm which of the top points should be implemented first.\n"
            "2. Share any constraints (timeline, tools, quality bar) that should shape the answer.\n"
            "3. Request a revised response focused on implementation detail if needed.\n\n"
            "Proactive Next-Step Question:\n"
            "Should I now convert the top 3 points into a concrete implementation plan with code-level tasks?"
        )

    def _append_confidence_score(self, final_text: str, quality_score: object) -> str:
        """Append a normalized confidence score line to the final response text."""
        try:
            score = float(quality_score)
        except (TypeError, ValueError):
            score = 0.5

        score = max(0.0, min(1.0, score))
        return f"{final_text.rstrip()}\n\nConfidence score: {score:.2f}"

    def _evaluate_child_response(
        self,
        child_name: str,
        task_input: str,
        response_content: str,
        user_request: str,
    ) -> dict[str, object]:
        if self.model is None:
            return {
                "quality_score": 0.8,
                "needs_refinement": False,
                "feedback": "",
                "criteria": {},
            }

        evaluator_prompt = (
            f"Evaluate this response on: completeness, clarity, relevance, coherence.\n"
            f"Task: {task_input[:300]}\n"
            f"Response: {response_content[:500]}\n"
            f"Return JSON: {{'quality_score': <0-1>, 'needs_refinement': <bool>, "
            f"'feedback': 'suggestions', 'criteria_assessment': {{"
            f"'completeness': <0-1>, 'clarity': <0-1>, 'relevance': <0-1>, 'coherence': <0-1>}}}}"
        )

        try:
            eval_response = self.model.invoke(evaluator_prompt)
            raw_content = str(getattr(eval_response, "content", eval_response))
            parsed = self._parse_json_response(raw_content)

            quality_score = float(parsed.get("quality_score", 0.5))
            needs_refinement = (
                bool(parsed.get("needs_refinement", False))
                or quality_score < self.CONFIDENCE_THRESHOLD
            )

            return {
                "quality_score": quality_score,
                "needs_refinement": needs_refinement,
                "feedback": str(parsed.get("feedback", "")),
                "criteria": parsed.get("criteria_assessment", {}),
            }
        except Exception as exc:
            print(f"Evaluation error: {exc}")
            return {
                "quality_score": 0.5,
                "needs_refinement": True,
                "feedback": f"Evaluation failed: {str(exc)}",
                "criteria": {},
            }

    def _evaluate_final_response(
        self,
        user_input: str,
        final_response: str,
        child_outputs: dict[str, str],
    ) -> dict[str, object]:
        if self.model is None:
            return {
                "quality_score": 0.8,
                "is_coherent": True,
                "assessment": "Model unavailable",
            }

        evaluator_prompt = (
            f"Evaluate if this response integrates all inputs, addresses the request, and is coherent.\n"
            f"Request: {user_input}\n"
            f"Response: {final_response[:500]}\n"
            f"Return JSON: {{'quality_score': <0-1>, 'is_coherent': <bool>, 'assessment': 'text'}}"
        )

        try:
            eval_response = self.model.invoke(evaluator_prompt)
            raw_content = str(getattr(eval_response, "content", eval_response))
            parsed = self._parse_json_response(raw_content)

            return {
                "quality_score": float(parsed.get("quality_score", 0.5)),
                "is_coherent": bool(parsed.get("is_coherent", True)),
                "assessment": str(parsed.get("assessment", "")),
            }
        except Exception as exc:
            print(f"Final evaluation error: {exc}")
            return {
                "quality_score": 0.5,
                "is_coherent": False,
                "assessment": f"Evaluation failed: {str(exc)}",
            }

    def _parse_json_response(self, raw_content: str) -> dict[str, Any]:
        content = raw_content.strip()
        start_idx = content.find("{")
        end_idx = content.rfind("}")
        if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
            return {}

        json_block = content[start_idx : end_idx + 1]
        try:
            parsed = json.loads(json_block)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _validate_execution_plan(self, parsed_plan: object) -> dict[str, object] | None:
        if not isinstance(parsed_plan, dict):
            return None

        steps = parsed_plan.get("steps")
        if not isinstance(steps, list) or not steps:
            return None

        validated_steps: list[dict[str, object]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue

            child_name = step.get("child")
            task = step.get("task")
            if not isinstance(child_name, str) or not isinstance(task, str):
                continue
            if child_name not in self._children:
                continue

            validated_steps.append(
                {
                    "order": int(step.get("order", index) or index),
                    "child": child_name,
                    "task": task.strip(),
                    "purpose": str(step.get("purpose", "")),
                    "use_previous_outputs": bool(step.get("use_previous_outputs", False)),
                }
            )

        if not validated_steps:
            return None

        validated_steps.sort(key=lambda item: int(item.get("order", 0)))
        return {
            "strategy": "llm",
            "summary": str(parsed_plan.get("summary", "LLM-generated execution plan")),
            "steps": validated_steps,
        }

    # ------------------------------------------------------------------
    # FastAPI wrapper
    # ------------------------------------------------------------------

    def build_default_cors_options(self) -> dict[str, object]:
        """
        Build default CORS options for API apps.

        Child implementations can override this to lock down origins,
        methods, headers, or credential behavior.
        """
        return {
            "allow_origins": ["*"],
            "allow_credentials": True,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }

    def register_api_middlewares(self, app: FastAPI) -> None:
        """
        Hook for child classes to register custom FastAPI middleware.

        Default behavior adds a lightweight processing-time header middleware.
        """

        @app.middleware("http")
        async def add_process_time_header(request, call_next):
            import time

            start = time.perf_counter()
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000
            response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
            return response

    def configure_api_app(
        self,
        app: FastAPI,
        *,
        enable_cors: bool = True,
        cors_options: dict[str, object] | None = None,
    ) -> None:
        """
        Configure middleware stack for the generated FastAPI app.

        Child implementations can override this whole method for full control,
        or override build_default_cors_options/register_api_middlewares.
        """
        if enable_cors:
            from fastapi.middleware.cors import CORSMiddleware

            options = cors_options or self.build_default_cors_options()
            app.add_middleware(CORSMiddleware, **options)

        self.register_api_middlewares(app)

    def _prepare_api_request(self, request: OrchestratorAPIRequest) -> None:
        if not self._children:
            self.setup_child_agents()

        if request.reset_history:
            self.reset_history()

        if request.routing_mode is not None:
            self.routing_mode = request.routing_mode

        if request.enable_react_evaluation is not None:
            self.enable_react_evaluation = request.enable_react_evaluation

        if self.chain is None:
            self.initialize()

    def _execute_api_request(self, request: OrchestratorAPIRequest) -> AgentResponse:
        self._prepare_api_request(request)
        return self.run(request.input)

    def _build_api_response(self, response: AgentResponse) -> OrchestratorAPIResponse:
        agent_name = str(response.metadata.get("agent_name", self.name))
        return OrchestratorAPIResponse(
            content=response.content,
            messages=list(response.messages),
            metadata=dict(response.metadata),
            agent_name=agent_name,
        )

    def _format_sse_event(self, event_name: str, payload: Any) -> str:
        encoded_payload = json.dumps(payload, ensure_ascii=False, default=str)
        return f"event: {event_name}\ndata: {encoded_payload}\n\n"

    async def _run_api_request_stream(self, request: OrchestratorAPIRequest):
        from fastapi.responses import StreamingResponse

        event_queue: Queue[tuple[str, Any]] = Queue()
        worker_done = threading.Event()
        stream_state = {"token_emitted": False}

        def stream_callback(event_name: str, payload: Any) -> None:
            if event_name == "token":
                stream_state["token_emitted"] = True
            event_queue.put((event_name, payload))

        def worker() -> None:
            previous_callback = getattr(self, "_stream_callback", None)
            try:
                setattr(self, "_stream_callback", stream_callback)
                response = self._execute_api_request(request)
                api_response = self._build_api_response(response)
                event_queue.put(("final", api_response.model_dump(mode="json")))
            except ValueError as exc:
                event_queue.put(
                    (
                        "error",
                        {
                            "status_code": 400,
                            "detail": str(exc),
                        },
                    )
                )
            except Exception as exc:
                event_queue.put(
                    (
                        "error",
                        {
                            "status_code": 500,
                            "detail": f"Agent execution failed: {exc}",
                        },
                    )
                )
            finally:
                if previous_callback is None:
                    try:
                        delattr(self, "_stream_callback")
                    except AttributeError:
                        pass
                else:
                    setattr(self, "_stream_callback", previous_callback)
                worker_done.set()

        threading.Thread(target=worker, daemon=True).start()

        async def event_generator():
            yield self._format_sse_event(
                "connected",
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

            while True:
                try:
                    event_name, payload = await asyncio.to_thread(event_queue.get, True, 1.0)
                except Empty:
                    if worker_done.is_set():
                        break
                    yield self._format_sse_event(
                        "keepalive",
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    continue

                if event_name == "final":
                    final_payload = payload if isinstance(payload, dict) else {}
                    final_text = str(final_payload.get("content", ""))
                    if not stream_state["token_emitted"]:
                        for token in final_text.split():
                            yield self._format_sse_event("token", {"text": f"{token} "})

                    yield self._format_sse_event("final", final_payload)
                    yield self._format_sse_event("done", {"success": True})
                    break

                if event_name == "error":
                    yield self._format_sse_event("error", payload)
                    yield self._format_sse_event("done", {"success": False})
                    break

                yield self._format_sse_event(event_name, payload)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def _run_api_request(self, request: OrchestratorAPIRequest) -> OrchestratorAPIResponse:
        from fastapi import HTTPException
        from fastapi.concurrency import run_in_threadpool

        try:
            response = await run_in_threadpool(self._execute_api_request, request)
            return self._build_api_response(response)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Agent execution failed: {exc}") from exc

    def build_health_response(self) -> OrchestratorHealthResponse:
        if not self._children:
            self.setup_child_agents()

        return OrchestratorHealthResponse(
            agent_name=self.name,
            initialized=self.chain is not None,
            child_agents=list(self._children.keys()),
            routing_mode=self.routing_mode,
            react_evaluation_enabled=self.enable_react_evaluation,
        )

    def create_api_router(
        self,
        prefix: str = "",
        tags: list[str] | None = None,
        include_health: bool = True,
    ) -> APIRouter:
        from fastapi import APIRouter

        router = APIRouter(prefix=prefix, tags=tags or [self.name])

        @router.post("/run", response_model=OrchestratorAPIResponse)
        async def run_agent(request: OrchestratorAPIRequest) -> OrchestratorAPIResponse:
            return await self._run_api_request(request)

        @router.post("/stream")
        async def stream_agent(request: OrchestratorAPIRequest):
            return await self._run_api_request_stream(request)

        if include_health:

            @router.get("/health", response_model=OrchestratorHealthResponse)
            async def healthcheck() -> OrchestratorHealthResponse:
                return self.build_health_response()

        return router

    def create_api_app(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        version: str = "1.0.0",
        prefix: str = "/api/agent",
        tags: list[str] | None = None,
        include_health: bool = True,
        enable_cors: bool = True,
        cors_options: dict[str, object] | None = None,
    ) -> FastAPI:
        from fastapi import FastAPI

        app = FastAPI(
            title=title or f"{self.name} API",
            description=description or self.description or "Default API wrapper for an orchestrator agent.",
            version=version,
        )
        app.include_router(
            self.create_api_router(
                prefix=prefix,
                tags=tags,
                include_health=include_health,
            )
        )
        self.configure_api_app(
            app,
            enable_cors=enable_cors,
            cors_options=cors_options,
        )
        return app

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @abstractmethod
    def run(self, user_input: str) -> AgentResponse:
        """
        Execute the full orchestration pipeline for a single user turn.

        Implementations must:
        - Select relevant child agents (via routing).
        - Run each child and collect outputs.
        - Optionally evaluate and refine outputs (ReAct loop).
        - Compose and return a final AgentResponse.
        """

