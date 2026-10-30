from app.models.db import gen_id, now_iso


class MessageCRUD:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _row_to_dict(row):
        if row["status"] == "error" or row["sender_type"] == "system":
            role = "error"
        elif row["sender_type"] == "human":
            role = "user"
        else:
            role = "assistant"
        return {
            "id": row["id"], "room_id": row["session_id"], "role": role,
            "character_id": row["sender_id"] if row["sender_type"] == "llm" else None,
            "speaker_name": row["sender_name"], "content": row["content"],
            "icon_url": row["icon_url"], "color": row["color"],
            "created_at": row["created_at"], "audio_url": row["audio_url"],
        }

    async def list_by_room(self, room_id):
        rows = await (await self.db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at, rowid", (room_id,))).fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def create(self, data):
        message_id, timestamp = gen_id(), now_iso()
        role = data["role"]
        sender_type = {"user": "human", "assistant": "llm", "error": "system"}[role]
        await self.db.execute("""INSERT INTO messages
            (id,session_id,sender_type,sender_id,sender_name,content,created_at,status,icon_url,color,audio_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (message_id,data["room_id"],sender_type,data.get("character_id") or sender_type,
            data.get("speaker_name", "You" if role == "user" else "System"),data["content"],timestamp,
            "error" if role == "error" else "complete",data.get("icon_url", ""),data.get("color", "#6366f1"),data.get("audio_url")))
        await self.db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (timestamp,data["room_id"]))
        await self.db.commit()
        return await self.get(message_id)

    async def get(self, message_id):
        row = await (await self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,))).fetchone()
        return self._row_to_dict(row) if row else None

    async def set_audio(self, message_id, url):
        await self.db.execute("UPDATE messages SET audio_url=? WHERE id=?", (url,message_id))
        await self.db.commit()
