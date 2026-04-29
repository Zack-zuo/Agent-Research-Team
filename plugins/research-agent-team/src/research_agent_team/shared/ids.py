from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    cleaned = prefix.strip().replace("_", "-")
    if not cleaned:
        cleaned = "id"
    return f"{cleaned}-{uuid4().hex[:12]}"
