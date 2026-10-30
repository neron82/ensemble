import asyncio
import re

import httpx

from app.adapters.base import LLMAdapter


def api_base(endpoint):
    base = endpoint.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


class OpenAICompatibleAdapter(LLMAdapter):
    TIMEOUT = 180

    async def send_message(self, participant, conversation_context, directed_user_message, options):
        headers = {}
        if participant.get("api_key"):
            headers["Authorization"] = f"Bearer {participant['api_key']}"
        room = options.get("room", {})
        cast = options.get("characters", [participant])
        system = (
            f"You are {participant['name']}, a character in a lively, immersive conversation.\n"
            f"Your persona:\n{participant.get('persona', '')}\n\n"
            f"Room: {room.get('title', 'Private conversation')}\nScene: {room.get('description', '')}\n"
            f"Present: the human guest, {', '.join(c['name'] for c in cast)}.\n"
            "Stay in your persona. Respond to the guest and build on the specific ideas of the other speakers. "
            "Disagree, show curiosity, use wit and emotion when fitting; do not turn every conversation into a work plan. "
            "Speak ONLY as yourself, not as the other characters or the guest. Do not prefix your reply with your name. "
            "Keep your contribution to one or two engaging paragraphs unless the guest asks for more. "
            "Do not repeat previous replies or announce whose turn is next. The host controls turn-taking. "
            "Only write your spoken contribution, without private reasoning."
        )
        messages = [{"role": "system", "content": system}]
        for message in conversation_context:
            if message["role"] == "error":
                continue
            own = message["role"] == "assistant" and message.get("character_id") == participant["id"]
            messages.append({"role": "assistant" if own else "user", "content": message["content"] if own else f"{message['speaker_name']}: {message['content']}"})
        if directed_user_message:
            messages.append({"role": "user", "content": directed_user_message})
        if len(messages) == 1:
            messages.append({"role": "user", "content": "Greet your guest briefly in character."})
        try:
            base = api_base(participant["endpoint"])
            async with asyncio.timeout(self.TIMEOUT):
                async with httpx.AsyncClient(timeout=self.TIMEOUT, trust_env=False) as client:
                    model = participant.get("model")
                    if not model:
                        response = await client.get(base + "/models", headers=headers)
                        response.raise_for_status()
                        models = response.json().get("data", [])
                        model = next((m["id"] for m in models if isinstance(m, dict) and m.get("id")), None)
                        if not model:
                            raise ValueError("No model is loaded at this endpoint")
                    response = await client.post(base + "/chat/completions", headers=headers, json={
                        "model": model, "messages": messages,
                        "temperature": participant.get("temperature", 0.85),
                        "max_tokens": participant.get("max_tokens", 2048), "stream": False,
                        "chat_template_kwargs": {"enable_thinking": False},
                    })
                    response.raise_for_status()
                    choice = response.json()["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise ValueError("The model exhausted its output token limit before finishing. Increase Maximum output tokens in this character's Advanced settings.")
                    content = choice["message"]["content"]
                    if not isinstance(content, str):
                        raise ValueError("The model returned no text. Increase the character's token limit if its reasoning exhausted the budget.")
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                    if not content or "<think>" in content:
                        raise ValueError("The model returned no spoken reply. Increase the character's token limit if reasoning exhausted the budget.")
                    return {"status": "ok", "content": content}
        except (httpx.TimeoutException, TimeoutError):
            return {"status": "error", "error": f"{participant['name']}'s model timed out after {self.TIMEOUT} seconds"}
        except httpx.HTTPStatusError as exc:
            return {"status": "error", "error": f"{participant['name']}'s model returned HTTP {exc.response.status_code}: {exc.response.text[:400]}"}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            return {"status": "error", "error": f"Unable to get a reply from {participant['name']}: {str(exc)[:400]}"}

    async def validate_config(self, participant):
        return {"valid": bool(participant.get("endpoint")), "errors": [] if participant.get("endpoint") else ["Endpoint is required"]}

    async def test_connection(self, participant):
        return await self.send_message(participant, [], "Greet me in one short sentence.", {})
