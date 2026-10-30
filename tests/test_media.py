import asyncio
import io
import wave

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app.services import media


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\0\0" * 240)
    return buffer.getvalue()


class SavedChat:
    def __init__(self):
        self.message = {"id": "reply", "role": "assistant", "character_id": "captain", "content": "Hold our course.", "audio_url": None}
        self.character = {"voice_url": "", "voice_transcript": "", "voice": "auto"}

    async def get_message(self, message_id):
        return dict(self.message) if message_id == "reply" else None

    async def get_character(self, character_id):
        return dict(self.character)

    async def set_message_audio(self, message_id, url):
        self.message["audio_url"] = url


@pytest.fixture
async def audio_client(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "MEDIA_DIR", tmp_path)
    app = FastAPI()
    app.include_router(media.router)
    app.mount("/media", StaticFiles(directory=tmp_path))
    app.state.chat = SavedChat()
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        yield client, app.state.chat, tmp_path


async def test_concurrent_playback_requests_share_cached_playable_audio(audio_client, monkeypatch):
    client, chat, _ = audio_client
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def synthesize(client, url, destination, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        destination.write_bytes(wav_bytes())

    monkeypatch.setattr(media, "_synthesize", synthesize)
    first = asyncio.create_task(client.post("/api/messages/reply/audio"))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(client.post("/api/messages/reply/audio"))
    release.set()
    responses = await asyncio.gather(first, second)
    assert all(r.is_success for r in responses)
    assert responses[0].json() == responses[1].json()
    cached = await client.post("/api/messages/reply/audio")
    assert cached.json() == responses[0].json()
    assert calls == 1
    audio = await client.get(cached.json()["audio_url"])
    assert audio.content == wav_bytes()
    assert chat.message["content"] == "Hold our course."


async def test_voice_failure_preserves_text_and_allows_retry(audio_client, monkeypatch):
    client, chat, directory = audio_client

    async def failing(client, url, destination, **kwargs):
        destination.write_bytes(b"incomplete")
        raise HTTPException(502, "Voice server unavailable")

    monkeypatch.setattr(media, "_synthesize", failing)
    assert (await client.post("/api/messages/reply/audio")).status_code == 502
    assert chat.message["content"] == "Hold our course."
    assert chat.message["audio_url"] is None
    assert list(directory.iterdir()) == []

    async def recovered(client, url, destination, **kwargs):
        destination.write_bytes(wav_bytes())

    monkeypatch.setattr(media, "_synthesize", recovered)
    response = await client.post("/api/messages/reply/audio")
    assert response.is_success
    assert (await client.get(response.json()["audio_url"])).content == wav_bytes()


async def test_uploads_reject_executable_content_oversize_and_path_escape(audio_client):
    client, chat, directory = audio_client
    svg = await client.post("/api/uploads/icon", files={"file": ("portrait.png", b'<svg onload="alert(1)"></svg>', "image/png")})
    assert svg.status_code == 415
    oversized = await client.post("/api/uploads/icon", content=b"x" * (5 * 1024 * 1024 + 65537), headers={"content-type": "multipart/form-data; boundary=test"})
    assert oversized.status_code == 413
    chat.character.update(voice_url="/media/../../private.wav", voice_transcript="Private material")
    assert (await client.post("/api/messages/reply/audio")).status_code == 400
    assert list(directory.iterdir()) == []
