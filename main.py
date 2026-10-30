"""Ensemble: local character conversations, served on loopback by default."""
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.models.db import close_db, init_db
from app.models.message import MessageCRUD
from app.models.participant import CharacterCRUD
from app.models.schemas import CharacterCreate, CharacterUpdate, ContinueRequest, RoomCreate, SendRequest
from app.models.session import RoomCRUD
from app.services.media import MEDIA_DIR, router as media_router, service_status
from app.services.orchestrator import ChatError, Orchestrator

logging.basicConfig(level=logging.INFO)
ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await init_db()
    characters, messages, rooms = CharacterCRUD(db), MessageCRUD(db), RoomCRUD(db)
    registry = {"openai_compatible": OpenAICompatibleAdapter()}
    chat = Orchestrator(characters, messages, rooms, registry)
    app.state.chat = app.state.orchestrator = chat
    app.state.adapter_registry = registry
    try:
        yield
    finally:
        await chat.close()
        await close_db(db)


app = FastAPI(title="Ensemble", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.include_router(media_router)


@app.exception_handler(ChatError)
async def chat_error(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default errors echo submitted values, including API keys.
    return JSONResponse(status_code=422, content={"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]})


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/services")
async def services():
    return await service_status()


async def require_character(chat, character_id):
    character = await chat.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    return character


def validate_character_media(data):
    if data.get("voice_url") and not data.get("voice_transcript", "").strip():
        raise HTTPException(422, "A voice reference needs its exact transcript")
    for field in ("voice_url", "icon_url"):
        url = data.get(field, "")
        if url.startswith("/media/"):
            path = MEDIA_DIR / url.removeprefix("/media/")
            if path.is_symlink() or path.resolve().parent != MEDIA_DIR or not path.is_file():
                raise HTTPException(422, "Upload the media file before saving this character")


@app.get("/api/characters")
async def list_characters(request: Request):
    chat = request.app.state.chat
    return [chat.characters_db.public(c) for c in await chat.characters_db.list_all()]


@app.post("/api/characters", status_code=201)
async def create_character(data: CharacterCreate, request: Request):
    chat = request.app.state.chat
    values = data.model_dump()
    validate_character_media(values)
    async with chat.lock:
        try:
            character = await chat.characters_db.create(values)
        except sqlite3.IntegrityError as exc:
            await chat.characters_db.db.rollback()
            raise HTTPException(409, "That character handle is already in use") from exc
    return chat.characters_db.public(character)


@app.put("/api/characters/{character_id}")
async def update_character(character_id: str, data: CharacterUpdate, request: Request):
    chat = request.app.state.chat
    async with chat.lock:
        existing = await require_character(chat, character_id)
        chat.ensure_character_idle(character_id)
        changes = data.model_dump(exclude_unset=True)
        validate_character_media({**existing, **changes})
        try:
            character = await chat.characters_db.update(character_id, changes)
        except sqlite3.IntegrityError as exc:
            await chat.characters_db.db.rollback()
            raise HTTPException(409, "That character handle is already in use") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return chat.characters_db.public(character)


@app.delete("/api/characters/{character_id}")
async def delete_character(character_id: str, request: Request):
    chat = request.app.state.chat
    async with chat.lock:
        await require_character(chat, character_id)
        chat.ensure_character_idle(character_id)
        await chat.characters_db.delete(character_id)
    return {"status": "deleted"}


@app.post("/api/characters/{character_id}/test")
async def test_character(character_id: str, request: Request):
    chat = request.app.state.chat
    character = await require_character(chat, character_id)
    result = await chat.adapter_registry[character["provider"]].test_connection(character)
    error = result.get("error")
    if error and character.get("api_key"):
        error = error.replace(character["api_key"], "[redacted]")
    return {"ok": result.get("status") == "ok", **({"error": error} if error else {})}


def room_result(chat, room):
    return {**room, "active_run": chat.active_run(room["id"])}


async def validate_members(chat, data):
    for member in data["character_ids"]:
        if await chat.get_character(member) is None:
            raise HTTPException(422, "One of the selected characters no longer exists")


@app.get("/api/rooms")
async def list_rooms(request: Request):
    chat = request.app.state.chat
    async with chat.lock:
        return [room_result(chat, r) for r in await chat.rooms_db.list_all()]


@app.post("/api/rooms", status_code=201)
async def create_room(data: RoomCreate, request: Request):
    chat = request.app.state.chat
    values = data.model_dump()
    async with chat.lock:
        await validate_members(chat, values)
        room = await chat.rooms_db.create(values)
    return room_result(chat, room)


@app.put("/api/rooms/{room_id}")
async def update_room(room_id: str, request: Request):
    chat = request.app.state.chat
    try:
        changes = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Expected a JSON object") from exc
    if not isinstance(changes, dict):
        raise HTTPException(422, "Expected a JSON object")
    async with chat.lock:
        room = await chat.require_room(room_id)
        chat.ensure_idle(room_id)
        try:
            values = RoomCreate.model_validate({**{key: room[key] for key in RoomCreate.model_fields}, **changes}).model_dump()
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        await validate_members(chat, values)
        room = await chat.rooms_db.update(room_id, values)
    return room_result(chat, room)


@app.delete("/api/rooms/{room_id}")
async def delete_room(room_id: str, request: Request):
    chat = request.app.state.chat
    async with chat.lock:
        await chat.require_room(room_id)
        chat.ensure_idle(room_id)
        await chat.rooms_db.delete(room_id)
    return {"status": "deleted"}


@app.get("/api/rooms/{room_id}/messages")
async def list_messages(room_id: str, request: Request):
    chat = request.app.state.chat
    async with chat.lock:
        await chat.require_room(room_id)
        return await chat.messages_db.list_by_room(room_id)


@app.post("/api/rooms/{room_id}/send")
async def send_message(room_id: str, data: SendRequest, request: Request):
    return await request.app.state.chat.start(room_id, **data.model_dump())


@app.post("/api/rooms/{room_id}/continue")
async def continue_conversation(room_id: str, data: ContinueRequest, request: Request):
    return await request.app.state.chat.start(room_id, **data.model_dump())


@app.post("/api/rooms/{room_id}/stop")
async def stop_conversation(room_id: str, request: Request):
    return await request.app.state.chat.stop(room_id)


@app.get("/api/runs/{run_id}/status")
async def get_run_status(run_id: str, request: Request):
    run = request.app.state.chat.get_run_status(run_id)
    if run is None:
        raise HTTPException(404, "Conversation run not found")
    return run


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8765)
