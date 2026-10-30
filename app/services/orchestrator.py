import asyncio
import logging

from app.models.db import gen_id
from app.services.routing import select_speakers

logger = logging.getLogger(__name__)


class ChatError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class Orchestrator:
    def __init__(self, characters_db, messages_db, rooms_db, adapter_registry):
        self.characters_db = characters_db
        self.messages_db = messages_db
        self.rooms_db = rooms_db
        self.adapter_registry = adapter_registry
        # Short persistence/configuration sections share this lock. Network calls
        # never hold it, so unrelated rooms generate concurrently.
        self.lock = asyncio.Lock()
        self._runs = {}
        self._active = {}
        self._tasks = {}
        self._closing = False

    def get_run_status(self, run_id):
        run = self._runs.get(run_id)
        return dict(run) if run else None

    def active_run(self, room_id):
        run_id = self._active.get(room_id)
        return self.get_run_status(run_id) if run_id else None

    def ensure_idle(self, room_id):
        if room_id in self._active:
            raise ChatError(409, "This room has an active conversation. Stop it before making changes.")

    def ensure_character_idle(self, character_id):
        for task_data in self._tasks.values():
            if character_id in task_data["character_ids"]:
                raise ChatError(409, "This character is participating in an active conversation")

    async def require_room(self, room_id):
        room = await self.rooms_db.get(room_id)
        if room is None:
            raise ChatError(404, "Room not found")
        return room

    async def start(self, room_id, content=None, target_character_id=None, turn_limit=None):
        async with self.lock:
            if self._closing:
                raise ChatError(503, "Application is shutting down")
            room = await self.require_room(room_id)
            self.ensure_idle(room_id)
            limit = room["turn_limit"] if turn_limit is None else turn_limit
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 24:
                raise ChatError(422, "Turn limit must be an integer between 1 and 24")
            history = await self.messages_db.list_by_room(room_id)
            if content is None and not any(message["role"] in {"user", "assistant"} for message in history):
                raise ChatError(400, "Send a message before continuing this conversation")
            if content is not None:
                content = content.strip()
                if not content:
                    raise ChatError(422, "Message must not be blank")
            characters = []
            for character_id in room["character_ids"]:
                character = await self.characters_db.get(character_id)
                if character:
                    characters.append(character)
            try:
                speakers = select_speakers(room, characters, target_character_id, history)
            except ValueError as exc:
                raise ChatError(422, str(exc)) from exc
            for character in speakers:
                if character["provider"] not in self.adapter_registry:
                    raise ChatError(422, f"Unsupported provider for {character['name']}")
            if content is not None:
                await self.messages_db.create({"room_id": room_id, "role": "user", "content": content})
            run_id = gen_id()
            run = {"id": run_id, "room_id": room_id, "status": "running", "completed_turns": 0,
                   "turn_limit": limit, "current_character_id": None, "error": None}
            self._runs[run_id] = run
            self._active[room_id] = run_id
            task = asyncio.create_task(self._run(run, room, speakers, characters), name=f"chat-{run_id}")
            self._tasks[run_id] = {"task": task, "character_ids": room["character_ids"]}
            return {"run_id": run_id}

    def _is_current(self, run):
        return run["status"] == "running" and self._active.get(run["room_id"]) == run["id"]

    async def _run(self, run, room, speakers, characters):
        try:
            for turn in range(run["turn_limit"]):
                async with self.lock:
                    if not self._is_current(run):
                        return
                    character = speakers[turn % len(speakers)]
                    run["current_character_id"] = character["id"]
                    history = await self.messages_db.list_by_room(room["id"])
                result = await self.adapter_registry[character["provider"]].send_message(
                    character, history, None, {"room": room, "characters": characters}
                )
                async with self.lock:
                    # Also protects against adapters that swallow CancelledError.
                    # Stop cannot cancel a transaction: it must acquire this lock
                    # before marking stopped and cancelling the task.
                    if not self._is_current(run):
                        return
                    if not isinstance(result, dict) or result.get("status") != "ok":
                        error = result.get("error", "The model request failed") if isinstance(result, dict) else "Invalid model response"
                        raise ValueError(str(error))
                    content = result.get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("The model returned an empty reply")
                    await self.messages_db.create({
                        "room_id": room["id"], "role": "assistant", "character_id": character["id"],
                        "speaker_name": character["name"], "icon_url": character["icon_url"],
                        "color": character["color"], "content": content.strip(),
                    })
                    run["completed_turns"] += 1
            async with self.lock:
                if self._is_current(run):
                    run["status"] = "completed"
        except asyncio.CancelledError:
            run["status"] = "stopped"
        except Exception as exc:
            async with self.lock:
                if self._is_current(run):
                    error = str(exc) or "Conversation failed"
                    for character in characters:
                        if character.get("api_key"):
                            error = error.replace(character["api_key"], "[redacted]")
                    run["error"] = error[:1000]
                    run["status"] = "error"
                    try:
                        await self.messages_db.create({"room_id": room["id"], "role": "error", "content": run["error"]})
                    except Exception:
                        logger.exception("Unable to persist conversation error")
        finally:
            async with self.lock:
                run["current_character_id"] = None
                if self._active.get(room["id"]) == run["id"]:
                    self._active.pop(room["id"], None)
                self._tasks.pop(run["id"], None)

    async def stop(self, room_id):
        async with self.lock:
            await self.require_room(room_id)
            run_id = self._active.get(room_id)
            if run_id is None:
                return {"status": "stopped"}
            run = self._runs[run_id]
            run["status"] = "stopped"
            run["current_character_id"] = None
            task = self._tasks[run_id]["task"]
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        async with self.lock:
            if self._active.get(room_id) == run_id:
                self._active.pop(room_id, None)
            self._tasks.pop(run_id, None)
        return self.get_run_status(run_id)

    async def close(self):
        async with self.lock:
            self._closing = True
            tasks = [value["task"] for value in self._tasks.values()]
            for run_id in self._active.values():
                self._runs[run_id]["status"] = "stopped"
                self._runs[run_id]["current_character_id"] = None
            for task in tasks:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._active.clear()

    async def get_message(self, message_id):
        return await self.messages_db.get(message_id)

    async def get_character(self, character_id):
        return await self.characters_db.get(character_id)

    async def set_message_audio(self, message_id, url):
        async with self.lock:
            await self.messages_db.set_audio(message_id, url)
