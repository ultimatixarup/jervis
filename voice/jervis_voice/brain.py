"""HTTP client for the brain on localhost:7777."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("jervis.voice.brain")


@dataclass(frozen=True)
class Reply:
    text: str
    pending_confirmation: str | None = None
    tool_calls: tuple[str, ...] = ()

    @property
    def awaiting_confirmation(self) -> bool:
        return self.pending_confirmation is not None


class BrainClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7777",
        session_id: str = "voice",
        timeout: float = 180.0,
        client: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.timeout = timeout
        # Injectable so tests can drive the real FastAPI app in-process instead of
        # standing up a socket. Typed loosely because starlette's TestClient is an
        # httpx.Client from a *different* httpx major version than the one installed.
        self._client: Any = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, object]:
        response = self._client.get(f"{self.base_url}/health")
        response.raise_for_status()
        return dict(response.json())

    def wait_until_ready(self, attempts: int = 40, delay: float = 0.5) -> bool:
        import time

        for _ in range(attempts):
            try:
                self.health()
                return True
            except (httpx.HTTPError, OSError):
                time.sleep(delay)
        return False

    def ask(self, text: str) -> Reply:
        return self._post("/ask", text)

    def confirm(self, text: str) -> Reply:
        return self._post("/confirm", text)

    def _post(self, path: str, text: str) -> Reply:
        # The timeout lives on the client, not the call: an injected client (a
        # starlette TestClient, say) rejects a per-request timeout.
        response = self._client.post(
            f"{self.base_url}{path}",
            json={"text": text, "session_id": self.session_id},
        )
        response.raise_for_status()
        body = response.json()
        return Reply(
            text=str(body.get("reply", "")),
            pending_confirmation=body.get("pending_confirmation"),
            tool_calls=tuple(body.get("tool_calls") or ()),
        )
