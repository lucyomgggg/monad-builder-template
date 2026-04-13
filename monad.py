"""
Telos "builder-class" monad runtime: Telos search/write/stats, outbound HTTP (GET or full request),
workspace file tools, grep/glob, Python execution, optional Stripe Checkout for pre-approved Price
IDs only (secret key never exposed to the model). Fork this template and drive behavior with
`config.yaml` (prompts, limits, `monad_id`, optional `stripe_checkout`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv
import litellm
from litellm import completion

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

litellm.set_verbose = False
for _litellm_logger_name in ("LiteLLM", "litellm", "litellm.cost_calculator"):
    logging.getLogger(_litellm_logger_name).setLevel(logging.WARNING)

_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _CONFIG_DIR / "config.yaml"
load_dotenv(_CONFIG_DIR / ".env", override=False)

_REQUIRED_KEYS = (
    "telos_base_url",
    "telos_timeout_sec",
    "telos_retry_max",
    "telos_retry_sleep_sec",
    "monad_id",
    "llm_model",
    "task",
    "interval_sec",
    "max_tool_rounds",
    "system_prompt",
    "tool_descriptions",
    "default_search_limit",
    "max_search_limit",
    "http_get_timeout_sec",
    "http_get_max_response_chars",
    "http_request_max_body_chars",
    "workspace_dir",
    "run_python_timeout_sec",
    "run_python_max_code_chars",
    "run_python_max_output_chars",
    "read_workspace_max_chars",
    "write_workspace_max_chars",
    "keep_snippet_files",
    "fetch_allowed_hosts",
    "list_workspace_max_depth",
    "list_workspace_max_entries",
    "list_workspace_default_depth",
    "run_python_max_stdin_chars",
    "grep_workspace_max_matches",
    "grep_workspace_max_file_bytes",
    "grep_workspace_max_output_chars",
    "grep_workspace_max_pattern_chars",
    "grep_workspace_max_files_scanned",
    "workspace_glob_max_files",
)

_TOOL_DESC_KEYS = (
    "telos_search",
    "telos_write",
    "telos_stats",
    "http_get",
    "http_request",
    "run_python",
    "read_workspace_file",
    "write_workspace_file",
    "list_workspace",
    "append_workspace_file",
    "delete_workspace_file",
    "grep_workspace",
    "workspace_glob",
    "create_workspace_dir",
    "runtime_info",
    "stripe_create_checkout_session",
)

_STRIPE_TOOL = "stripe_create_checkout_session"


def _stripe_checkout_enabled(cfg: dict[str, Any]) -> bool:
    sc = cfg.get("stripe_checkout")
    if not isinstance(sc, dict):
        return False
    # YAML boolean only; avoid truthy strings like "false"
    return sc.get("enabled") is True


def _stripe_metadata_clean(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    mk_re = re.compile(r"^[a-zA-Z0-9_]{1,40}$")
    for i, (k, v) in enumerate(raw.items()):
        if i >= 10:
            break
        ks = str(k).strip()
        if not mk_re.match(ks):
            continue
        vs = str(v)
        if len(vs) > 500:
            vs = vs[:500]
        out[ks] = vs
    return out


def _stripe_create_checkout(cfg: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if not _stripe_checkout_enabled(cfg):
        return {"error": "stripe_checkout.enabled is not true or block missing"}
    sc = cfg["stripe_checkout"]
    assert isinstance(sc, dict)
    sk = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not sk.startswith(("sk_test_", "sk_live_")):
        return {"error": "STRIPE_SECRET_KEY missing or not a Stripe secret key (sk_test_/sk_live_)"}

    price_id = str(args.get("price_id", "")).strip()
    allowed = [str(x).strip() for x in sc.get("allowed_price_ids", []) if str(x).strip()]
    if price_id not in allowed:
        return {"error": "price_id is not in stripe_checkout.allowed_price_ids"}

    modes = sc.get("allowed_modes", ["payment"])
    if not isinstance(modes, list) or not modes:
        return {"error": "stripe_checkout.allowed_modes must be a non-empty list"}
    modes_s = [str(m).strip() for m in modes if str(m).strip() in ("payment", "subscription", "setup")]
    if not modes_s:
        return {"error": "allowed_modes entries must be payment, subscription, and/or setup"}
    mode = str(args.get("mode", modes_s[0])).strip()
    if mode not in modes_s:
        return {"error": f"mode must be one of {modes_s}"}

    max_q = int(sc.get("max_quantity", 99))
    max_q = max(1, min(max_q, 999))
    try:
        qty = int(args.get("quantity", 1))
    except (TypeError, ValueError):
        return {"error": "quantity must be an integer"}
    qty = max(1, min(qty, max_q))

    success_url = str(sc.get("success_url", "")).strip()
    cancel_url = str(sc.get("cancel_url", "")).strip()
    if not success_url.startswith(("http://", "https://")) or not cancel_url.startswith(("http://", "https://")):
        return {"error": "success_url and cancel_url must be http(s) URLs in config"}

    cref_raw = args.get("client_reference_id")
    cref: str | None = None
    if cref_raw is not None:
        cref = str(cref_raw).strip()[:200] or None

    meta = _stripe_metadata_clean(args.get("metadata"))

    form: dict[str, str] = {
        "mode": mode,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": str(qty),
    }
    if cref:
        form["client_reference_id"] = cref
    for mk, mv in meta.items():
        form[f"metadata[{mk}]"] = mv

    timeout = min(60.0, float(cfg["http_get_timeout_sec"]))
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=form,
                headers={"Authorization": f"Bearer {sk}"},
            )
    except httpx.RequestError as e:
        return {"error": f"Stripe request failed: {e}"}

    try:
        body = r.json()
    except ValueError:
        return {"error": "Stripe returned non-JSON", "status_code": r.status_code}

    if not isinstance(body, dict):
        return {"error": "unexpected Stripe response", "status_code": r.status_code}

    if not (200 <= r.status_code < 300):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message", str(err))
        else:
            msg = str(err or body)
        return {"error": "Stripe API error", "message": msg, "status_code": r.status_code}

    url = body.get("url")
    sid = body.get("id")
    if not url or not isinstance(url, str):
        return {"error": "Stripe response missing checkout url", "status_code": r.status_code}
    return {"ok": True, "checkout_url": url, "session_id": sid if isinstance(sid, str) else None}


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
                resp = self._client.request(method, path, json=json_body)
            except httpx.RequestError as exc:
                log.error("telos %s %s: %s", method, path, exc)
                return None
            if resp.status_code == 429 and attempt < self._retry_max:
                attempt += 1
                log.warning(
                    "telos 429; sleeping %ss (attempt %s/%s)",
                    self._retry_sleep,
                    attempt,
                    self._retry_max,
                )
                time.sleep(self._retry_sleep)
                continue
            return resp

    def search(self, query: str, limit: int) -> list[dict]:
        resp = self._request_json(
            "POST",
            "/api/v1/search",
            {"monad_id": self._monad_id, "query": query, "limit": limit},
        )
        if resp is None or not (200 <= resp.status_code < 300):
            return []
        data = resp.json()
        return data.get("results") or []

    def write(self, content: str, parent_ids: list[str] | None = None) -> str | None:
        resp = self._request_json(
            "POST",
            "/api/v1/write",
            {
                "monad_id": self._monad_id,
                "content": content,
                "parent_ids": parent_ids or [],
            },
        )
        if resp is None or resp.status_code == 413:
            return None
        if not (200 <= resp.status_code < 300):
            return None
        data = resp.json()
        nid = str(data.get("id", ""))
        return nid or None

    def stats_nodes(self) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = self._client.get("/api/v1/stats/nodes")
            except httpx.RequestError as exc:
                log.error("telos GET /api/v1/stats/nodes: %s", exc)
                return {"error": str(exc)}
            if resp.status_code == 429 and attempt < self._retry_max:
                attempt += 1
                log.warning(
                    "telos 429; sleeping %ss (attempt %s/%s)",
                    self._retry_sleep,
                    attempt,
                    self._retry_max,
                )
                time.sleep(self._retry_sleep)
                continue
            if not (200 <= resp.status_code < 300):
                return {
                    "error": f"HTTP {resp.status_code}",
                    "body_prefix": (resp.text or "")[:500],
                }
            try:
                data = resp.json()
            except ValueError:
                return {"error": "invalid JSON in stats response"}
            return data if isinstance(data, dict) else {"data": data}


def load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        log.error("config.yaml not found: %s", _CONFIG_PATH)
        sys.exit(1)
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        log.error("config.yaml parse error: %s", e)
        sys.exit(1)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        log.error("config.yaml must be a mapping at the top level")
        sys.exit(1)
    return raw


def validate_config(cfg: dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        log.error("config.yaml missing required keys: %s", missing)
        sys.exit(1)

    td = cfg["tool_descriptions"]
    if not isinstance(td, dict):
        log.error("tool_descriptions must be a mapping")
        sys.exit(1)
    for k in _TOOL_DESC_KEYS:
        if k == _STRIPE_TOOL and not _stripe_checkout_enabled(cfg):
            continue
        if k not in td or not str(td[k]).strip():
            log.error("tool_descriptions.%s is empty", k)
            sys.exit(1)

    if _stripe_checkout_enabled(cfg):
        sc = cfg["stripe_checkout"]
        assert isinstance(sc, dict)
        if not str(sc.get("success_url", "")).strip().startswith(("http://", "https://")):
            log.error("stripe_checkout.success_url must be an http(s) URL")
            sys.exit(1)
        if not str(sc.get("cancel_url", "")).strip().startswith(("http://", "https://")):
            log.error("stripe_checkout.cancel_url must be an http(s) URL")
            sys.exit(1)
        raw_prices = sc.get("allowed_price_ids")
        if not isinstance(raw_prices, list) or not [str(x).strip() for x in raw_prices if str(x).strip()]:
            log.error("stripe_checkout.allowed_price_ids must be a non-empty list of Stripe Price IDs")
            sys.exit(1)
        for pid in raw_prices:
            ps = str(pid).strip()
            if ps and not ps.startswith("price_"):
                log.error("stripe_checkout allowed price id must start with price_: %s", ps[:20])
                sys.exit(1)
        raw_modes = sc.get("allowed_modes", ["payment"])
        if not isinstance(raw_modes, list) or not raw_modes:
            log.error("stripe_checkout.allowed_modes must be a non-empty list")
            sys.exit(1)
        for m in raw_modes:
            if str(m).strip() not in ("payment", "subscription", "setup"):
                log.error("stripe_checkout.allowed_modes must use only payment, subscription, setup")
                sys.exit(1)
        mq = sc.get("max_quantity", 99)
        try:
            mq_i = int(mq)
        except (TypeError, ValueError):
            log.error("stripe_checkout.max_quantity must be an integer")
            sys.exit(1)
        if mq_i < 1 or mq_i > 999:
            log.error("stripe_checkout.max_quantity must be between 1 and 999")
            sys.exit(1)
        sk = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if not sk.startswith(("sk_test_", "sk_live_")):
            log.error("STRIPE_SECRET_KEY must be set when stripe_checkout.enabled (sk_test_ or sk_live_)")
            sys.exit(1)

    if "fetch_allowed_hosts" not in cfg or not isinstance(cfg["fetch_allowed_hosts"], list):
        log.error("fetch_allowed_hosts must be a list (empty means allow all hosts)")
        sys.exit(1)

    if not isinstance(cfg["keep_snippet_files"], bool):
        log.error("keep_snippet_files must be a boolean")
        sys.exit(1)

    task = str(cfg["task"]).strip()
    if not task:
        log.error("task must be non-empty")
        sys.exit(1)

    ws_dir = str(cfg["workspace_dir"]).strip()
    if not ws_dir or ".." in ws_dir or Path(ws_dir).is_absolute():
        log.error("workspace_dir must be a non-empty relative path without '..'")
        sys.exit(1)

    try:
        int(cfg["interval_sec"])
        int(cfg["max_tool_rounds"])
        int(cfg["default_search_limit"])
        int(cfg["max_search_limit"])
        float(cfg["telos_timeout_sec"])
        int(cfg["telos_retry_max"])
        float(cfg["telos_retry_sleep_sec"])
        float(cfg["http_get_timeout_sec"])
        int(cfg["http_get_max_response_chars"])
        float(cfg["run_python_timeout_sec"])
        int(cfg["run_python_max_code_chars"])
        int(cfg["run_python_max_output_chars"])
        int(cfg["read_workspace_max_chars"])
        int(cfg["write_workspace_max_chars"])
        int(cfg["list_workspace_max_depth"])
        int(cfg["list_workspace_max_entries"])
        int(cfg["list_workspace_default_depth"])
        int(cfg["run_python_max_stdin_chars"])
        int(cfg["http_request_max_body_chars"])
        int(cfg["grep_workspace_max_matches"])
        int(cfg["grep_workspace_max_file_bytes"])
        int(cfg["grep_workspace_max_output_chars"])
        int(cfg["grep_workspace_max_pattern_chars"])
        int(cfg["grep_workspace_max_files_scanned"])
        int(cfg["workspace_glob_max_files"])
    except (TypeError, ValueError) as e:
        log.error("invalid numeric field: %s", e)
        sys.exit(1)

    if int(cfg["grep_workspace_max_matches"]) < 1:
        log.error("grep_workspace_max_matches must be >= 1")
        sys.exit(1)
    if int(cfg["grep_workspace_max_files_scanned"]) < 1:
        log.error("grep_workspace_max_files_scanned must be >= 1")
        sys.exit(1)
    if int(cfg["workspace_glob_max_files"]) < 1:
        log.error("workspace_glob_max_files must be >= 1")
        sys.exit(1)

    if int(cfg["list_workspace_max_depth"]) < 0 or int(cfg["list_workspace_default_depth"]) < 0:
        log.error("list_workspace depths must be >= 0")
        sys.exit(1)
    if int(cfg["list_workspace_default_depth"]) > int(cfg["list_workspace_max_depth"]):
        log.error("list_workspace_default_depth must be <= list_workspace_max_depth")
        sys.exit(1)
    if int(cfg["list_workspace_max_entries"]) < 1:
        log.error("list_workspace_max_entries must be >= 1")
        sys.exit(1)

    if not str(cfg["telos_base_url"]).strip():
        log.error("telos_base_url is empty")
        sys.exit(1)

    tc = cfg.get("tool_choice", "auto")
    if isinstance(tc, str) and not str(tc).strip():
        log.error("tool_choice must not be empty when set as string")
        sys.exit(1)
    if not isinstance(tc, (str, dict)):
        log.error("tool_choice must be a string (e.g. auto, required) or an OpenAI-style object")
        sys.exit(1)

    if "parallel_tool_calls" in cfg and not isinstance(cfg["parallel_tool_calls"], bool):
        log.error("parallel_tool_calls must be a boolean when set")
        sys.exit(1)


def _workspace_root(cfg: dict[str, Any]) -> Path:
    rel = str(cfg["workspace_dir"]).strip() or "workspace"
    root = (_CONFIG_DIR / rel).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_workspace_path(workspace: Path, relative: str) -> Path | None:
    rel = relative.strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    candidate = (workspace / rel).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate


def _workspace_base_path(workspace: Path, relative: str) -> Path | None:
    rel = relative.strip().replace("\\", "/")
    if rel in ("", "."):
        return workspace
    return _safe_workspace_path(workspace, rel)


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "\n...(truncated)"


def build_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    td = cfg["tool_descriptions"]
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "telos_search",
                "description": str(td["telos_search"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "telos_write",
                "description": str(td["telos_write"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "parent_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "telos_stats",
                "description": str(td["telos_stats"]),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "http_get",
                "description": str(td["http_get"]),
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "http_request",
                "description": str(td["http_request"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "description": "HTTP method: GET, POST, PUT, PATCH, or DELETE.",
                        },
                        "url": {"type": "string"},
                        "json": {
                            "description": "Optional JSON-serializable object or array (sets JSON body for non-GET).",
                        },
                        "body": {
                            "type": "string",
                            "description": "Optional raw UTF-8 body when json is not used (non-GET).",
                        },
                        "headers": {
                            "type": "object",
                            "description": "Optional string headers (Host is ignored).",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_python",
                "description": str(td["run_python"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "stdin": {"type": "string", "description": "Optional UTF-8 text passed to the process stdin."},
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_workspace_file",
                "description": str(td["read_workspace_file"]),
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_workspace_file",
                "description": str(td["write_workspace_file"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_workspace",
                "description": str(td["list_workspace"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Subdirectory under workspace; empty string lists from workspace root.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Max tree depth below path (0 = immediate children only).",
                        },
                        "max_entries": {"type": "integer"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "append_workspace_file",
                "description": str(td["append_workspace_file"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_workspace_file",
                "description": str(td["delete_workspace_file"]),
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep_workspace",
                "description": str(td["grep_workspace"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Python regex (multiline)."},
                        "path": {
                            "type": "string",
                            "description": "Optional subdirectory under workspace; empty = whole workspace.",
                        },
                        "max_matches": {"type": "integer"},
                        "ignore_case": {"type": "boolean"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "workspace_glob",
                "description": str(td["workspace_glob"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "glob_pattern": {
                            "type": "string",
                            "description": "Glob relative to path, e.g. **/*.py or *.md",
                        },
                        "path": {
                            "type": "string",
                            "description": "Optional subdirectory under workspace; empty = workspace root.",
                        },
                    },
                    "required": ["glob_pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_workspace_dir",
                "description": str(td["create_workspace_dir"]),
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative directory to create."}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "runtime_info",
                "description": str(td["runtime_info"]),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]
    if _stripe_checkout_enabled(cfg):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": _STRIPE_TOOL,
                    "description": str(td[_STRIPE_TOOL]),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "price_id": {
                                "type": "string",
                                "description": "Stripe Price ID; must be one of stripe_checkout.allowed_price_ids in config.",
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Line item quantity (capped by stripe_checkout.max_quantity).",
                            },
                            "mode": {
                                "type": "string",
                                "description": "Checkout mode: payment, subscription, or setup (must be allowed in config).",
                            },
                            "client_reference_id": {
                                "type": "string",
                                "description": "Optional idempotency / correlation id (max 200 chars).",
                            },
                            "metadata": {
                                "type": "object",
                                "description": "Optional Stripe metadata (<=10 keys, [a-zA-Z0-9_]+, values <=500 chars).",
                            },
                        },
                        "required": ["price_id"],
                    },
                },
            }
        )
    return tools


_ALLOWED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def _fetch_url_allowed(url: str, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    try:
        host = httpx.URL(url).host
    except Exception:
        return False
    return host in allowed


def _sanitize_outbound_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for i, (k, v) in enumerate(raw.items()):
        if i >= 40:
            break
        ks = str(k).strip()
        vs = str(v)
        if not ks or len(ks) > 128 or len(vs) > 8192:
            continue
        if ks.lower() == "host":
            continue
        out[ks] = vs
    return out


def _http_execute(
    url: str,
    method: str,
    cfg: dict[str, Any],
    allow: list[str] | None,
    *,
    json_body: Any | None = None,
    text_body: str | None = None,
    headers_raw: Any = None,
) -> dict[str, Any]:
    if not _fetch_url_allowed(url, allow):
        return {"error": "host not in fetch_allowed_hosts"}
    m = method.upper().strip()
    if m not in _ALLOWED_HTTP_METHODS:
        return {"error": f"method must be one of {sorted(_ALLOWED_HTTP_METHODS)}"}
    timeout = float(cfg["http_get_timeout_sec"])
    max_chars = int(cfg["http_get_max_response_chars"])
    max_body = int(cfg["http_request_max_body_chars"])
    hdrs = _sanitize_outbound_headers(headers_raw)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            if m == "GET":
                r = client.get(url, headers=hdrs or None)
            else:
                if json_body is not None:
                    if not isinstance(json_body, (dict, list)):
                        return {"error": "json must be an object or array"}
                    ser = json.dumps(json_body, ensure_ascii=False)
                    if len(ser) > max_body:
                        return {"error": f"JSON body exceeds http_request_max_body_chars ({max_body})"}
                    r = client.request(m, url, headers=hdrs or None, json=json_body)
                elif text_body is not None:
                    b = text_body.encode("utf-8")
                    if len(b) > max_body:
                        return {"error": f"body exceeds http_request_max_body_chars ({max_body})"}
                    r = client.request(m, url, headers=hdrs or None, content=b)
                else:
                    r = client.request(m, url, headers=hdrs or None)
        text = _truncate(r.text, max_chars)
        return {"status_code": r.status_code, "body_prefix": text}
    except httpx.RequestError as e:
        return {"error": str(e)}


def run_tools(
    telos: TelosClient,
    cfg: dict[str, Any],
    name: str,
    arguments: str,
) -> str:
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid JSON arguments: {e}"}, ensure_ascii=False)

    allowed_hosts = cfg["fetch_allowed_hosts"]
    allow = [str(h) for h in allowed_hosts] if allowed_hosts else None

    if name == "telos_search":
        q = args.get("query", "")
        default_lim = int(cfg["default_search_limit"])
        max_lim = int(cfg["max_search_limit"])
        lim = int(args.get("limit", default_lim))
        lim = max(1, min(lim, max_lim))
        hits = telos.search(str(q), lim)
        return json.dumps(hits, ensure_ascii=False)

    if name == "telos_write":
        content = str(args.get("content", ""))
        pids = args.get("parent_ids")
        if not isinstance(pids, list):
            pids = []
        pids = [str(x) for x in pids]
        nid = telos.write(content, pids)
        return json.dumps({"id": nid, "ok": nid is not None}, ensure_ascii=False)

    if name == "telos_stats":
        st = telos.stats_nodes()
        return json.dumps(st, ensure_ascii=False)

    if name == _STRIPE_TOOL:
        out = _stripe_create_checkout(cfg, args)
        return json.dumps(out, ensure_ascii=False)

    if name == "http_get":
        url = str(args.get("url", ""))
        result = _http_execute(url, "GET", cfg, allow)
        return json.dumps(result, ensure_ascii=False)

    if name == "http_request":
        url = str(args.get("url", ""))
        method = str(args.get("method", "GET"))
        json_b = args.get("json", None)
        body_raw = args.get("body", None)
        hdrs = args.get("headers")
        if json_b is not None and body_raw is not None:
            return json.dumps({"error": "use only one of json or body"}, ensure_ascii=False)
        text_body = str(body_raw) if body_raw is not None else None
        result = _http_execute(
            url,
            method,
            cfg,
            allow,
            json_body=json_b,
            text_body=text_body,
            headers_raw=hdrs,
        )
        return json.dumps(result, ensure_ascii=False)

    workspace = _workspace_root(cfg)

    if name == "runtime_info":
        info = {
            "utc_iso": datetime.now(timezone.utc).isoformat(),
            "python_version": sys.version.split()[0],
            "full_python_version": sys.version,
            "platform": sys.platform,
            "workspace_resolved": str(workspace.resolve()),
        }
        return json.dumps(info, ensure_ascii=False)

    if name == "create_workspace_dir":
        rel = str(args.get("path", ""))
        path = _safe_workspace_path(workspace, rel)
        if path is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"ok": True, "path": rel}, ensure_ascii=False)

    if name == "workspace_glob":
        gpat = str(args.get("glob_pattern", ""))
        if not gpat.strip() or ".." in gpat or gpat.startswith(("/", "\\")):
            return json.dumps({"error": "invalid glob_pattern"}, ensure_ascii=False)
        rel_root = str(args.get("path", ""))
        base = _workspace_base_path(workspace, rel_root)
        if base is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        if not base.exists():
            return json.dumps({"error": "path does not exist"}, ensure_ascii=False)
        if not base.is_dir():
            return json.dumps({"error": "path must be a directory for workspace_glob"}, ensure_ascii=False)
        maxf = int(cfg["workspace_glob_max_files"])
        paths: list[str] = []
        truncated = False
        try:
            for p in sorted(base.glob(gpat)):
                if p.is_dir():
                    continue
                try:
                    p.resolve().relative_to(workspace.resolve())
                except ValueError:
                    continue
                rp = str(p.relative_to(workspace)).replace("\\", "/")
                paths.append(rp)
                if len(paths) >= maxf:
                    truncated = True
                    break
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"paths": paths, "truncated": truncated}, ensure_ascii=False)

    if name == "grep_workspace":
        pat = str(args.get("pattern", ""))
        max_pat = int(cfg["grep_workspace_max_pattern_chars"])
        if len(pat) > max_pat:
            return json.dumps({"error": f"pattern exceeds grep_workspace_max_pattern_chars ({max_pat})"}, ensure_ascii=False)
        ign = bool(args.get("ignore_case", False))
        flags = re.MULTILINE | (re.IGNORECASE if ign else 0)
        try:
            rx = re.compile(pat, flags)
        except re.error as e:
            return json.dumps({"error": f"regex error: {e}"}, ensure_ascii=False)
        base_rel = str(args.get("path", ""))
        base = _workspace_base_path(workspace, base_rel)
        if base is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        if not base.exists():
            return json.dumps({"error": "path does not exist"}, ensure_ascii=False)
        max_matches = int(args.get("max_matches", cfg["grep_workspace_max_matches"]))
        max_matches = max(1, min(max_matches, int(cfg["grep_workspace_max_matches"])))
        max_file = int(cfg["grep_workspace_max_file_bytes"])
        max_scan = int(cfg["grep_workspace_max_files_scanned"])
        max_out = int(cfg["grep_workspace_max_output_chars"])
        matches: list[dict[str, Any]] = []
        files_scanned = 0
        truncated_matches = False
        truncated_scan = False
        try:
            iter_paths = [base] if base.is_file() else sorted(base.rglob("*"))
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        for p in iter_paths:
            if len(matches) >= max_matches:
                truncated_matches = True
                break
            if not p.is_file():
                continue
            if p.name.startswith("_snippet_") and p.suffix == ".py":
                continue
            files_scanned += 1
            if files_scanned > max_scan:
                truncated_scan = True
                break
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz > max_file:
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
            rel_rp = str(p.relative_to(workspace)).replace("\\", "/")
            for li, line in enumerate(text.splitlines(), start=1):
                if len(matches) >= max_matches:
                    truncated_matches = True
                    break
                if rx.search(line):
                    matches.append({"path": rel_rp, "line": li, "text": line[:500]})
            if truncated_matches:
                break
        out_obj: dict[str, Any] = {
            "matches": matches,
            "files_scanned": files_scanned,
            "truncated_matches": truncated_matches,
            "truncated_scan": truncated_scan,
        }
        while True:
            ser = json.dumps(out_obj, ensure_ascii=False)
            if len(ser) <= max_out:
                return ser
            if not out_obj["matches"]:
                return _truncate(ser, max_out)
            out_obj["matches"].pop()
            out_obj["truncated_output"] = True

    if name == "read_workspace_file":
        rel = str(args.get("path", ""))
        max_chars = int(cfg["read_workspace_max_chars"])
        path = _safe_workspace_path(workspace, rel)
        if path is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        if not path.is_file():
            return json.dumps({"error": "not a file or missing"}, ensure_ascii=False)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"path": rel, "content": _truncate(text, max_chars)}, ensure_ascii=False)

    if name == "write_workspace_file":
        rel = str(args.get("path", ""))
        content = str(args.get("content", ""))
        max_write = int(cfg["write_workspace_max_chars"])
        if len(content) > max_write:
            return json.dumps(
                {"error": f"content exceeds write_workspace_max_chars ({max_write})"},
                ensure_ascii=False,
            )
        path = _safe_workspace_path(workspace, rel)
        if path is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"ok": True, "path": rel, "bytes": len(content.encode("utf-8"))}, ensure_ascii=False)

    if name == "list_workspace":
        rel = str(args.get("path", ""))
        max_dep_cfg = int(cfg["list_workspace_max_depth"])
        max_ent_cfg = int(cfg["list_workspace_max_entries"])
        def_dep = int(cfg["list_workspace_default_depth"])
        dep = int(args.get("max_depth", def_dep))
        ent = int(args.get("max_entries", max_ent_cfg))
        dep = max(0, min(dep, max_dep_cfg))
        ent = max(1, min(ent, max_ent_cfg))
        base = _workspace_base_path(workspace, rel)
        if base is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        if not base.exists():
            return json.dumps({"error": "path does not exist"}, ensure_ascii=False)
        if base.is_file():
            try:
                sz = base.stat().st_size
            except OSError as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)
            rp = str(base.relative_to(workspace)).replace("\\", "/")
            return json.dumps(
                {"entries": [{"path": rp, "kind": "file", "size": sz}], "truncated": False},
                ensure_ascii=False,
            )
        entries: list[dict[str, Any]] = []
        truncated = False
        try:
            for p in sorted(base.rglob("*")):
                if len(entries) >= ent:
                    truncated = True
                    break
                rel_to_base = p.relative_to(base)
                depth = len(rel_to_base.parts) - 1
                if depth > dep:
                    continue
                rp = str(p.relative_to(workspace)).replace("\\", "/")
                if p.is_dir():
                    entries.append({"path": rp, "kind": "dir"})
                else:
                    try:
                        sz = p.stat().st_size
                    except OSError:
                        sz = None
                    entries.append({"path": rp, "kind": "file", "size": sz})
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"entries": entries, "truncated": truncated}, ensure_ascii=False)

    if name == "append_workspace_file":
        rel = str(args.get("path", ""))
        content = str(args.get("content", ""))
        max_write = int(cfg["write_workspace_max_chars"])
        if len(content) > max_write:
            return json.dumps(
                {"error": f"content exceeds write_workspace_max_chars ({max_write})"},
                ensure_ascii=False,
            )
        path = _safe_workspace_path(workspace, rel)
        if path is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"ok": True, "path": rel, "appended_bytes": len(content.encode("utf-8"))}, ensure_ascii=False)

    if name == "delete_workspace_file":
        rel = str(args.get("path", ""))
        path = _safe_workspace_path(workspace, rel)
        if path is None:
            return json.dumps({"error": "invalid path"}, ensure_ascii=False)
        if not path.is_file():
            return json.dumps({"error": "not a file or missing"}, ensure_ascii=False)
        try:
            path.unlink()
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return json.dumps({"ok": True, "path": rel}, ensure_ascii=False)

    if name == "run_python":
        code = str(args.get("code", ""))
        max_code = int(cfg["run_python_max_code_chars"])
        if len(code) > max_code:
            return json.dumps(
                {"error": f"code exceeds run_python_max_code_chars ({max_code})"},
                ensure_ascii=False,
            )
        stdin_raw = args.get("stdin", None)
        inp: str | None = None
        if stdin_raw is not None:
            inp = str(stdin_raw)
            lim_stdin = int(cfg["run_python_max_stdin_chars"])
            if len(inp) > lim_stdin:
                return json.dumps(
                    {"error": f"stdin exceeds run_python_max_stdin_chars ({lim_stdin})"},
                    ensure_ascii=False,
                )
        timeout = float(cfg["run_python_timeout_sec"])
        max_out = int(cfg["run_python_max_output_chars"])
        snippet = workspace / f"_snippet_{uuid.uuid4().hex}.py"
        try:
            snippet.write_text(code, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(snippet)],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                input=inp,
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
            out = _truncate((proc.stdout or ""), max_out)
            err = _truncate((proc.stderr or ""), max_out)
            return json.dumps(
                {
                    "returncode": proc.returncode,
                    "stdout": out,
                    "stderr": err,
                    "snippet": snippet.name,
                },
                ensure_ascii=False,
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "timeout", "timeout_sec": timeout}, ensure_ascii=False)
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        finally:
            if not bool(cfg["keep_snippet_files"]):
                try:
                    snippet.unlink(missing_ok=True)
                except OSError:
                    pass

    return json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False)


def _assistant_message_to_dict(msg: Any) -> dict[str, Any]:
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    d: dict[str, Any] = {"role": "assistant", "content": getattr(msg, "content", None)}
    tc = getattr(msg, "tool_calls", None)
    if tc:
        out = []
        for c in tc:
            if hasattr(c, "model_dump"):
                out.append(c.model_dump())
            else:
                fn = getattr(c, "function", c)
                out.append(
                    {
                        "id": getattr(c, "id", ""),
                        "type": "function",
                        "function": {
                            "name": getattr(fn, "name", ""),
                            "arguments": getattr(fn, "arguments", "{}"),
                        },
                    }
                )
        d["tool_calls"] = out
    return d


def _litellm_completion_with_retries(**kwargs: Any) -> Any:
    for attempt in range(3):
        try:
            return completion(**kwargs)
        except Exception as e:
            if attempt >= 2:
                raise
            wait = 2.0 * (attempt + 1)
            log.warning("LLM completion failed (attempt %s/3), retrying in %ss: %s", attempt + 1, wait, e)
            time.sleep(wait)


def _tool_choice_for_round(cfg: dict[str, Any], round_i: int) -> str | dict[str, Any]:
    raw = cfg.get("tool_choice", "auto")
    if round_i > 0:
        return "auto"
    if isinstance(raw, dict):
        return raw
    s = str(raw).strip()
    return s if s else "auto"


def agent_turn(
    telos: TelosClient,
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    model: str,
) -> None:
    tools = build_tools(cfg)
    max_rounds = int(cfg["max_tool_rounds"])
    parallel = cfg.get("parallel_tool_calls", True)
    if not isinstance(parallel, bool):
        parallel = True

    for round_i in range(max_rounds):
        tool_choice = _tool_choice_for_round(cfg, round_i)
        res = _litellm_completion_with_retries(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel,
        )
        choice = res.choices[0]
        msg = choice.message
        d = _assistant_message_to_dict(msg)
        messages.append(d)

        tool_calls = getattr(msg, "tool_calls", None) or d.get("tool_calls")
        if not tool_calls:
            log.info("assistant: %s", (d.get("content") or "")[:500])
            return

        for tc in tool_calls:
            if isinstance(tc, dict):
                tid = tc.get("id", "")
                fn = tc.get("function", {})
                name = fn.get("name", "")
                arguments = fn.get("arguments", "{}")
            else:
                tid = getattr(tc, "id", "")
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", "") if fn else ""
                arguments = getattr(fn, "arguments", "{}") if fn else "{}"

            payload = run_tools(telos, cfg, name, arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": payload,
                }
            )
        log.debug("tool round %s done", round_i + 1)

    log.warning("reached max_tool_rounds (%s)", max_rounds)


def run_once(cfg: dict[str, Any]) -> int:
    validate_config(cfg)

    base = str(cfg["telos_base_url"]).rstrip("/")
    monad_id = str(cfg["monad_id"])
    model = str(cfg["llm_model"])
    task = str(cfg["task"]).strip()
    interval = int(cfg["interval_sec"])
    system = str(cfg["system_prompt"])

    telos = TelosClient(
        base_url=base,
        monad_id=monad_id,
        timeout=float(cfg["telos_timeout_sec"]),
        retry_max=int(cfg["telos_retry_max"]),
        retry_sleep=float(cfg["telos_retry_sleep_sec"]),
    )
    try:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        agent_turn(telos, cfg, messages, model)
    finally:
        telos.close()

    return interval


def main() -> None:
    cfg0 = load_config()
    log.info("%s starting", str(cfg0.get("monad_id", "monad")))
    while True:
        cfg = load_config()
        try:
            interval = run_once(cfg)
        except Exception:
            log.exception("run_once error")
            interval = int(cfg["interval_sec"])
        time.sleep(interval)


if __name__ == "__main__":
    main()
