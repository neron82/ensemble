# Ensemble

A local-first character chat system: assemble a cast, set a scene, and let models answer one
another in character. FastAPI, SQLite and a vanilla-JavaScript interface; no frontend build, no CDN,
no cloud keys. Every character can point at its own OpenAI-compatible endpoint, and every reply can
be spoken in that character's cloned voice through a local OmniVoice server.

Starter rooms ship with it — *The Captain's Table* (Picard, Data, Worf) and *Small Council, Big
Opinions* (Tyrion, Daenerys, Jon) — as editable roleplay examples, not recordings or official
likenesses.

## What it does

- **Rooms** with their own scene, cast, speaking order, history, reply budget and active run.
  Rooms run independently; one can keep talking while you work in another.
- **Characters** with persona, icon, endpoint, model, key, temperature, output allowance and voice.
  **1:1 chat** opens or reuses a private room with one character.
- **Bounded runs**: a reply cap (1–24) is the total number of character replies per send or
  Continue, not rounds per cast member. **Stop** cancels the run and keeps what was already said.
- **Cloned voices**: upload a reference recording plus its exact transcript, then use **Listen**.
  Replies are cached per message; auto-play can play a room's new replies in sequence.
- **No silent fallbacks**: model failures stop that room's run and show an error; truncated
  responses are treated as errors rather than publishing unfinished reasoning as dialogue.

## Run

Requires Python 3.11 or newer.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Open **http://127.0.0.1:8765**. Run a single application worker: active conversations and
cancellation are process-local. The entry point binds to loopback, not the network.

## Configuration

Set environment variables before starting the process.

| Setting | Environment variable | Default |
| --- | --- | --- |
| Model API base | `LLM_BASE_URL` | `http://127.0.0.1:8080/v1` |
| OmniVoice base | `VOICE_BASE_URL` | `http://127.0.0.1:8880` |
| SQLite database | `LLM_DIALOG_DB` | `llm_dialog.db` |
| Uploads and generated speech | `LLM_DIALOG_MEDIA_DIR` | `data/media` |

Individual characters can override the endpoint, model ID and key. An empty model ID discovers the
first model advertised by that character's `/v1/models`. Both base URLs with and without `/v1` are
accepted for the model connection; the voice URL is the server root, not its `/health` URL.

## How a run works

The cast takes turns in its configured order, continuing after the last speaker on later runs. Each
speaker receives the room scene, their own persona and the preceding conversation, including replies
generated earlier in the same run. The target selector restricts a run to one member; textual
mentions never trigger extra turns. **Continue** lets the cast respond again to existing history
without adding a human message, under the same strict cap.

The adapter requests `chat_template_kwargs.enable_thinking=false`, uses only the spoken response
field, removes delimited reasoning, and treats a length-truncated response as an error. If an
endpoint rejects that local-server extension, it must support the option to use this adapter
unchanged. New characters get a 2,048-token output allowance; some local reasoning models count
internal reasoning against it, so raise **Advanced → Maximum output tokens** when needed.

## Architecture

```
main.py                     lifespan, HTTP routes, validation, static/media serving
app/models/db.py            additive migrations and one-time character/room seeds
app/models/{participant,session,message}.py
                            character, room/membership and attributed-message persistence
app/services/orchestrator.py
                            cancellable bounded runs; short serialized persistence sections
app/services/routing.py     explicit target or ordered room-member selection
app/adapters/openai_compatible.py
                            model discovery, persona/history prompts, response handling
app/services/media.py       bounded uploads, voice synthesis/cache, local-service discovery
static/                     responsive room UI, character studio, playback, portraits
tests/                      isolated regression suite
```

Network calls never hold the database coordination lock. Uploads use generated filenames rather than
trusted paths; icons accept PNG/JPEG/GIF/WebP up to 5 MiB (SVG rejected), voice references accept
WAV/MP3/OGG/FLAC up to 25 MiB.

The HTTP interface is `/api/characters`, `/api/rooms`, room-scoped `/messages`, `/send`, `/continue`,
`/stop`, `/api/runs/{id}/status`, `/api/uploads/{kind}`, `/api/messages/{id}/audio` and
`/api/services`. Interactive API documentation is at `/docs`.

## Tests

```sh
.venv/bin/python -m pytest -q
```

The suite does not contact your model or voice server. It covers total reply bounds, context
handoff, multi-turn follow-ups, independent rooms, in-flight cancellation, active-room edit
conflicts, direct membership, target enforcement, historical attribution after character deletion,
key privacy, migration/restart idempotence, endpoint normalization, truncated responses, concurrent
cached audio playback, voice-error recovery and upload/path safety. Current state: 14 passed.

## Security and limits

This is a **trusted, single-user local application, not an authenticated public service**. Do not
expose it directly to the internet. Keys live in the local database and are not returned in
character API responses; an empty key edit preserves the existing key, and the editor offers an
explicit removal option. Back up the database and the media directory together.

Model failures stop that room's run; there are no automatic model-driven conversation loops,
production mock responses or silent service fallbacks. Persona fidelity and prose length depend on
the loaded model. `PROJECT.md` holds the fuller design and behaviour notes.
