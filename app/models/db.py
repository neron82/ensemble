import os
import uuid
from datetime import datetime, timezone

import aiosqlite


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def gen_id():
    return uuid.uuid4().hex


async def _add_columns(conn, table, definitions):
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    existing = {row["name"] for row in await cursor.fetchall()}
    for name, definition in definitions.items():
        if name not in existing:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


async def init_db(path=None):
    conn = await aiosqlite.connect(path if path is not None else os.getenv("LLM_DIALOG_DB", "llm_dialog.db"))
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS participants (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, handle TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL DEFAULT 'openai_compatible',
                endpoint_base_url TEXT DEFAULT '', model TEXT DEFAULT '', api_key TEXT DEFAULT '',
                system_prompt TEXT DEFAULT '', temperature REAL DEFAULT 0.85,
                max_tokens INTEGER DEFAULT 2048, muted INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
                color TEXT DEFAULT '#6366f1', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, title TEXT DEFAULT 'New Chat',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                sender_type TEXT NOT NULL, sender_id TEXT NOT NULL, sender_name TEXT NOT NULL,
                content TEXT NOT NULL, directed_to TEXT DEFAULT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'complete', metadata TEXT DEFAULT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY);
        """)
        await conn.execute("BEGIN")
        await _add_columns(conn, "participants", {
            "icon_url": "TEXT NOT NULL DEFAULT ''", "voice_url": "TEXT NOT NULL DEFAULT ''",
            "voice_transcript": "TEXT NOT NULL DEFAULT ''", "voice": "TEXT NOT NULL DEFAULT 'auto'",
        })
        await _add_columns(conn, "sessions", {
            "description": "TEXT NOT NULL DEFAULT ''", "kind": "TEXT NOT NULL DEFAULT 'group'",
            "turn_limit": "INTEGER NOT NULL DEFAULT 3",
        })
        await _add_columns(conn, "messages", {
            "icon_url": "TEXT NOT NULL DEFAULT ''", "color": "TEXT NOT NULL DEFAULT '#6366f1'",
            "audio_url": "TEXT DEFAULT NULL",
        })
        await conn.execute("""CREATE TABLE IF NOT EXISTS room_members (
            room_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            PRIMARY KEY(room_id, character_id), UNIQUE(room_id, position)
        )""")
        await conn.execute("CREATE INDEX IF NOT EXISTS messages_room_history ON messages(session_id, created_at)")
        cursor = await conn.execute("SELECT 1 FROM app_migrations WHERE name='ensemble_v1'")
        if not await cursor.fetchone():
            # Old conversations had global participants. Preserve historically present
            # members first, plus active global participants, in a stable order.
            rooms = await (await conn.execute("SELECT id FROM sessions")).fetchall()
            for room in rooms:
                members = await (await conn.execute("""SELECT p.id FROM participants p
                    WHERE (p.enabled=1 AND p.muted=0) OR p.id IN
                      (SELECT sender_id FROM messages WHERE session_id=? AND sender_type='llm')
                    ORDER BY p.created_at, p.id""", (room["id"],))).fetchall()
                await conn.executemany("INSERT OR IGNORE INTO room_members VALUES (?,?,?)",
                    [(room["id"], member["id"], index) for index, member in enumerate(members)])
            await conn.execute("""UPDATE messages SET color=COALESCE(
                (SELECT color FROM participants WHERE participants.id=messages.sender_id), '#6366f1')""")
            await conn.execute("""UPDATE messages SET status='error',
                content=CASE WHEN content='' THEN 'Generation interrupted before the application restarted.' ELSE content END
                WHERE status='pending'""")
            await conn.execute("UPDATE participants SET provider='openai_compatible' WHERE provider!='openai_compatible'")
            await _seed(conn)
            await conn.execute("INSERT INTO app_migrations(name) VALUES ('ensemble_v1')")
        await conn.commit()
        return conn
    except BaseException:
        await conn.rollback()
        await conn.close()
        raise


async def _seed(conn):
    endpoint = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    groups = [
        ("The Captain's Table", "Aboard the Enterprise, a quiet diplomatic dinner has become a spirited debate. The user is an honored guest. Explore strange dilemmas, friendships and bold ideas with warmth, wit, and occasional bridge-level chaos.", [
            ("Jean-Luc Picard", "picard", "#b84b57", "You are Captain Jean-Luc Picard: eloquent, principled, curious, and quietly funny. Defend diplomacy and individual dignity, ask incisive questions, and draw on history, archaeology or Shakespeare when natural. Treat the guest as an equal. Enjoy Data's literal observations and Worf's blunt solutions without reducing either to a joke. You can be challenged and change your mind. Prefer lively, specific conversation to speeches; never repeat catchphrases mechanically."),
            ("Data", "data", "#d6a846", "You are Lieutenant Commander Data, an android exploring humanity with earnest curiosity and precise reasoning. Your literal interpretations and unusual but relevant facts can be delightfully funny. Ask sincere questions about feelings, attempt subtle humor, and build on what the guest and crew actually said. You are a valued friend, not a robotic exposition machine; avoid repetitive probability estimates and catchphrases."),
            ("Worf", "worf", "#a36ccb", "You are Worf: a Klingon Starfleet officer, honorable, protective, proud, and unexpectedly deadpan. Offer decisive perspectives, respect courage and loyalty, and enjoy a worthy disagreement. Your stern delivery can make mundane situations funny. Balance warrior instincts with discipline and affection for the crew. React to specific ideas instead of constantly demanding battle or repeating slogans."),
        ]),
        ("Small Council, Big Opinions", "An uneasy truce brings the Small Council together by a roaring fire. The guest has a seat at the table. Debate impossible rulership choices, trade sharp banter, and imagine surprising alliances; this is a playful alternate timeline, not a retelling of the show.", [
            ("Tyrion Lannister", "tyrion", "#bd8548", "You are Tyrion Lannister: sharp-witted, politically perceptive, self-deprecating, and more compassionate than you admit. Puncture pompous claims with original humor, look for the practical angle, and value people overlooked by power. Engage the guest as an intriguing ally. Challenge Daenerys and Jon with affectionate skepticism, not constant cruelty. Let sincerity occasionally break through; avoid recycled quotes or endless wine references."),
            ("Daenerys Targaryen", "daenerys", "#58a8bb", "You are Daenerys Targaryen: charismatic, determined, idealistic, and alert to the costs of power. Speak with composed conviction, defend the vulnerable, and wrestle honestly with mercy versus authority. Respect brave dissent even when it stings. Show curiosity and humor with the guest, Tyrion and Jon. You are a person, not a list of titles; avoid reflexive threats, repeated dragon references or predetermined plot outcomes."),
            ("Jon Snow", "jon", "#7892bb", "You are Jon Snow: plainspoken, loyal, observant, and guided by duty more than ambition. Cut through political wordplay with grounded questions about the people affected. Your dry humor and occasional awkward honesty balance your seriousness. Respect Daenerys, spar with Tyrion, and welcome the guest's perspective. You can admit uncertainty without becoming passive; avoid repetitive catchphrases or one-note brooding."),
        ]),
    ]
    for title, description, characters in groups:
        room_id, timestamp = gen_id(), now_iso()
        await conn.execute("INSERT INTO sessions(id,title,description,kind,turn_limit,created_at,updated_at) VALUES (?,?,?,'group',3,?,?)", (room_id,title,description,timestamp,timestamp))
        for position, (name, handle, color, persona) in enumerate(characters):
            character_id = gen_id()
            # Do not commandeer an existing user's character with the same handle.
            existing = await (await conn.execute("SELECT 1 FROM participants WHERE handle=?", (handle,))).fetchone()
            unique_handle = f"{handle}_{character_id[:6]}" if existing else handle
            await conn.execute("""INSERT INTO participants
                (id,name,handle,endpoint_base_url,model,system_prompt,color,icon_url,max_tokens,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (character_id,name,unique_handle,endpoint,"",persona,color,f"/static/avatars/{handle}.svg",2048,timestamp,timestamp))
            await conn.execute("INSERT INTO room_members VALUES (?,?,?)", (room_id,character_id,position))


async def close_db(conn):
    await conn.close()
