# monad-builder-template

A **builder-class Telos monad**: shared memory (`telos_search`, `telos_write`, `telos_stats`), outbound HTTP (`http_get`, `http_request`), a full **workspace** (list/read/write/append/delete, `grep_workspace`, `workspace_glob`, `create_workspace_dir`), **`run_python`** with the workspace as cwd, **`runtime_info`**, and optionally **Stripe Checkout** for pre-approved Price IDs only (the secret key never leaves the host).

Fork this repository, set `config.yaml` and environment variables, and run `python monad.py`. Behavior is entirely config-driven.

---

## Tools (summary)

| Category | Tools |
|----------|--------|
| Telos | `telos_search`, `telos_write`, `telos_stats` |
| HTTP | `http_get`, `http_request` (GET/POST/PUT/PATCH/DELETE; JSON or raw body; optional headers; `Host` ignored) |
| Workspace | list/read/write/append/delete, `grep_workspace`, `workspace_glob`, `create_workspace_dir` |
| Execution | `run_python` |
| Meta | `runtime_info` |
| Revenue (optional) | `stripe_create_checkout_session` — only if `stripe_checkout.enabled: true` in `config.yaml` |

**Safety:** `run_python` runs with the process/container privileges. An empty `fetch_allowed_hosts` list allows HTTP to any host (SSRF-class risk). For production, set an allowlist and tune caps in `config.yaml` (`grep_workspace_*`, `workspace_glob_max_files`, `http_request_max_body_chars`, etc.).

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env   # set keys for your llm_model (and STRIPE_SECRET_KEY if Stripe is enabled)
# Edit config.yaml: telos_base_url, monad_id, task, system_prompt, limits
python monad.py
```

On a **shared** Telos instance, use a **unique** `monad_id` so your writes are identifiable.

---

## Model and providers

`llm_model` is any [LiteLLM](https://docs.litellm.ai/docs/providers) model string (e.g. `openai/gpt-4o-mini`, `openrouter/anthropic/claude-sonnet-4.5`). Set the matching API key in `.env` or the environment. `monad.py` loads `.env` from this directory with `override=False` (existing shell variables win).

---

## Stripe Checkout (optional)

1. Add a `stripe_checkout` block in `config.yaml` with `enabled: true`, valid `success_url` / `cancel_url`, non-empty `allowed_price_ids` (Stripe Price IDs, `price_...`), and `tool_descriptions.stripe_create_checkout_session`.
2. Set `STRIPE_SECRET_KEY` in the environment (never in YAML).

When `enabled` is false, the Stripe tool is omitted and its description in YAML is not required.

---

## Railway / Docker

- **`railway.toml`** — Dockerfile build, start command `python monad.py`.
- **`Dockerfile`** — `python:3.12-slim-bookworm`, copies the repo and runs the monad.
- Point the Railway service root at **this directory** (or the repo root if this is the only app).
- Do not bake API keys into the image; use Railway Variables or your platform’s secret store.

The image does not include `git` or `curl` by default; outbound work uses **httpx** and **Python** via the tools above.

---

## Files

| File | Role |
|------|------|
| `monad.py` | LLM loop, Telos client, tool dispatch |
| `config.yaml` | Non-secret runtime settings (reloaded each iteration) |
| `ARCHITECTURE.md` | Control flow and extension notes |
| `requirements.txt` | Python dependencies |
| `.env.example` | Reminder for API keys |

---

## Relationship to Telos project monads

In the Telos `monads/` monorepo layout, **the-builder-monad** and **the-merchant-monad** deploy from their own folders but should keep **`monad.py` identical** to this template. After changing the runtime here, copy `monad.py` into those folders (or use a git subtree) so they stay in sync.
