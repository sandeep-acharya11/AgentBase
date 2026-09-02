# AgentBase / BasePackage

`BasePackage` is a reusable agent framework providing `BaseAgent`, `MasterAgent`, `MasterAgentLanggraph`, an `OrchestratorMixin`, and an MCP (Model Context Protocol) client — building blocks for creating single- or multi-agent LangChain/LangGraph applications.

This README explains how to install `BasePackage` **as a dependency in another project directly from this Git repository**.

## Repository

```
https://github.com/sandeep-acharya11/AgentBase.git
```

## Requirements

- Python >= 3.10
- `pip` (or `uv` / `poetry`, examples below)
- Git

## Installation

### 1. Install directly with `pip` (recommended)

From your target project's virtual environment, install straight from GitHub:

```powershell
# Latest commit on the default branch
pip install "git+https://github.com/sandeep-acharya11/AgentBase.git"
```

Install a specific branch, tag, or commit by appending `@<ref>`:

```powershell
# Specific branch
pip install "git+https://github.com/sandeep-acharya11/AgentBase.git@main"

# Specific tag
pip install "git+https://github.com/sandeep-acharya11/AgentBase.git@v0.1.0"

# Specific commit SHA
pip install "git+https://github.com/sandeep-acharya11/AgentBase.git@<commit-sha>"
```

Since the package sources live under `src/`, no extra subdirectory flag is needed — the `pyproject.toml` at the repo root already declares `package-dir = {"" = "src"}`.

### 2. Add as a dependency in `pyproject.toml`

If your project also uses `pyproject.toml` (PEP 621 / setuptools, Poetry, or `uv`):

```toml
[project]
dependencies = [
    "BasePackage @ git+https://github.com/sandeep-acharya11/AgentBase.git@main",
]
```

**Poetry:**

```toml
[tool.poetry.dependencies]
BasePackage = { git = "https://github.com/sandeep-acharya11/AgentBase.git", branch = "main" }
```

```powershell
poetry add git+https://github.com/sandeep-acharya11/AgentBase.git#main
```

**uv:**

```powershell
uv add "git+https://github.com/sandeep-acharya11/AgentBase.git"
```

### 3. Add to `requirements.txt`

```text
git+https://github.com/sandeep-acharya11/AgentBase.git@main#egg=BasePackage
```

Then install with:

```powershell
pip install -r requirements.txt
```

### 4. Editable / local development install (clone first)

Useful when you want to modify `BasePackage` alongside your consuming project:

```powershell
git clone https://github.com/sandeep-acharya11/AgentBase.git
cd AgentBase
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

On Linux/macOS:

```bash
git clone https://github.com/sandeep-acharya11/AgentBase.git
cd AgentBase
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 5. Verify the installation

```powershell
python -c "import BasePackage; print(BasePackage.__all__)"
```

Expected output:

```text
['AgentConfig', 'AgentMessage', 'AgentResponse', 'BaseAgent', 'MasterAgent']
```

## Quick Start

Once installed, import and use the framework in your project:

```python
from BasePackage import AgentConfig, BaseAgent, MasterAgent
from langchain_core.tools import BaseTool


class MyChildAgent(BaseAgent):
    def build_tools(self) -> list[BaseTool]:
        return []

    def build_system_prompt(self) -> str:
        return "You are a helpful specialist agent."


config = AgentConfig(name="my-agent", description="A demo agent")
agent = MyChildAgent(config)

response = agent.run("Hello, agent!")
print(response)
```

Building a multi-agent orchestrator with `MasterAgent`:

```python
from BasePackage import AgentConfig, BaseAgent, MasterAgent
from langchain_core.tools import BaseTool


class PlanningChildAgent(BaseAgent):
    def build_tools(self) -> list[BaseTool]:
        return []

    def build_system_prompt(self) -> str:
        return "You are a planning specialist. Break tasks into ordered steps."


class ReasoningChildAgent(BaseAgent):
    def build_tools(self) -> list[BaseTool]:
        return []

    def build_system_prompt(self) -> str:
        return "You are a reasoning specialist. Explain tradeoffs and conclusions clearly."


master_config = AgentConfig(
    name="master-agent",
    description="Master agent orchestrating planner and reasoner child agents",
)
master_agent = MasterAgent(master_config)

planner_agent = PlanningChildAgent(
    AgentConfig(
        name="planning-child-agent",
        description="Child agent for decomposition and planning",
    )
)
reasoner_agent = ReasoningChildAgent(
    AgentConfig(
        name="reasoning-child-agent",
        description="Child agent for reasoning and explanations",
    )
)

master_agent.add_child("planner", planner_agent, keywords=["plan", "steps", "roadmap"])
master_agent.add_child("reasoner", reasoner_agent, keywords=["why", "explain", "reason"], set_default=True)

result = master_agent.run("Plan and reason through this task")
```

For a full runnable example, see [`src/BasePackage/MultiAgentMain.py`](src/BasePackage/MultiAgentMain.py), which wires up `PlanningChildAgent` and `ReasoningChildAgent` under a `MasterAgent` and can be run via:

```powershell
.venv\Scripts\python.exe -m BasePackage.MultiAgentMain --routing-mode keyword
```

## Environment Configuration

`BasePackage` loads `.env` during agent initialization. Select the model provider and
model with `AGENT_MODEL_PROVIDER` and `AGENT_MODEL_NAME`; values passed in
`AgentConfig` remain the fallback defaults.

### OpenAI

For OpenAI (the default), add the following to `.env` or set the value in your shell:

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

### Ollama (local)

Ollama is supported through its OpenAI-compatible API, so no additional Python SDK is
needed. Follow these steps to run the included multi-agent example against a local
model:

1. Install [Ollama](https://ollama.com/) and make sure its server is running. When
    using the command-line server directly, run `ollama serve` in a separate terminal.
2. Pull a chat model:

    ```powershell
    ollama pull llama3.2:3b
    ```

3. Copy the environment template and configure the local model:

    ```powershell
    Copy-Item .env.example .env
    ```

    Update `.env` with:

    ```dotenv
    AGENT_MODEL_PROVIDER=ollama
    AGENT_MODEL_NAME=llama3.2:3b
    ```

4. Run the included example:

    ```powershell
    .venv\Scripts\python.exe -m BasePackage.MultiAgentMain --routing-mode keyword
    ```

    Enter a request such as `Explain the tradeoffs between PostgreSQL and SQLite`, then
    type `exit` to end the interactive session.

`OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434/v1` and `OLLAMA_API_KEY`
defaults to `ollama`, so only the provider and model are required for a standard local
Ollama installation. Use these variables in `.env` to override the defaults or connect
to a hosted Ollama-compatible endpoint:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
# Required by the OpenAI-compatible client; not validated by local Ollama.
OLLAMA_API_KEY=ollama
```

The default model factory supports `openai` and `ollama`. For another provider, override
`BaseAgent.default_model()` with a compatible LangChain runnable.

## Dependencies

`BasePackage` installs the following core dependencies automatically:

- `pydantic>=2.0`
- `python-dotenv>=1.0`
- `langchain-core>=0.3`
- `langchain-openai>=0.2`
- `langgraph>=0.2`
- `typing_extensions>=4.9`
- `mcp>=1.0`

## Documentation

Detailed design and usage documentation for each module lives alongside the source in [`src/BasePackage/`](src/BasePackage/):

| Document | Description |
|---|---|
| [BaseAgent.md](src/BasePackage/BaseAgent.md) | Full reference for the `BaseAgent` class: config/message/response data models, lifecycle (`initialize`, `shutdown`), model/prompt/chain construction, memory, and tool wrapping. |
| [MasterAgent.md](src/BasePackage/MasterAgent.md) | `MasterAgent` sequential orchestration: entrypoints, sequential execution paths, refinement internals, routing helpers, and sample orchestration code. |
| [MasterAgentLanggraph.md](src/BasePackage/MasterAgentLanggraph.md) | Graph-based orchestration built on LangGraph: graph workflow, node responsibilities, streaming endpoint behavior, and stream event payloads. |
| [OrchestratorMixin.md](src/BasePackage/OrchestratorMixin.md) | Shared orchestration contract/mixin used by both `MasterAgent` and `MasterAgentLanggraph`: child registry/routing, shared response composition, and evaluation helpers. |
| [MCP.md](src/BasePackage/MCP.md) | `BaseMCPClient`/`DefaultMCPClient` documentation: MCP server configuration, connecting, listing tools, and calling tools. |
| [ClassReference.md](src/BasePackage/ClassReference.md) | High-level class reference and class diagram spanning `BaseAgent`, `OrchestratorMixin`, and `MasterAgent`. |
| [REACT_TOGGLE_GUIDE.md](src/BasePackage/REACT_TOGGLE_GUIDE.md) | Guide for enabling/disabling ReAct evaluation, with behavior and metadata comparisons. |

## Uninstalling

```powershell
pip uninstall BasePackage
```
