from __future__ import annotations

import json
import logging
from typing import Any

from builder_runtime.http_tools import execute_http_request
from builder_runtime.telos import TelosClient
from builder_runtime.workspace import (
    append_workspace_file,
    create_workspace_dir,
    delete_workspace_file,
    glob_workspace,
    grep_workspace,
    list_workspace,
    read_workspace_file,
    run_python,
    runtime_info,
    workspace_root,
    write_workspace_file,
)

log = logging.getLogger(__name__)


def _search_quality_hint(hits: list[dict]) -> str:
    """Return a short hint about result quality to help the LLM decide whether to write."""
    if not hits:
        return "No results found. This topic may be unexplored in Telos."
    scores = [hit.get("score", 0) for hit in hits]
    top_score = max(scores) if scores else 0
    if top_score > 0.85:
        return "High similarity results found. Check carefully for duplicates before writing."
    if top_score > 0.7:
        return "Moderately related results. There may be room to extend or challenge existing knowledge."
    return "Weakly related results. This area may benefit from fresh exploration if you have genuine insight."


def build_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    td = cfg["tool_descriptions"]
    return [
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
                        "kind": {"type": "string"},
                        "scope_kind": {"type": "string"},
                        "scope_id": {"type": "string"},
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
                        "kind": {"type": "string"},
                        "scope_kind": {"type": "string"},
                        "scope_id": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "telos_pass",
                "description": str(td["telos_pass"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Why this loop does not warrant a write."},
                    },
                    "required": ["reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "telos_reflect",
                "description": str(td["telos_reflect"]),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of recent entries to retrieve (default 5)."},
                    },
                    "required": [],
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


def run_tools(
    telos: TelosClient,
    cfg: dict[str, Any],
    name: str,
    arguments: str,
) -> str:
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON arguments: {exc}"}, ensure_ascii=False)

    allowed_hosts = cfg["fetch_allowed_hosts"]
    allow = [str(host) for host in allowed_hosts] if allowed_hosts else None

    if name == "telos_search":
        query = args.get("query", "")
        default_limit = int(cfg["default_search_limit"])
        max_limit = int(cfg["max_search_limit"])
        limit = int(args.get("limit", default_limit))
        limit = max(1, min(limit, max_limit))
        hits = telos.search(
            str(query),
            limit,
            kind=str(args["kind"]) if args.get("kind") is not None else None,
            scope_kind=str(args["scope_kind"]) if args.get("scope_kind") is not None else None,
            scope_id=str(args["scope_id"]) if args.get("scope_id") is not None else None,
        )
        result = {
            "results": hits,
            "meta": {
                "result_count": len(hits),
                "top_score": hits[0]["score"] if hits else None,
                "hint": _search_quality_hint(hits),
            },
        }
        return json.dumps(result, ensure_ascii=False)

    if name == "telos_write":
        content = str(args.get("content", ""))
        parent_ids = args.get("parent_ids")
        if not isinstance(parent_ids, list):
            parent_ids = []
        parent_ids = [str(value) for value in parent_ids]
        raw_metadata = args.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else None
        node_id = telos.write(
            content,
            parent_ids,
            kind=str(args["kind"]) if args.get("kind") is not None else None,
            scope_kind=str(args["scope_kind"]) if args.get("scope_kind") is not None else None,
            scope_id=str(args["scope_id"]) if args.get("scope_id") is not None else None,
            metadata=metadata,
        )
        return json.dumps({"id": node_id, "ok": node_id is not None}, ensure_ascii=False)

    if name == "telos_pass":
        reason = str(args.get("reason", ""))
        log.info("telos_pass: %s", reason[:300])
        return json.dumps({"ok": True, "action": "pass", "reason": reason[:300]}, ensure_ascii=False)

    if name == "telos_reflect":
        default_limit = int(cfg["default_search_limit"])
        limit = int(args.get("limit", 5))
        limit = max(1, min(limit, default_limit))
        hits = telos.reflect(limit)
        return json.dumps({"recent_writes": hits, "count": len(hits)}, ensure_ascii=False)

    if name == "telos_stats":
        return json.dumps(telos.stats_nodes(), ensure_ascii=False)

    if name == "http_get":
        url = str(args.get("url", ""))
        return json.dumps(execute_http_request(url, "GET", cfg, allow), ensure_ascii=False)

    if name == "http_request":
        url = str(args.get("url", ""))
        method = str(args.get("method", "GET"))
        json_body = args.get("json", None)
        body_raw = args.get("body", None)
        headers = args.get("headers")
        if json_body is not None and body_raw is not None:
            return json.dumps({"error": "use only one of json or body"}, ensure_ascii=False)
        text_body = str(body_raw) if body_raw is not None else None
        return json.dumps(
            execute_http_request(
                url,
                method,
                cfg,
                allow,
                json_body=json_body,
                text_body=text_body,
                headers_raw=headers,
            ),
            ensure_ascii=False,
        )

    workspace = workspace_root(cfg)

    if name == "runtime_info":
        return json.dumps(runtime_info(workspace), ensure_ascii=False)

    if name == "create_workspace_dir":
        return json.dumps(create_workspace_dir(workspace, str(args.get("path", ""))), ensure_ascii=False)

    if name == "workspace_glob":
        return json.dumps(
            glob_workspace(
                workspace,
                cfg,
                str(args.get("glob_pattern", "")),
                str(args.get("path", "")),
            ),
            ensure_ascii=False,
        )

    if name == "grep_workspace":
        return grep_workspace(
            workspace,
            cfg,
            str(args.get("pattern", "")),
            str(args.get("path", "")),
            int(args["max_matches"]) if args.get("max_matches") is not None else None,
            bool(args.get("ignore_case", False)),
        )

    if name == "read_workspace_file":
        return json.dumps(
            read_workspace_file(workspace, cfg, str(args.get("path", ""))),
            ensure_ascii=False,
        )

    if name == "write_workspace_file":
        return json.dumps(
            write_workspace_file(
                workspace,
                cfg,
                str(args.get("path", "")),
                str(args.get("content", "")),
            ),
            ensure_ascii=False,
        )

    if name == "list_workspace":
        return json.dumps(
            list_workspace(
                workspace,
                cfg,
                str(args.get("path", "")),
                int(args["max_depth"]) if args.get("max_depth") is not None else None,
                int(args["max_entries"]) if args.get("max_entries") is not None else None,
            ),
            ensure_ascii=False,
        )

    if name == "append_workspace_file":
        return json.dumps(
            append_workspace_file(
                workspace,
                cfg,
                str(args.get("path", "")),
                str(args.get("content", "")),
            ),
            ensure_ascii=False,
        )

    if name == "delete_workspace_file":
        return json.dumps(delete_workspace_file(workspace, str(args.get("path", ""))), ensure_ascii=False)

    if name == "run_python":
        stdin_raw = args.get("stdin", None)
        stdin = str(stdin_raw) if stdin_raw is not None else None
        return json.dumps(
            run_python(
                workspace,
                cfg,
                str(args.get("code", "")),
                stdin,
            ),
            ensure_ascii=False,
        )

    return json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False)
