import json
from __future__ import annotations

from typing import Any

from httpx import AsyncClient  # noqa: F401
from httpx import Client as BaseClient  # noqa: F401


class SyncClient(BaseClient):
    def aclose(self) -> None:
        self.close()


def sanitize_param(param: Any) -> str:
    param_str = json.dumps(param)
    reserved_chars = ".,:()"
    if any(char in param_str for char in reserved_chars):
        return f'"{param_str}"'
    return param_str


def sanitize_pattern_param(pattern: str) -> str:
    return sanitize_param(pattern.replace("%", "*"))
