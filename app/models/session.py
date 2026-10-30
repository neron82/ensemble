from app.models.db import gen_id, now_iso


class RoomCRUD:
    def __init__(self, db):
        self.db = db

    async def list_all(self):
        rows = await (await self.db.execute("SELECT * FROM sessions ORDER BY updated_at DESC, rowid DESC")).fetchall()
        members = await (await self.db.execute("SELECT * FROM room_members ORDER BY position")).fetchall()
        by_room = {}
        for member in members:
            by_room.setdefault(member["room_id"], []).append(member["character_id"])
        return [dict(row, character_ids=by_room.get(row["id"], [])) for row in rows]

    async def get(self, room_id):
        row = await (await self.db.execute("SELECT * FROM sessions WHERE id=?", (room_id,))).fetchone()
        if not row:
            return None
        members = await (await self.db.execute("SELECT character_id FROM room_members WHERE room_id=? ORDER BY position", (room_id,))).fetchall()
        return dict(row, character_ids=[member["character_id"] for member in members])

    async def create(self, data):
        room_id, timestamp = gen_id(), now_iso()
        await self.db.execute("""INSERT INTO sessions(id,title,description,kind,turn_limit,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)""", (room_id,data["title"],data.get("description", ""),data.get("kind", "group"),data.get("turn_limit", 3),timestamp,timestamp))
        await self.db.executemany("INSERT INTO room_members VALUES (?,?,?)", [(room_id,member,index) for index,member in enumerate(data["character_ids"])])
        await self.db.commit()
        return await self.get(room_id)

    async def update(self, room_id, data):
        await self.db.execute("UPDATE sessions SET title=?,description=?,kind=?,turn_limit=?,updated_at=? WHERE id=?", (data["title"],data["description"],data["kind"],data["turn_limit"],now_iso(),room_id))
        await self.db.execute("DELETE FROM room_members WHERE room_id=?", (room_id,))
        await self.db.executemany("INSERT INTO room_members VALUES (?,?,?)", [(room_id,member,index) for index,member in enumerate(data["character_ids"])])
        await self.db.commit()
        return await self.get(room_id)

    async def delete(self, room_id):
        await self.db.execute("DELETE FROM messages WHERE session_id=?", (room_id,))
        await self.db.execute("DELETE FROM room_members WHERE room_id=?", (room_id,))
        await self.db.execute("DELETE FROM sessions WHERE id=?", (room_id,))
        await self.db.commit()
