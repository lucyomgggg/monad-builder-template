from __future__ import annotations

import json
from typing import Any

import httpx

from builder_runtime.utils import truncate

ALLOWED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def fetch_url_allowed(url: str, allowed_hosts: list[str] | None) -> bool:
    if not allowed_hosts:
        return True
    try:
        host = httpx.URL(url).host
    except Exception:
        return False
    return host in allowed_hosts


def sanitize_outbound_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    headers: dict[str, str] = {}
    for index, (key, value) in enumerate(raw.items()):
        if index >= 40:
            break
        key_str = str(key).strip()
        value_str = str(value)
        if not key_str or len(key_str) > 128 or len(value_str) > 8192:
            continue
        if key_str.lower() == "host":
            continue
        headers[key_str] = value_str
    return headers


def execute_http_request(
    url: str,
    method: str,
    cfg: dict[str, Any],
    allowed_hosts: list[str] | None,
    *,
    json_body: Any | None = None,
    text_body: str | None = None,
    headers_raw: Any = None,
) -> dict[str, Any]:
    if not fetch_url_allowed(url, allowed_hosts):
        return {"error": "host not in fetch_allowed_hosts"}
    method_name = method.upper().strip()
    if method_name not in ALLOWED_HTTP_METHODS:
        return {"error": f"method must be one of {sorted(ALLOWED_HTTP_METHODS)}"}
    timeout = float(cfg["http_get_timeout_sec"])
    max_chars = int(cfg["http_get_max_response_chars"])
    max_body = int(cfg["http_request_max_body_chars"])
    headers = sanitize_outbound_headers(headers_raw)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            if method_name == "GET":
                response = client.get(url, headers=headers or None)
            else:
                if json_body is not None:
                    if not isinstance(json_body, (dict, list)):
                        return {"error": "json must be an object or array"}
                    serialized = json.dumps(json_body, ensure_ascii=False)
                    if len(serialized) > max_body:
                        return {"error": f"JSON body exceeds http_request_max_body_chars ({max_body})"}
                    response = client.request(method_name, url, headers=headers or None, json=json_body)
                elif text_body is not None:
                    body = text_body.encode("utf-8")
                    if len(body) > max_body:
                        return {"error": f"body exceeds http_request_max_body_chars ({max_body})"}
                    response = client.request(method_name, url, headers=headers or None, content=body)
                else:
                    response = client.request(method_name, url, headers=headers or None)
        return {
            "status_code": response.status_code,
            "body_prefix": truncate(response.text, max_chars),
        }
    except httpx.RequestError as exc:
        return {"error": str(exc)}
