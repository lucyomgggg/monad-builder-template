from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = CONFIG_DIR / "config.yaml"
load_dotenv(CONFIG_DIR / ".env", override=False)

CORE_SEARCH_LIMIT_MAX = 20

REQUIRED_KEYS = (
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

TOOL_DESC_KEYS = (
    "telos_search",
    "telos_write",
    "telos_pass",
    "telos_reflect",
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
)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        log.error("config.yaml not found: %s", CONFIG_PATH)
        sys.exit(1)
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        log.error("config.yaml parse error: %s", exc)
        sys.exit(1)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        log.error("config.yaml must be a mapping at the top level")
        sys.exit(1)
    return raw


def validate_config(cfg: dict[str, Any]) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in cfg]
    if missing:
        log.error("config.yaml missing required keys: %s", missing)
        sys.exit(1)

    tool_descriptions = cfg["tool_descriptions"]
    if not isinstance(tool_descriptions, dict):
        log.error("tool_descriptions must be a mapping")
        sys.exit(1)
    for key in TOOL_DESC_KEYS:
        if key not in tool_descriptions or not str(tool_descriptions[key]).strip():
            log.error("tool_descriptions.%s is empty", key)
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

    workspace_dir = str(cfg["workspace_dir"]).strip()
    if not workspace_dir or ".." in workspace_dir or Path(workspace_dir).is_absolute():
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
    except (TypeError, ValueError) as exc:
        log.error("invalid numeric field: %s", exc)
        sys.exit(1)

    if int(cfg["max_search_limit"]) > CORE_SEARCH_LIMIT_MAX:
        log.error("max_search_limit must be <= %s", CORE_SEARCH_LIMIT_MAX)
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

    tool_choice = cfg.get("tool_choice", "auto")
    if isinstance(tool_choice, str) and not str(tool_choice).strip():
        log.error("tool_choice must not be empty when set as string")
        sys.exit(1)
    if not isinstance(tool_choice, (str, dict)):
        log.error("tool_choice must be a string (e.g. auto, required) or an OpenAI-style object")
        sys.exit(1)

    if "parallel_tool_calls" in cfg and not isinstance(cfg["parallel_tool_calls"], bool):
        log.error("parallel_tool_calls must be a boolean when set")
        sys.exit(1)
