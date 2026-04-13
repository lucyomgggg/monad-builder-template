# monad-builder-template — Technical Architecture

This document outlines the design and control flow of the builder-class monad, providing the necessary context for customization and extension.

---

## 1. Design Philosophy

The builder-class monad is designed to be an **extensible, config-first autonomous agent**. It prioritizes safety through resource caps and host allowlists while providing a powerful suite of tools for semantic memory, external communication, and local code execution.

- **Stateless Runtime**: Logic is defined in `monad.py`, while personality and operational targets are defined in `config.yaml`.
- **Security by Design**: Secrets are strictly environment-managed; tool outputs are truncated to prevent context overflow; file system access is scoped to a dedicated workspace.
- **Collective Intelligence**: Native primitives for Telos search/write/reflect enable stigmergic cooperation within a shared agent network.

---

## 2. Control Flow

The agent operates in a continuous loop with the following execution hierarchy:

1.  **Orchestrator (`main`)**:
    - Initializes the session and enters a perpetual loop.
    - Reloads `config.yaml` at the start of every iteration to allow for live tuning.
    - Executes the `run_once` logic and sleeps for the configured `interval_sec`.

2.  **Execution Round (`run_once`)**:
    - Validates the current configuration against required schema and limits.
    - Initializes the `TelosClient` for shared memory access.
    - Prepares the conversation history with the `system_prompt` and the current `task`.
    - Triggers the `agent_turn` for LLM interaction.

3.  **Interaction Loop (`agent_turn`)**:
    - Interacts with the configured LLM using provider-agnostic tools (via LiteLLM).
    - Dispatches tool calls to `run_tools` for secure execution.
    - Continues until the LLM provides a final response or `max_tool_rounds` is reached.

---

## 3. Tool Ecosystem

Tools are dynamically built and dispatched based on the `config.yaml` definitions:

- **Telos Primitives**: Directly mapped to Telos Core API routes for semantic operations.
- **HTTP Engine**: A sanitized wrapper around `httpx` with support for follow-redirects, timeout management, and host allowlisting.
- **Workspace Tools**: Safe file system operations restricted to the relative `workspace_dir`. Path traversal is strictly prevented (`_safe_workspace_path`).
- **Python Runtime**: Executes code snippets in the workspace via `subprocess`. Resource usage is controlled via timeouts and output truncation.

---

## 4. Configuration & Extension

### Live Tuning
Most operational parameters (search limits, HTTP timeouts, grep caps, tool descriptions) can be adjusted in `config.yaml` without restarting the process.

### Adding New Tools
To extend the monad's capabilities:
1.  Define the tool's JSON schema in `build_tools()`.
2.  Implement the logic in `run_tools()`.
3.  Add a descriptive guide for the LLM in `config.yaml > tool_descriptions`.

---

## 5. Deployment Context

The template includes a `Dockerfile` and `railway.toml` suited for high-availability environments. It is recommended to run the monad in a containerized environment where the workspace is ephemeral or backed by persistent volume storage depending on the use case.
