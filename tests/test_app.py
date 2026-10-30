"""Behavioral regressions for persistent, bounded character conversations."""
import asyncio
import sqlite3

import httpx
import pytest

from main import app


class ConversationModel:
    """A controlled model boundary; never contacts a user's model server."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.block_room = None
        self.fail = False

    async def send_message(self, character, history, directed, options):
        if options["room"]["id"] == self.block_room:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        if self.fail:
            return {"status": "error", "error": "Model service unavailable"}
        previous = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "opening")
        question = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        return {"status": "ok", "content": f"{character['name']} on {question}; following [{previous}]"}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_DIALOG_DB", str(tmp_path / "chat.sqlite"))
    async with app.router.lifespan_context(app):
        model = ConversationModel()
        app.state.adapter_registry["openai_compatible"] = model
        async with httpx.AsyncClient(app=app, base_url="http://test") as http:
            yield http, model


async def create_cast(http):
    cast = []
    for name in ("Navigator", "Captain"):
        response = await http.post("/api/characters", json={"name": name, "persona": f"You are the {name.lower()} of a starship."})
        assert response.is_success, response.text
        cast.append(response.json())
    return cast


async def create_room(http, cast, **changes):
    data = {"title": "The bridge", "description": "Investigate a signal", "kind": "group", "character_ids": [c["id"] for c in cast], "turn_limit": 3}
    data.update(changes)
    response = await http.post("/api/rooms", json=data)
    assert response.is_success, response.text
    return response.json()


async def settle(http, response):
    assert response.is_success, response.text
    run_id = response.json()["run_id"]
    for _ in range(200):
        status = (await http.get(f"/api/runs/{run_id}/status")).json()
        if status["status"] != "running":
            return status
        await asyncio.sleep(0.01)
    pytest.fail("Conversation did not finish")


async def messages(http, room):
    response = await http.get(f"/api/rooms/{room['id']}/messages")
    assert response.is_success, response.text
    return response.json()


async def test_reply_budget_is_total_and_later_turns_receive_conversation(client):
    http, _ = client
    cast = await create_cast(http)
    room = await create_room(http, cast)
    status = await settle(http, await http.post(f"/api/rooms/{room['id']}/send", json={"content": "Identify the signal"}))
    assert status["status"] == "completed"
    assert status["completed_turns"] == 3
    history = await messages(http, room)
    replies = [m for m in history if m["role"] == "assistant"]
    assert [m["character_id"] for m in replies] == [cast[0]["id"], cast[1]["id"], cast[0]["id"]]
    assert replies[0]["content"] in replies[1]["content"]
    assert replies[1]["content"] in replies[2]["content"]
    await settle(http, await http.post(f"/api/rooms/{room['id']}/send", json={"content": "And the risk?", "turn_limit": 1}))
    followup = (await messages(http, room))[-1]
    assert "And the risk?" in followup["content"]
    assert replies[-1]["content"] in followup["content"]


async def test_stop_cancels_inflight_and_rejects_overlapping_room_changes(client):
    http, model = client
    cast = await create_cast(http)
    room = await create_room(http, cast)
    model.block_room = room["id"]
    first = await http.post(f"/api/rooms/{room['id']}/send", json={"content": "Hold position"})
    assert first.is_success
    await asyncio.wait_for(model.entered.wait(), 2)
    assert [m["content"] for m in await messages(http, room)] == ["Hold position"]
    overlap = await http.post(f"/api/rooms/{room['id']}/send", json={"content": "Must not persist"})
    assert overlap.status_code == 409
    edit = await http.put(f"/api/rooms/{room['id']}", json={"title": "Unsafe edit"})
    assert edit.status_code == 409
    assert (await http.post(f"/api/rooms/{room['id']}/stop")).is_success
    await asyncio.wait_for(model.cancelled.wait(), 2)
    assert (await settle(http, first))["status"] == "stopped"
    assert [m["content"] for m in await messages(http, room)] == ["Hold position"]
    model.block_room = None
    resumed = await settle(http, await http.post(f"/api/rooms/{room['id']}/continue", json={"turn_limit": 1}))
    assert resumed["completed_turns"] == 1


async def test_rooms_run_independently_without_history_leaks(client):
    http, model = client
    cast = await create_cast(http)
    first = await create_room(http, cast, title="Secret bridge")
    second = await create_room(http, cast, title="Public lounge", turn_limit=1)
    model.block_room = first["id"]
    await http.post(f"/api/rooms/{first['id']}/send", json={"content": "Classified coordinates"})
    await asyncio.wait_for(model.entered.wait(), 2)
    status = await settle(http, await http.post(f"/api/rooms/{second['id']}/send", json={"content": "Tea, please"}))
    assert status["status"] == "completed"
    assert all("Classified" not in m["content"] for m in await messages(http, second))
    active = next(r for r in (await http.get("/api/rooms")).json() if r["id"] == first["id"])
    assert active["active_run"]["status"] == "running"
    await http.post(f"/api/rooms/{first['id']}/stop")


async def test_direct_membership_and_invalid_budget_do_not_persist_messages(client):
    http, _ = client
    cast = await create_cast(http)
    bad_direct = await http.post("/api/rooms", json={"title": "Private", "kind": "direct", "character_ids": [c["id"] for c in cast]})
    assert bad_direct.status_code in (400, 422)
    room = await create_room(http, cast[:1], kind="direct", turn_limit=1)
    for body in ({"content": "No", "turn_limit": 25}, {"content": "No", "turn_limit": 0}, {"content": "No", "target_character_id": cast[1]["id"]}, {"content": "   "}):
        rejected = await http.post(f"/api/rooms/{room['id']}/send", json=body)
        assert rejected.status_code in (400, 422)
    assert await messages(http, room) == []
    await settle(http, await http.post(f"/api/rooms/{room['id']}/send", json={"content": "Just between us"}))
    assert [m["character_id"] for m in await messages(http, room) if m["role"] == "assistant"] == [cast[0]["id"]]


async def test_targeted_reply_and_deleted_character_preserve_attribution(client):
    http, _ = client
    cast = await create_cast(http)
    room = await create_room(http, cast)
    await settle(http, await http.post(f"/api/rooms/{room['id']}/send", json={"content": "Captain only", "target_character_id": cast[1]["id"], "turn_limit": 2}))
    before = [m for m in await messages(http, room) if m["role"] == "assistant"]
    assert [m["character_id"] for m in before] == [cast[1]["id"], cast[1]["id"]]
    assert (await http.delete(f"/api/characters/{cast[1]['id']}")).is_success
    after = [m for m in await messages(http, room) if m["role"] == "assistant"]
    assert [(m["speaker_name"], m["content"]) for m in after] == [(m["speaker_name"], m["content"]) for m in before]
    saved = next(r for r in (await http.get("/api/rooms")).json() if r["id"] == room["id"])
    assert cast[1]["id"] not in saved["character_ids"]


async def test_failed_model_keeps_user_message_and_releases_room(client):
    http, model = client
    room = await create_room(http, await create_cast(http))
    model.fail = True
    failed = await settle(http, await http.post(f"/api/rooms/{room['id']}/send", json={"content": "Are you there?"}))
    assert failed["status"] == "error"
    assert failed["error"]
    history = await messages(http, room)
    assert history[0]["content"] == "Are you there?"
    assert not any(m["role"] == "assistant" for m in history)
    model.fail = False
    resumed = await settle(http, await http.post(f"/api/rooms/{room['id']}/continue", json={"turn_limit": 1}))
    assert resumed["status"] == "completed"


async def test_api_keys_are_not_disclosed_and_blank_edit_preserves_secret(client):
    http, _ = client
    response = await http.post("/api/characters", json={"name": "Keeper", "persona": "Guard the archive", "api_key": "private-test-secret"})
    assert response.is_success, response.text
    character = response.json()
    assert "private-test-secret" not in response.text
    updated = await http.put(f"/api/characters/{character['id']}", json={"name": "Archive keeper", "api_key": ""})
    assert updated.is_success
    assert "private-test-secret" not in (await http.get("/api/characters")).text
    stored = await app.state.chat.get_character(character["id"])
    assert stored["api_key"] == "private-test-secret"


async def test_legacy_history_migrates_once_and_survives_restart(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE participants (id TEXT PRIMARY KEY, name TEXT NOT NULL, handle TEXT NOT NULL UNIQUE,
                provider TEXT DEFAULT 'openai_compatible', endpoint_base_url TEXT DEFAULT '', model TEXT DEFAULT '',
                api_key TEXT DEFAULT '', system_prompt TEXT DEFAULT '', temperature REAL DEFAULT .7, max_tokens INTEGER DEFAULT 512,
                muted INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, color TEXT DEFAULT '#6366f1', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE sessions (id TEXT PRIMARY KEY,title TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE messages (id TEXT PRIMARY KEY,session_id TEXT NOT NULL,sender_type TEXT NOT NULL,sender_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,content TEXT NOT NULL,directed_to TEXT,created_at TEXT NOT NULL,status TEXT DEFAULT 'complete',metadata TEXT);
            INSERT INTO participants (id,name,handle,system_prompt,created_at,updated_at) VALUES ('legacy-person','Old captain','old-captain','Remember the voyage','2026-01-01','2026-01-01');
            INSERT INTO sessions VALUES ('legacy-room','Saved voyage','2026-01-01','2026-01-01');
            INSERT INTO messages (id,session_id,sender_type,sender_id,sender_name,content,created_at) VALUES ('legacy-message','legacy-room','llm','legacy-person','Old captain','Our voyage continues.','2026-01-01');
        """)
    monkeypatch.setenv("LLM_DIALOG_DB", str(path))
    snapshots = []
    for _ in range(2):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(app=app, base_url="http://test") as http:
                rooms = (await http.get("/api/rooms")).json()
                characters = (await http.get("/api/characters")).json()
                history = (await http.get("/api/rooms/legacy-room/messages")).json()
                assert next(c for c in characters if c["id"] == "legacy-person")["persona"] == "Remember the voyage"
                assert [(m["speaker_name"], m["content"]) for m in history] == [("Old captain", "Our voyage continues.")]
                assert next(r for r in rooms if r["id"] == "legacy-room")["title"] == "Saved voyage"
                snapshots.append(({r["id"] for r in rooms}, {c["id"] for c in characters}))
    assert snapshots[0] == snapshots[1]
