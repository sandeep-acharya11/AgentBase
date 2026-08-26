from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Ensure project root is importable when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from BasePackage.AgentConfig import AgentConfig
from BasePackage.BaseAgent import BaseAgent
from BasePackage.MasterAgent import MasterAgent
from langchain_core.tools import BaseTool

class PlanningChildAgent(BaseAgent):
    """Child agent specialized in planning and decomposition."""

    def build_tools(self) -> list[BaseTool]:
        return []

    def build_system_prompt(self) -> str:
        return (
            "You are a planning specialist. "
            "Break tasks into concise ordered steps and highlight assumptions."
        )

class ReasoningChildAgent(BaseAgent):
    """Child agent specialized in reasoning and explanation."""

    def build_tools(self) -> list[BaseTool]:
        return []

    def build_system_prompt(self) -> str:
        return (
            "You are a reasoning specialist. "
            "Provide clear explanations with compact, actionable conclusions."
        )

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the multi-agent demo")
    parser.add_argument(
        "--routing-mode",
        choices=["keyword", "llm"],
        default="keyword",
        help="Select keyword routing or LLM routing",
    )
    args = parser.parse_args()

    master_config = AgentConfig(
        name="master-agent",
        description="Master agent orchestrating planner and reasoner child agents",
    )
    masterAgent = MasterAgent(master_config)

    planner_agent = PlanningChildAgent(
        AgentConfig(
            name="planning-child-agent",
            description="Child agent for decomposition and planning",
            model_provider=master_config.model_provider,
            model_name=master_config.model_name,
            temperature=master_config.temperature,
            max_tokens=master_config.max_tokens,
            tags=["child", "planner"],
        )
    )
    reasoner_agent = ReasoningChildAgent(
        AgentConfig(
            name="reasoning-child-agent",
            description="Child agent for reasoning and explanations",
            model_provider=master_config.model_provider,
            model_name=master_config.model_name,
            temperature=master_config.temperature,
            max_tokens=master_config.max_tokens,
            tags=["child", "reasoner"],
        )
    )

    masterAgent.routing_mode = args.routing_mode
    masterAgent.children = {
        "planner": planner_agent,
        "reasoner": reasoner_agent,
    }
    masterAgent.routing_keywords = {
        "planner": [
            "plan",
            "steps",
            "roadmap",
            "break down",
            "decompose",
            "strategy",
            "prepare",
        ],
        "reasoner": [
            "why",
            "explain",
            "reason",
            "compare",
            "tradeoff",
            "pros and cons",
        ],
    }
    masterAgent.default_child = "reasoner"

    masterAgent.initialize()

    print(f"Routing mode: {masterAgent.routing_mode}")

    print("MasterAgent ready. Type 'exit' to quit.")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            print("Session ended.")
            break
        if not user_input:
            continue

        response = masterAgent.run(user_input)
        print(f"Assistant: {response.content}\n")
        selected = response.metadata.get("orchestration", {}).get("selected_children", [])
        print(f"Selected child agents: {selected}\n")

if __name__ == "__main__":
    main()
