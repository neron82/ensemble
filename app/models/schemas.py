import os
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TurnLimit = Annotated[int, Field(strict=True, ge=1, le=24)]


class CharacterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    handle: str = Field(default="", max_length=60)
    persona: str = Field(default="", max_length=16000)
    provider: Literal["openai_compatible"] = "openai_compatible"
    endpoint: str = Field(default_factory=lambda: os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1"))
    model: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=4096)
    clear_api_key: bool = False
    temperature: float = Field(default=0.85, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=2048, strict=True, ge=1, le=32768)
    color: str = "#6366f1"
    icon_url: str = ""
    voice_url: str = ""
    voice_transcript: str = Field(default="", max_length=16000)
    voice: str = Field(default="auto", max_length=100)

    @field_validator("name", "handle", "endpoint", "model", "voice")
    @classmethod
    def strip_text(cls, value):
        return value.strip()

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value:
            raise ValueError("Name must not be blank")
        return value

    @field_validator("handle")
    @classmethod
    def valid_handle(cls, value):
        value = value.lower()
        if value and not re.fullmatch(r"[a-z0-9_]+", value):
            raise ValueError("Handle must contain letters, numbers or underscores")
        return value

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Endpoint must be an HTTP(S) URL without credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("color")
    @classmethod
    def valid_color(cls, value):
        if not re.fullmatch(r"#[a-fA-F0-9]{6}", value):
            raise ValueError("Color must be a six-digit hex color")
        return value

    @field_validator("icon_url", "voice_url")
    @classmethod
    def local_media(cls, value, info):
        if not value:
            return value
        if info.field_name == "icon_url" and re.fullmatch(r"/static/avatars/(picard|data|worf|tyrion|daenerys|jon)\.svg", value):
            return value
        extensions = "png|jpg|webp|gif" if info.field_name == "icon_url" else "wav|mp3|flac|ogg"
        if not re.fullmatch(r"/media/[a-f0-9]{32}\.(?:" + extensions + r")", value):
            raise ValueError("Media must be a local uploaded file URL")
        return value


class CharacterUpdate(CharacterCreate):
    # Inherited defaults are excluded when serializing an update; explicitly
    # supplied nulls remain invalid, so stored NOT NULL fields stay well formed.
    name: str = Field(default="", min_length=1, max_length=100)


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=12000)
    kind: Literal["group", "direct"] = "group"
    character_ids: list[str] = Field(default_factory=list, max_length=32)
    turn_limit: TurnLimit | None = None

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @model_validator(mode="after")
    def membership(self):
        if len(set(self.character_ids)) != len(self.character_ids):
            raise ValueError("Room members must be unique")
        if self.kind == "direct" and len(self.character_ids) != 1:
            raise ValueError("Direct rooms require exactly one character")
        if self.turn_limit is None:
            self.turn_limit = 1 if self.kind == "direct" else 3
        return self


class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_character_id: str | None = None
    turn_limit: TurnLimit | None = None


class SendRequest(ContinueRequest):
    content: str = Field(min_length=1, max_length=32000)

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Message must not be blank")
        return value
