import os
import re

from app.models.db import gen_id, now_iso


class CharacterCRUD:
    COLUMNS = {
        "name": "name", "handle": "handle", "provider": "provider",
        "endpoint": "endpoint_base_url", "model": "model", "api_key": "api_key",
        "persona": "system_prompt", "temperature": "temperature", "max_tokens": "max_tokens",
        "color": "color", "icon_url": "icon_url", "voice_url": "voice_url",
        "voice_transcript": "voice_transcript", "voice": "voice",
    }

    def __init__(self, db):
        self.db = db

    def _row_to_dict(self, row):
        result = {key: row[column] for key, column in self.COLUMNS.items()}
        result.update({key: row[key] for key in ("id", "created_at", "updated_at")})
        result["endpoint"] = result["endpoint"] or os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
        for key in ("persona", "model", "api_key", "icon_url", "voice_url", "voice_transcript"):
            result[key] = result[key] or ""
        return result

    @staticmethod
    def public(character):
        result = {key: value for key, value in character.items() if key != "api_key"}
        result["has_api_key"] = bool(character.get("api_key"))
        return result

    async def list_all(self):
        rows = await (await self.db.execute("SELECT * FROM participants ORDER BY created_at, rowid")).fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get(self, character_id):
        row = await (await self.db.execute("SELECT * FROM participants WHERE id=?", (character_id,))).fetchone()
        return self._row_to_dict(row) if row else None

    async def get_by_handle(self, handle):
        row = await (await self.db.execute("SELECT * FROM participants WHERE handle=?", (handle,))).fetchone()
        return self._row_to_dict(row) if row else None

    async def create(self, data):
        data = dict(data)
        if not data.get("handle"):
            base = re.sub(r"[^a-z0-9_]", "", data["name"].lower().replace(" ", "_"))[:50] or "character"
            handle, suffix = base, 2
            while await self.get_by_handle(handle):
                handle, suffix = f"{base}_{suffix}", suffix + 1
            data["handle"] = handle
        if data.pop("clear_api_key", False):
            data["api_key"] = ""
        selected = {self.COLUMNS[key]: value for key, value in data.items() if key in self.COLUMNS}
        character_id, timestamp = gen_id(), now_iso()
        selected.update(id=character_id, created_at=timestamp, updated_at=timestamp)
        await self.db.execute(f"INSERT INTO participants ({','.join(selected)}) VALUES ({','.join('?' for _ in selected)})", tuple(selected.values()))
        await self.db.commit()
        return await self.get(character_id)

    async def update(self, character_id, data):
        data = dict(data)
        clear = data.pop("clear_api_key", False)
        if clear:
            data["api_key"] = ""
        elif not data.get("api_key"):
            data.pop("api_key", None)
        if "handle" in data and not data["handle"]:
            raise ValueError("Handle must not be blank")
        selected = {self.COLUMNS[key]: value for key, value in data.items() if key in self.COLUMNS}
        if selected:
            selected["updated_at"] = now_iso()
            await self.db.execute(f"UPDATE participants SET {','.join(key+'=?' for key in selected)} WHERE id=?", (*selected.values(), character_id))
            await self.db.commit()
        return await self.get(character_id)

    async def delete(self, character_id):
        # Message sender snapshots intentionally have no foreign key to characters.
        await self.db.execute("DELETE FROM room_members WHERE character_id=?", (character_id,))
        await self.db.execute("DELETE FROM participants WHERE id=?", (character_id,))
        await self.db.commit()
