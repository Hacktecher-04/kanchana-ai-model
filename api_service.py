#!/usr/bin/env python3
"""Compatibility entrypoint for Uvicorn.

The API implementation now lives in `app_core/` modules.
"""

from app_core.api import app
from app_core.schemas import ChatRequest, ChatResponse, HistoryMessage, ClientContext

__all__ = ["app", "ChatRequest", "ChatResponse", "HistoryMessage", "ClientContext"]
