from __future__ import annotations

__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        # Lazy import to avoid loading full API/runtime when importing utility modules.
        from .api import app as _app

        return _app
    raise AttributeError(name)
