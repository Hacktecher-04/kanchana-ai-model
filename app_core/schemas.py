from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ClientContext(BaseModel):
    user_id: Optional[str] = Field(default=None, max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=120)
    model_name: Optional[str] = Field(default=None, max_length=120)
    behavior_profile: Optional[str] = Field(default=None, max_length=4000)
    prebuilt_prompt: Optional[str] = Field(default=None, max_length=16000)
    memory_short: list[str] = Field(default_factory=list, max_length=40)
    memory_long: list[str] = Field(default_factory=list, max_length=80)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    system_prompt: Optional[str] = Field(default=None, max_length=30000)
    history: list["HistoryMessage"] = Field(default_factory=list, max_length=200)
    context: Optional[ClientContext] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=1000)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, gt=0.0, le=1.0)
    repeat_penalty: Optional[float] = Field(default=None, ge=1.0, le=2.0)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatResponse(BaseModel):
    reply: str
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
