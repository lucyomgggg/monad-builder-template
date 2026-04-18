from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TelosClient:
    def __init__(
        self,
        base_url: str,
        monad_id: str,
        *,
        timeout: float,
        retry_max: int,
        retry_sleep: float,
    ) -> None:
        self._monad_id = monad_id
        self._retry_max = retry_max
        self._retry_sleep = retry_sleep
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout),
            headers={"Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def _request_json(self, method: str, path: str, json_body: dict[str, Any]) -> httpx.Response | None:
        attempt = 0
        while True:
            try:
                response = self._client.request(method, path, json=json_body)
            except httpx.RequestError as exc:
                log.error("telos %s %s: %s", method, path, exc)
                return None
            if response.status_code == 429 and attempt < self._retry_max:
                attempt += 1
                log.warning(
                    "telos 429; sleeping %ss (attempt %s/%s)",
                    self._retry_sleep,
                    attempt,
                    self._retry_max,
                )
                time.sleep(self._retry_sleep)
                continue
            return response

    def search(
        self,
        query: str,
        limit: int,
        *,
        kind: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
    ) -> list[dict]:
        payload: dict[str, Any] = {
            "monad_id": self._monad_id,
            "query": query,
            "limit": limit,
        }
        if kind is not None:
            payload["kind"] = kind
        if scope_kind is not None:
            payload["scope_kind"] = scope_kind
        if scope_id is not None:
            payload["scope_id"] = scope_id
        response = self._request_json("POST", "/api/v1/search", payload)
        if response is None or not (200 <= response.status_code < 300):
            return []
        data = response.json()
        return data.get("results") or []

    def write(
        self,
        content: str,
        parent_ids: list[str] | None = None,
        *,
        kind: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {
            "monad_id": self._monad_id,
            "content": content,
            "parent_ids": parent_ids or [],
        }
        if kind is not None:
            payload["kind"] = kind
        if scope_kind is not None:
            payload["scope_kind"] = scope_kind
        if scope_id is not None:
            payload["scope_id"] = scope_id
        if metadata is not None:
            payload["metadata"] = metadata
        response = self._request_json("POST", "/api/v1/write", payload)
        if response is None or response.status_code == 413:
            return None
        if not (200 <= response.status_code < 300):
            return None
        data = response.json()
        node_id = str(data.get("id", ""))
        return node_id or None

    def reflect(self, limit: int = 5) -> list[dict]:
        """Retrieve this monad's own recent contributions via semantic search."""
        return self.search(f"recent contributions by {self._monad_id}", limit)

    def stats_nodes(self) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = self._client.get("/api/v1/stats/nodes")
            except httpx.RequestError as exc:
                log.error("telos GET /api/v1/stats/nodes: %s", exc)
                return {"error": str(exc)}
            if response.status_code == 429 and attempt < self._retry_max:
                attempt += 1
                log.warning(
                    "telos 429; sleeping %ss (attempt %s/%s)",
                    self._retry_sleep,
                    attempt,
                    self._retry_max,
                )
                time.sleep(self._retry_sleep)
                continue
            if not (200 <= response.status_code < 300):
                return {
                    "error": f"HTTP {response.status_code}",
                    "body_prefix": (response.text or "")[:500],
                }
            try:
                data = response.json()
            except ValueError:
                return {"error": "invalid JSON in stats response"}
            return data if isinstance(data, dict) else {"data": data}
