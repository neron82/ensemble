"""Local media storage and the real OmniVoice speech boundary."""
import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from fastapi import APIRouter, File, HTTPException, Request, UploadFile


MEDIA_DIR = Path(os.getenv("LLM_DIALOG_MEDIA_DIR", "data/media")).expanduser().resolve()
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


class BoundedUploadRoute(APIRoute):
    """Reject oversized multipart bodies before their spool files grow unbounded."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_handler(request):
            if not request.url.path.startswith("/api/uploads/"):
                return await handler(request)
            kind = request.path_params.get("kind")
            if kind not in ("icon", "voice"):
                raise HTTPException(404, "Unknown upload kind")
            # Allow multipart headers while retaining a separate exact file limit.
            limit = (_ICON_LIMIT if kind == "icon" else _VOICE_LIMIT) + _CHUNK_SIZE
            declared = request.headers.get("content-length")
            if declared:
                try:
                    length = int(declared)
                except ValueError as exc:
                    raise HTTPException(400, "Invalid Content-Length") from exc
                if length < 0:
                    raise HTTPException(400, "Invalid Content-Length")
                if length > limit:
                    raise HTTPException(413, "Upload body exceeds the file size limit")
            receive = request._receive
            received = 0
            exceeded = False

            async def limited_receive():
                nonlocal received, exceeded
                event = await receive()
                received += len(event.get("body", b""))
                if received > limit:
                    exceeded = True
                    # Starlette closes multipart spool files for this exception.
                    raise MultiPartException("Upload body exceeds the file size limit")
                return event

            request._receive = limited_receive
            try:
                return await handler(request)
            except StarletteHTTPException as exc:
                if exceeded:
                    raise HTTPException(413, "Upload body exceeds the file size limit") from exc
                raise
            finally:
                request._receive = receive

        return bounded_handler


router = APIRouter(prefix="/api", route_class=BoundedUploadRoute)

_ICON_LIMIT = 5 * 1024 * 1024
_VOICE_LIMIT = 25 * 1024 * 1024
_SPEECH_LIMIT = 50 * 1024 * 1024
_TEXT_LIMIT = 20000
_CLONE_TEXT_LIMIT = 10000
_CHUNK_SIZE = 64 * 1024
_MEDIA_NAME = re.compile(r"/media/([a-f0-9]{32}\.(?:png|jpg|gif|webp|wav|mp3|ogg|flac))")
_AUDIO_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac"}
# Counts include waiters, so removing an idle lock cannot create a second lock
# while another request is still waiting on the original one.
_audio_locks = {}


def _llm_endpoint():
    endpoint = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    return endpoint if endpoint.endswith("/v1") else endpoint + "/v1"


def _voice_endpoint():
    return os.getenv("VOICE_BASE_URL", "http://127.0.0.1:8880").rstrip("/")


def _error_text(value):
    """Keep upstream diagnostics useful without returning unbounded HTML/errors."""
    if isinstance(value, dict):
        value = value.get("detail") or value.get("error") or value.get("message") or "Unexpected upstream response"
        if isinstance(value, dict):
            value = value.get("message") or str(value)
    return " ".join(str(value).split())[:400]


async def _response_error(response):
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=1024):
        body.extend(chunk[:4096 - len(body)])
        if len(body) >= 4096:
            break
    text = body.decode("utf-8", errors="replace")
    # A bounded body may not be complete JSON; the text still gives a diagnostic.
    try:
        text = json.loads(text)
    except ValueError:
        pass
    return _error_text(text) or response.reason_phrase


async def _service_json(client, url):
    async with client.stream("GET", url) as response:
        if response.is_error:
            raise ValueError(f"HTTP {response.status_code}: {await _response_error(response)}")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise ValueError("Service response exceeded 1 MiB")
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ValueError("Service returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Service returned an invalid response")
        return data


async def service_status():
    """Discover local services independently; reachable is not necessarily ready."""
    llm_endpoint, voice_endpoint = _llm_endpoint(), _voice_endpoint()
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0), follow_redirects=False, trust_env=False) as client:
        async def llm_status():
            result = {"ok": False, "endpoint": llm_endpoint, "models": []}
            try:
                async with asyncio.timeout(6):
                    data = await _service_json(client, llm_endpoint + "/models")
                entries = data.get("data")
                if not isinstance(entries, list):
                    raise ValueError("Model service returned no model list")
                result["models"] = [row["id"] for row in entries if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()]
                result["ok"] = bool(result["models"])
                if not result["ok"]:
                    result["error"] = "No models are loaded"
            except (httpx.HTTPError, ValueError, TimeoutError) as exc:
                result["error"] = _error_text(exc) or "Model service is unavailable"
            return result

        async def voice_status():
            result = {"ok": False, "endpoint": voice_endpoint}
            try:
                async with asyncio.timeout(6):
                    data = await _service_json(client, voice_endpoint + "/health")
                result["ok"] = data.get("ready") is True and data.get("model_loaded") is True
                if not result["ok"]:
                    result["error"] = "Voice service is reachable but its model is not ready"
            except (httpx.HTTPError, ValueError, TimeoutError) as exc:
                result["error"] = _error_text(exc) or "Voice service is unavailable"
            return result

        llm, voice = await asyncio.gather(llm_status(), voice_status())
    return {"llm": llm, "voice": voice}


def _file_extension(data, kind):
    if kind == "icon":
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            return "png"
        if data.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "webp"
        raise HTTPException(415, "Icons must be PNG, JPEG, GIF, or WebP images; SVG is not accepted")
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"fLaC"):
        return "flac"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xff and data[1] & 0xe0 == 0xe0):
        return "mp3"
    raise HTTPException(415, "Voice references must be WAV, MP3, OGG, or FLAC audio")


@router.post("/uploads/{kind}")
async def upload_media(kind: str, file: UploadFile = File(...)):
    destination = None
    try:
        if kind not in ("icon", "voice"):
            raise HTTPException(404, "Unknown upload kind")
        limit = _ICON_LIMIT if kind == "icon" else _VOICE_LIMIT
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            raise HTTPException(400, "The uploaded file is empty")
        extension = _file_extension(chunk, kind)
        filename = f"{uuid4().hex}.{extension}"
        destination = MEDIA_DIR / filename
        size = 0
        with destination.open("xb") as output:
            while chunk:
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"{kind.capitalize()} uploads must be at most {limit // (1024 * 1024)} MiB")
                output.write(chunk)
                chunk = await file.read(_CHUNK_SIZE)
        return {"url": f"/media/{filename}", "filename": filename}
    except BaseException:
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def _stored_media(url, *, audio=False):
    match = _MEDIA_NAME.fullmatch(url) if isinstance(url, str) else None
    if not match:
        raise HTTPException(400, "Invalid stored media URL; upload the reference again")
    path = MEDIA_DIR / match.group(1)
    if audio and path.suffix[1:] not in _AUDIO_TYPES:
        raise HTTPException(400, "The voice reference must be an audio upload")
    if path.is_symlink() or path.resolve().parent != MEDIA_DIR:
        raise HTTPException(400, "Invalid stored media path")
    if not path.is_file():
        raise HTTPException(404, "Stored media is missing; upload the reference again")
    return path


@asynccontextmanager
async def _message_lock(chat, message_id):
    key = (id(chat), message_id)
    entry = _audio_locks.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            _audio_locks.pop(key, None)


async def _synthesize(client, url, destination, **kwargs):
    """Bound the upstream response and only publish a completed WAV file."""
    temporary = destination.with_suffix(".part")
    try:
        async with client.stream("POST", url, **kwargs) as response:
            if response.is_error or response.is_redirect:
                detail = await _response_error(response)
                raise HTTPException(502, f"Voice service returned HTTP {response.status_code}: {detail}")
            header = bytearray()
            size = 0
            with temporary.open("xb") as output:
                async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    size += len(chunk)
                    if size > _SPEECH_LIMIT:
                        raise HTTPException(502, "Voice service audio exceeded 50 MiB")
                    if len(header) < 12:
                        header.extend(chunk[:12 - len(header)])
                    output.write(chunk)
            if size < 44 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                raise HTTPException(502, "Voice service did not return WAV audio")
        temporary.replace(destination)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Voice synthesis timed out; message text is saved. Try playback again") from exc
    except httpx.HTTPError as exc:
        detail = _error_text(exc) or "connection failed"
        raise HTTPException(502, f"Voice service unavailable: {detail}") from exc
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/messages/{message_id}/audio")
async def message_audio(message_id: str, request: Request):
    chat = request.app.state.chat
    async with _message_lock(chat, message_id):
        message = await chat.get_message(message_id)
        if message is None:
            raise HTTPException(404, "Message not found")
        if message.get("role") != "assistant":
            raise HTTPException(400, "Only character replies can be spoken")
        cached = message.get("audio_url")
        if cached:
            try:
                path = _stored_media(cached, audio=True)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            else:
                if path.suffix != ".wav":
                    raise HTTPException(400, "Invalid cached speech format")
                return {"audio_url": cached}
        content = message.get("content") or ""
        if not content.strip():
            raise HTTPException(400, "This reply has no text to speak yet")
        if len(content) > _TEXT_LIMIT:
            raise HTTPException(413, f"Speech is limited to {_TEXT_LIMIT} characters per reply")
        character = await chat.get_character(message.get("character_id"))
        if character is None:
            raise HTTPException(404, "This reply's character no longer exists")
        reference = character.get("voice_url")
        transcript = character.get("voice_transcript") or ""
        reference_path = None
        if reference:
            if len(content) > _CLONE_TEXT_LIMIT:
                raise HTTPException(413, f"Cloned speech is limited to {_CLONE_TEXT_LIMIT} characters per reply")
            if not transcript.strip():
                raise HTTPException(400, "Add an exact transcript of the voice reference before cloning")
            if len(transcript) > _TEXT_LIMIT:
                raise HTTPException(413, f"Reference transcript is limited to {_TEXT_LIMIT} characters")
            reference_path = _stored_media(reference, audio=True)
            if reference_path.stat().st_size > _VOICE_LIMIT:
                raise HTTPException(413, "Voice reference exceeds 25 MiB; upload a shorter reference")
        destination = MEDIA_DIR / f"{uuid4().hex}.wav"
        timeout = httpx.Timeout(180.0, connect=5.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                # The outer deadline also bounds a server that slowly streams forever.
                async with asyncio.timeout(180):
                    if reference_path is not None:
                        with reference_path.open("rb") as reference_file:
                            await _synthesize(
                                client, _voice_endpoint() + "/v1/audio/speech/clone", destination,
                                data={"text": content, "ref_text": transcript, "response_format": "wav"},
                                files={"ref_audio": (reference_path.name, reference_file, _AUDIO_TYPES[reference_path.suffix[1:]])},
                            )
                    else:
                        await _synthesize(
                            client, _voice_endpoint() + "/v1/audio/speech", destination,
                            json={"model": "omnivoice", "input": content, "voice": character.get("voice") or "auto", "response_format": "wav"},
                        )
            audio_url = f"/media/{destination.name}"
            await chat.set_message_audio(message_id, audio_url)
            if await chat.get_message(message_id) is None:
                raise HTTPException(404, "Message was deleted during voice synthesis")
            return {"audio_url": audio_url}
        except TimeoutError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(504, "Voice synthesis timed out; message text is saved. Try playback again") from exc
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
