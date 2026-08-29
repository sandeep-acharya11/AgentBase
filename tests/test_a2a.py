from fastapi import FastAPI
from fastapi.testclient import TestClient

from BasePackage import (
    A2AAgentConfig,
    A2AAgentSkill,
    AgentConfig,
    AgentResponse,
    BaseAgent,
    mount_a2a_routes,
)


class EchoAgent(BaseAgent):
    def build_tools(self) -> list:
        return []

    def build_system_prompt(self) -> str:
        return "Return supplied text."

    def run(self, user_input: str) -> AgentResponse:
        return AgentResponse(content=f"Echo: {user_input}")


def test_mount_a2a_routes_serves_card_and_completes_task() -> None:
    app = FastAPI()
    mount_a2a_routes(
        app,
        EchoAgent(AgentConfig(name="echo-agent", description="Returns supplied text.")),
        A2AAgentConfig(
            public_url="http://testserver",
            skills=[
                A2AAgentSkill(
                    id="echo",
                    name="Echo text",
                    description="Returns the supplied text.",
                )
            ],
        ),
    )
    client = TestClient(app)

    card_response = client.get("/.well-known/agent-card.json")

    assert card_response.status_code == 200
    card = card_response.json()
    assert card["name"] == "echo-agent"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["supportedInterfaces"][0]["url"] == "http://testserver/a2a"
    assert card["skills"][0]["id"] == "echo"

    response = client.post(
        "/a2a",
        headers={"A2A-Version": "1.0"},
        json={
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                }
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "request-1"
    task = payload["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "Echo: hello"


def test_orchestrator_create_api_app_includes_a2a_by_default() -> None:
    from BasePackage.MasterAgent import MasterAgent

    class DummyMasterAgent(MasterAgent):
        def setup_child_agents(self) -> None:
            pass

    agent = DummyMasterAgent(AgentConfig(name="test-master", description="Test master agent"))
    app = agent.create_api_app(prefix="/api/test")
    client = TestClient(app)

    card_response = client.get("/.well-known/agent-card.json")
    assert card_response.status_code == 200
    card = card_response.json()
    assert card["name"] == "test-master"
    assert card["supportedInterfaces"][0]["url"] == "http://127.0.0.1:8000/a2a"

    health_response = client.get("/api/test/health")
    assert health_response.status_code == 200