import httpx
import pytest

from app.adapters.openai_compatible import OpenAICompatibleAdapter


@pytest.mark.parametrize("endpoint", ["http://model.local", "http://model.local/v1/"])
async def test_local_endpoint_discovery_and_spoken_response(endpoint, monkeypatch):
    def upstream(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "local-model"}]})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Hold position.", "reasoning_content": "Private deliberation"}}]})
        return httpx.Response(404)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs))
    result = await OpenAICompatibleAdapter().send_message({"id": "captain", "name": "Captain", "endpoint": endpoint}, [], "Your orders?", {})
    assert result == {"status": "ok", "content": "Hold position."}


async def test_token_exhaustion_does_not_publish_reasoning_as_dialogue(monkeypatch):
    def upstream(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "We need to respond as the captain. First consider..."}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs))
    result = await OpenAICompatibleAdapter().send_message({"id": "captain", "name": "Captain", "endpoint": "http://model.local/v1", "model": "local-model"}, [], "Your orders?", {})
    assert result["status"] == "error"
    assert "content" not in result
    assert "token limit" in result["error"]
