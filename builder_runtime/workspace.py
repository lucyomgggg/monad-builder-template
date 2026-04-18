from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from builder_runtime.config import CONFIG_DIR
from builder_runtime.utils import truncate


def workspace_root(cfg: dict[str, Any]) -> Path:
    rel = str(cfg["workspace_dir"]).strip() or "workspace"
    root = (CONFIG_DIR / rel).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_workspace_path(workspace: Path, relative: str) -> Path | None:
    rel = relative.strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    candidate = (workspace / rel).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate


def workspace_base_path(workspace: Path, relative: str) -> Path | None:
    rel = relative.strip().replace("\\", "/")
    if rel in ("", "."):
        return workspace
    return safe_workspace_path(workspace, rel)


def runtime_info(workspace: Path) -> dict[str, Any]:
    return {
        "utc_iso": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version.split()[0],
        "full_python_version": sys.version,
        "platform": sys.platform,
        "workspace_resolved": str(workspace.resolve()),
    }


def create_workspace_dir(workspace: Path, relative: str) -> dict[str, Any]:
    path = safe_workspace_path(workspace, relative)
    if path is None:
        return {"error": "invalid path"}
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"error": str(exc)}
    return {"ok": True, "path": relative}


def glob_workspace(workspace: Path, cfg: dict[str, Any], glob_pattern: str, base_rel: str) -> dict[str, Any]:
    if not glob_pattern.strip() or ".." in glob_pattern or glob_pattern.startswith(("/", "\\")):
        return {"error": "invalid glob_pattern"}
    base = workspace_base_path(workspace, base_rel)
    if base is None:
        return {"error": "invalid path"}
    if not base.exists():
        return {"error": "path does not exist"}
    if not base.is_dir():
        return {"error": "path must be a directory for workspace_glob"}
    max_files = int(cfg["workspace_glob_max_files"])
    paths: list[str] = []
    truncated = False
    try:
        for path in sorted(base.glob(glob_pattern)):
            if path.is_dir():
                continue
            try:
                path.resolve().relative_to(workspace.resolve())
            except ValueError:
                continue
            rel_path = str(path.relative_to(workspace)).replace("\\", "/")
            paths.append(rel_path)
            if len(paths) >= max_files:
                truncated = True
                break
    except OSError as exc:
        return {"error": str(exc)}
    return {"paths": paths, "truncated": truncated}


def grep_workspace(
    workspace: Path,
    cfg: dict[str, Any],
    pattern: str,
    base_rel: str,
    max_matches_arg: int | None,
    ignore_case: bool,
) -> str:
    max_pattern = int(cfg["grep_workspace_max_pattern_chars"])
    if len(pattern) > max_pattern:
        return json.dumps({"error": f"pattern exceeds grep_workspace_max_pattern_chars ({max_pattern})"}, ensure_ascii=False)
    flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return json.dumps({"error": f"regex error: {exc}"}, ensure_ascii=False)

    base = workspace_base_path(workspace, base_rel)
    if base is None:
        return json.dumps({"error": "invalid path"}, ensure_ascii=False)
    if not base.exists():
        return json.dumps({"error": "path does not exist"}, ensure_ascii=False)

    max_matches = int(max_matches_arg or cfg["grep_workspace_max_matches"])
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
    except OSError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    for path in iter_paths:
        if len(matches) >= max_matches:
            truncated_matches = True
            break
        if not path.is_file():
            continue
        if path.name.startswith("_snippet_") and path.suffix == ".py":
            continue
        files_scanned += 1
        if files_scanned > max_scan:
            truncated_scan = True
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_file:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        rel_path = str(path.relative_to(workspace)).replace("\\", "/")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if len(matches) >= max_matches:
                truncated_matches = True
                break
            if regex.search(line):
                matches.append({"path": rel_path, "line": line_number, "text": line[:500]})
        if truncated_matches:
            break
    output: dict[str, Any] = {
        "matches": matches,
        "files_scanned": files_scanned,
        "truncated_matches": truncated_matches,
        "truncated_scan": truncated_scan,
    }
    while True:
        serialized = json.dumps(output, ensure_ascii=False)
        if len(serialized) <= max_out:
            return serialized
        if not output["matches"]:
            return truncate(serialized, max_out)
        output["matches"].pop()
        output["truncated_output"] = True


def read_workspace_file(workspace: Path, cfg: dict[str, Any], relative: str) -> dict[str, Any]:
    max_chars = int(cfg["read_workspace_max_chars"])
    path = safe_workspace_path(workspace, relative)
    if path is None:
        return {"error": "invalid path"}
    if not path.is_file():
        return {"error": "not a file or missing"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": str(exc)}
    return {"path": relative, "content": truncate(text, max_chars)}


def write_workspace_file(workspace: Path, cfg: dict[str, Any], relative: str, content: str) -> dict[str, Any]:
    max_write = int(cfg["write_workspace_max_chars"])
    if len(content) > max_write:
        return {"error": f"content exceeds write_workspace_max_chars ({max_write})"}
    path = safe_workspace_path(workspace, relative)
    if path is None:
        return {"error": "invalid path"}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": str(exc)}
    return {"ok": True, "path": relative, "bytes": len(content.encode("utf-8"))}


def list_workspace(
    workspace: Path,
    cfg: dict[str, Any],
    relative: str,
    max_depth_arg: int | None,
    max_entries_arg: int | None,
) -> dict[str, Any]:
    max_depth_cfg = int(cfg["list_workspace_max_depth"])
    max_entries_cfg = int(cfg["list_workspace_max_entries"])
    default_depth = int(cfg["list_workspace_default_depth"])
    depth = int(max_depth_arg if max_depth_arg is not None else default_depth)
    entries_limit = int(max_entries_arg if max_entries_arg is not None else max_entries_cfg)
    depth = max(0, min(depth, max_depth_cfg))
    entries_limit = max(1, min(entries_limit, max_entries_cfg))
    base = workspace_base_path(workspace, relative)
    if base is None:
        return {"error": "invalid path"}
    if not base.exists():
        return {"error": "path does not exist"}
    if base.is_file():
        try:
            size = base.stat().st_size
        except OSError as exc:
            return {"error": str(exc)}
        rel_path = str(base.relative_to(workspace)).replace("\\", "/")
        return {"entries": [{"path": rel_path, "kind": "file", "size": size}], "truncated": False}
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        for path in sorted(base.rglob("*")):
            if len(entries) >= entries_limit:
                truncated = True
                break
            rel_to_base = path.relative_to(base)
            entry_depth = len(rel_to_base.parts) - 1
            if entry_depth > depth:
                continue
            rel_path = str(path.relative_to(workspace)).replace("\\", "/")
            if path.is_dir():
                entries.append({"path": rel_path, "kind": "dir"})
            else:
                try:
                    size = path.stat().st_size
                except OSError:
                    size = None
                entries.append({"path": rel_path, "kind": "file", "size": size})
    except OSError as exc:
        return {"error": str(exc)}
    return {"entries": entries, "truncated": truncated}


def append_workspace_file(workspace: Path, cfg: dict[str, Any], relative: str, content: str) -> dict[str, Any]:
    max_write = int(cfg["write_workspace_max_chars"])
    if len(content) > max_write:
        return {"error": f"content exceeds write_workspace_max_chars ({max_write})"}
    path = safe_workspace_path(workspace, relative)
    if path is None:
        return {"error": "invalid path"}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        return {"error": str(exc)}
    return {"ok": True, "path": relative, "appended_bytes": len(content.encode("utf-8"))}


def delete_workspace_file(workspace: Path, relative: str) -> dict[str, Any]:
    path = safe_workspace_path(workspace, relative)
    if path is None:
        return {"error": "invalid path"}
    if not path.is_file():
        return {"error": "not a file or missing"}
    try:
        path.unlink()
    except OSError as exc:
        return {"error": str(exc)}
    return {"ok": True, "path": relative}


def run_python(workspace: Path, cfg: dict[str, Any], code: str, stdin: str | None) -> dict[str, Any]:
    max_code = int(cfg["run_python_max_code_chars"])
    if len(code) > max_code:
        return {"error": f"code exceeds run_python_max_code_chars ({max_code})"}
    if stdin is not None:
        limit = int(cfg["run_python_max_stdin_chars"])
        if len(stdin) > limit:
            return {"error": f"stdin exceeds run_python_max_stdin_chars ({limit})"}
    timeout = float(cfg["run_python_timeout_sec"])
    max_output = int(cfg["run_python_max_output_chars"])
    snippet = workspace / f"_snippet_{uuid.uuid4().hex}.py"
    try:
        snippet.write_text(code, encoding="utf-8")
        process = subprocess.run(
            [sys.executable, str(snippet)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        return {
            "returncode": process.returncode,
            "stdout": truncate(process.stdout or "", max_output),
            "stderr": truncate(process.stderr or "", max_output),
            "snippet": snippet.name,
        }
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "timeout_sec": timeout}
    except OSError as exc:
        return {"error": str(exc)}
    finally:
        if not bool(cfg["keep_snippet_files"]):
            try:
                snippet.unlink(missing_ok=True)
            except OSError:
                pass
