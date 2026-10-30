# Ensemble

A local-first character chat system: assemble a cast, set a scene, and hold an ongoing conversation with models that respond to one another in character. FastAPI, SQLite, and a responsive vanilla JavaScript interface; no frontend build or CDN is required.

## Run

Requires Python 3.11 or newer.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Open **http://127.0.0.1:8765**. Run a single application worker: active conversations and cancellation are process-local. The entry point binds to loopback, not the network.

Defaults:

| Setting | Environment variable | Default |
| --- | --- | --- |
| Model API base | `LLM_BASE_URL` | `http://127.0.0.1:8080/v1` |
| OmniVoice base | `VOICE_BASE_URL` | `http://127.0.0.1:8880` |
| SQLite database | `LLM_DIALOG_DB` | `llm_dialog.db` |
| Uploads and generated speech | `LLM_DIALOG_MEDIA_DIR` | `data/media` |

Set environment variables before starting the process. Individual characters can use different model endpoints, model IDs, and API keys. An empty model ID discovers the first model advertised by that character's `/v1/models` endpoint. Both base URLs with and without `/v1` are accepted for the model connection; the voice URL is the server root, not its `/health` URL.

This is a trusted, single-user local application, **not an authenticated public service**. Do not expose it directly to the internet. Keys are stored in the local database, not returned in character API responses; an empty key edit preserves the existing key, and the editor offers an explicit removal option. Back up the database and media directory together.

## Make a conversation

- **Rooms:** create a named room, describe its scene, select its cast, and reorder the speaking sequence. Rooms have independent histories, casts, reply budgets, and active runs.
- **Characters:** use the character library to configure name, handle, persona, icon, reference recording/transcript, and default voice. Advanced settings contain endpoint, model, key, temperature, output-token allowance, and color.
- **Direct conversations:** select **1:1 chat** on a character to open or reuse a private room with that character.
- **Reply cap:** the number is the **total character replies per send or Continue**, not rounds per cast member. The server accepts 1–24. A cap of 3 in a three-character room produces three replies total. A cap of 1 produces one reply and hands control back to you.
- **Speaking order:** the cast takes turns in its configured order, continuing after the last speaker on subsequent runs. Each speaker receives the room scene, their own persona, and the preceding conversation, including replies generated earlier in the same run. The target selector restricts a run to one member. Textual mentions never trigger extra turns.
- **Continue:** lets the cast respond again to existing history without adding a new human message. It still has a strict reply cap.
- **Stop:** cancels the active request and prevents additional replies from being saved. Completed messages remain. Another room can keep running independently.
- **Reload:** histories and room settings persist. A page reload reconnects to active runs. An application restart ends active runs without resuming them autonomously; the interface releases its controls and preserves saved messages.

First migration adds two editable starter rooms, **The Captain's Table** (Picard, Data, Worf) and **Small Council, Big Opinions** (Tyrion, Daenerys, Jon). They include original illustrative icon assets and persona directions. These are editable roleplay examples, not recordings or official likenesses/voices. Starter rooms are seeded only once; deleting one does not make it reappear on restart.

Existing prototype characters and conversations are retained by additive schema migration. Existing endpoint/model choices remain configurable; older models may need their saved endpoints updated. Legacy mute/enable flags are used when choosing the initial cast for old rooms; ongoing participation is now controlled by each room's cast.

## Voices

Every assistant reply has **Listen**. Optional **Auto-play voices** plays new replies sequentially in the currently viewed room. Switching rooms stops playback; synthesis errors are displayed separately and never erase conversation text. Browsers may require a Listen click before permitting automatic playback.

For a cloned voice:

1. Open a character editor.
2. Upload a clean reference recording you have permission to use.
3. Enter the **exact words spoken in that recording**.
4. Save the character, then use Listen on a new reply.

Supported references: WAV, MP3, OGG, or FLAC, up to 25 MiB. Icons: PNG, JPEG, GIF, or WebP, up to 5 MiB; SVG uploads are rejected. The app uses generated filenames rather than trusting uploaded paths.

The backend calls OmniVoice's multipart `/v1/audio/speech/clone` with `text`, `ref_audio`, `ref_text`, and `response_format=wav`. Without a reference it explicitly uses `/v1/audio/speech` with the selected standard voice (`auto` by default); it does not pretend to clone an unconfigured voice or silently fall back after a cloning failure. Cloning accepts up to 10,000 text characters, standard speech up to 20,000, with a 180-second synthesis deadline.

Generated WAV files are cached per message. Changing a character's voice affects speech not already synthesized; historical cached audio retains its original voice. Removing a character or room does not automatically purge media files. Keep reference media and transcripts private as appropriate.

## Model behavior and errors

New characters have a 2,048-token output allowance. Some local reasoning models count internal reasoning against that allowance, even when thinking is disabled. The adapter requests `chat_template_kwargs.enable_thinking=false` (supported by the configured local server), uses only the spoken response field, removes delimited reasoning, and treats a length-truncated response as an error rather than publishing unfinished reasoning as dialogue. For an endpoint that rejects this local-server extension, its API must support that option to use this adapter unchanged.

If generation exhausts the budget, raise **Advanced → Maximum output tokens**. The **reply cap** and **output tokens** are different controls: the first bounds how many characters speak, the second bounds one model request. Models remain probabilistic; persona fidelity and prose length depend on the loaded model.

Model failures stop that room's run, preserve completed conversation, and show an error. Continue or send again once the connection or configuration is fixed. Connection status independently checks model discovery and OmniVoice readiness. There are no automatic model-driven conversation loops, production mock responses, or silent service fallbacks.

## Code map

- `main.py`: lifespan, same-origin HTTP routes, character/room validation and static/media serving.
- `app/models/db.py`: additive migrations and one-time character/room seeds.
- `app/models/participant.py`, `session.py`, `message.py`: character, room/membership, and attributed message persistence. Legacy table names retain existing data without an export/import cutover.
- `app/services/orchestrator.py`: cancellable bounded runs and short serialized persistence sections; network calls do not hold the database coordination lock.
- `app/services/routing.py`: explicit target or ordered room-member selection.
- `app/adapters/openai_compatible.py`: local model discovery, persona/history prompts, and response handling.
- `app/services/media.py`: bounded uploads, voice synthesis/cache, and local-service discovery.
- `static/index.html`, `styles.css`, `app.js`, `avatars/`: responsive room UI, character studio, playback, and local portrait assets.

The HTTP interface is `/api/characters`, `/api/rooms`, room-scoped `/messages`, `/send`, `/continue`, `/stop`, `/api/runs/{id}/status`, `/api/uploads/{kind}`, `/api/messages/{id}/audio`, and `/api/services`. Interactive backend API documentation is at `/docs`. The old `/api/participants` and `/api/sessions` routes were replaced rather than kept as aliases.

## Verification

```sh
.venv/bin/python -m pytest -q
```

The isolated regression suite covers total reply bounds, context handoff, multi-turn follow-ups, independent rooms, in-flight cancellation, active-room edit conflicts, direct membership, target enforcement, historical attribution after character deletion, key privacy, migration/restart idempotence, model endpoint normalization and truncated responses, concurrent cached audio playback, voice-error recovery, and upload/path safety. Tests do not contact your model or voice server.

Live verification exercised the local Qwen endpoint and OmniVoice, three-speaker crew dialogue, continued 1:1 history, custom persona/icon/reference/transcript creation in the browser, real cloned audio decoding/playback, standard speech generation, stop/reload/restart behavior, and desktop/mobile layouts using an isolated copy of the original database.
