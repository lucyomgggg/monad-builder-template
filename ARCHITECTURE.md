# monad-builder-template — Architecture

This document describes how the builder-class monad is structured so you can navigate `monad.py` and `config.yaml` confidently.

---

## 1. Purpose

You want a long-running agent that:

- Reads and writes **Telos** (semantic search + append, plus optional stats).
- Calls **HTTP** APIs (GET or arbitrary methods with body/headers).
- Uses a local **workspace** for drafts, scripts, and grep/glob exploration.
- Runs **Python** in that workspace (`run_python`).
- Optionally creates **Stripe Checkout** sessions for fixed, config-approved Price IDs only.

Secrets (LLM keys, `STRIPE_SECRET_KEY`) stay in the **environment** or `.env`. Everything else is in **`config.yaml`**, reloaded each iteration.

---

## 2. Control flow (high level)

1. **`main()`** — Loads config, runs **`run_once()`**, sleeps **`interval_sec`**, repeats. Reloads YAML every cycle.
2. **`run_once()`** — Validates config, opens **`TelosClient`**, seeds chat with `system_prompt` + `task`, runs **`agent_turn()`** (LLM + tools up to **`max_tool_rounds`**), closes the client.
3. **`agent_turn()`** — LiteLLM **`completion`** with OpenAI-style tools; dispatches tool calls to **`run_tools()`**.

Stripe: **`build_tools()`** only adds `stripe_create_checkout_session` when **`stripe_checkout.enabled`** is true. **`validate_config()`** enforces URLs, price allowlist, and env key when enabled.

---

## 3. Config validation

**`_REQUIRED_KEYS`** lists scalar and block keys that must exist (workspace limits, grep caps, HTTP limits, etc.). **`tool_descriptions`** must contain a non-empty string for every tool in **`_TOOL_DESC_KEYS`**, except **`stripe_create_checkout_session`** when Stripe is disabled.

---

## 4. Telos mapping

The client uses **`telos_base_url`** and **`monad_id`** (for tagging / headers as implemented in code). Search and write map to Telos Core HTTP routes defined in `monad.py` (`TelosClient`).

---

## 5. HTTP allowlist

**`fetch_allowed_hosts`** — empty list means any host (useful for dev); non-empty restricts `http_get` / `http_request` to those hosts (after normalization in code).

---

## 6. Extending

- Add tools in **`build_tools()`** and **`run_tools()`**, and document them in **`tool_descriptions`** in YAML.
- Prefer caps in **`config.yaml`** over hard-coded limits so forks can tune behavior without editing Python.

For a minimal Telos-only monad without workspace or `run_python`, see **`monad-template`** in the same monorepo pattern.
