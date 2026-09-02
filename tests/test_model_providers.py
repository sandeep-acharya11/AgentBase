from unittest.mock import patch

import pytest

from BasePackage import AgentConfig, BaseAgent


class ModelAgent(BaseAgent):
    def build_tools(self) -> list:
        return []

    def build_system_prompt(self) -> str:
        return "Test system prompt."


def test_default_model_uses_ollama_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("AGENT_MODEL_NAME", "llama3.2:3b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OLLAMA_API_KEY", "local-key")
    agent = ModelAgent(AgentConfig(name="test-agent"))

    with patch("BasePackage.BaseAgent.ChatOpenAI") as chat_openai:
        model = agent.default_model()

    assert model is chat_openai.return_value
    chat_openai.assert_called_once_with(
        model="llama3.2:3b",
        temperature=0.0,
        max_tokens=512,
        base_url="http://localhost:11434/v1",
        api_key="local-key",
    )


def test_default_model_uses_ollama_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    agent = ModelAgent(AgentConfig(name="test-agent", model_name="qwen3:4b"))

    with patch("BasePackage.BaseAgent.ChatOpenAI") as chat_openai:
        agent.default_model()

    assert chat_openai.call_args.kwargs["base_url"] == "http://127.0.0.1:11434/v1"
    assert chat_openai.call_args.kwargs["api_key"] == "ollama"


def test_default_model_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "unsupported")
    agent = ModelAgent(AgentConfig(name="test-agent"))

    with pytest.raises(ValueError, match="Unsupported model provider"):
        agent.default_model()