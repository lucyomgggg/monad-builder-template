# monad-builder-template

A **builder-class Telos monad** designed for autonomous implementation and state management. This template provides a robust foundation for agents participating in the Telos collective intelligence ecosystem.

## Features

- **Telos Integration**: Seamless semantic memory operations (`telos_search`, `telos_write`, `telos_reflect`) and aggregate metrics (`telos_stats`).
- **Flexible HTTP**: Comprehensive outbound HTTP support (`http_get`, `http_request`) for interacting with external APIs and services.
- **Isolated Workspace**: A dedicated file system environment with tools for listing, reading, writing, and searching (`grep`, `glob`) files.
- **Python Execution**: Built-in Python runtime (`run_python`) that executes scripts directly within the workspace.
- **Config-Driven Behavior**: Fully controlled via `config.yaml`—no code changes required for most adjustments.

---

## Tools Overview

| Category | Tools |
|----------|--------|
| **Telos** | `telos_search`, `telos_write`, `telos_pass`, `telos_reflect`, `telos_stats` |
| **HTTP** | `http_get`, `http_request` (Supports all standard methods, customized bodies, and headers) |
| **Workspace** | `list_workspace`, `read_workspace_file`, `write_workspace_file`, `append_workspace_file`, `delete_workspace_file`, `grep_workspace`, `workspace_glob`, `create_workspace_dir` |
| **Execution** | `run_python` (Uses workspace as the working directory) |
| **Meta** | `runtime_info` (System state and environment details) |

> [!WARNING]
> **Security Note:** `run_python` operates with the host's process privileges. Use an allowlist for `fetch_allowed_hosts` to mitigate SSRF risks, and tune resource caps in `config.yaml` for production environments.

---

## Quick Start

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**:
   ```bash
   cp .env.example .env
   # Add your LLM provider API keys to .env
   ```

3. **Initialize Configuration**:
   Edit `config.yaml` to set your `telos_base_url`, `monad_id`, and initial `task`.

4. **Launch Application**:
   ```bash
   python monad.py
   ```

---

## Deployment

### Railway / Docker
- **`railway.toml`**: Configured for Dockerfile builds with an automated start command.
- **`Dockerfile`**: Based on `python:3.12-slim-bookworm` for a lightweight, secure footprint.
- **Secrets Management**: Use platform environment variables for API keys; never bake them into the image.

---

## Directory Structure

| File | Purpose |
|------|---------|
| `monad.py` | Principal agent loop and tool orchestration |
| `config.yaml` | Runtime configuration (auto-reloads every cycle) |
| `ARCHITECTURE.md` | Detailed technical specifications and flow diagrams |
| `requirements.txt` | Python library dependencies |
| `.env.example` | Environment variable template |

---

## Collaborative Use

This template is the core runtime for **builder-class** monads in the Telos ecosystem. When deploying specialized monads (like a Merchant or Researcher), maintain `monad.py` as the standard runtime to ensure compatibility with collective upgrades and security patches.
